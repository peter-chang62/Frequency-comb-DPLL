# PLL firmware architecture: files, hierarchy, and code walkthrough

This document explains how the digital phase-locked loop is implemented in
`Firmware_Vivado_Project/redpitaya.srcs/sources_1/`, file by file, for anyone
auditing or modifying register addressing, scaling, or startup sequencing.

## Module hierarchy

```
red_pitaya_top.v (board top-level)
 ├─ red_pitaya_ps (Zynq PS, generates sys_addr/sys_wdata/... bus)
 ├─ red_pitaya_pll (MMCM: generates adc_clk / adc_clk_2x)
 ├─ digital_clock_freq_counter ×2 (external-clock and ext-vs-ADC-clock measurement)
 ├─ 8-way sys-bus fan-out (sys_addr[22:20] one-hot decode → sys_cs)
 │    0: dpll_wrapper.v        <- the DPLL/DDC/loop-filter subsystem
 │    1: ram_data_logger
 │    2: addr_packed.vhd       <- generic register write-cache/readback CAM
 │    3: red_pitaya_hk, 4: red_pitaya_ams, 5/6: mux_internal_vco, 7: unused (tied ack)
 ├─ duty_cycle_protector.vhd (HV-switcher watchdog on osc_output -> exp_n_out[2])
 └─ dpll_wrapper.v
     ├─ DDC_wideband_filters ×2                (phase detector, one per ADC channel)
     │   ├─ LO_DDS                             (NCO, reference cos/sin)
     │   ├─ first_order_IIR_highpass_filter
     │   ├─ input_multiplier ×2                (I/Q mixers)
     │   ├─ ddc_frontend_lowpass_filter ×2      (boxcar/FIR chains, mode-selectable)
     │   │   ├─ boxcar_4_pts_filter, boxcar_2_pts_filter ×2
     │   │   ├─ adjustable_boxcar_filter_v2
     │   │   └─ N_times_clk_FIR_wrapper -> ddc_minimum_phase_fir
     │   ├─ angle_CORDIC                       (rectangular -> polar)
     │   ├─ quantizer / limiter ×2             (alternate phase-detector outputs)
     │   └─ phase-unwrap + inst.-frequency differentiator (in-line, with a
     │       "trickle the lock-offset to zero" FSM)
     ├─ dual_type_frequency_counter ×2         (zero-dead-time freq counters, logging)
     │   └─ triangular_frequency_counter ×2 each
     ├─ PLL_loop_filters_with_saturation ×2    (the live PII²D loop filter, DAC0/DAC1)
     │   ├─ resize_with_saturation ×4
     │   ├─ integrator_with_saturation
     │   └─ IIR_LPF
     ├─ acquisition_FLL ×2 + dac2_error_computation ×2 (slow "unload DAC1 onto DAC2")
     ├─ output_summing ×5                      (sum + saturate to DAC rail limits)
     ├─ system_identification_vna_with_dither_wrapper
     │   └─ system_identification_with_dither2.vhd (swept-sine VNA + dither mode)
     │       └─ system_identification_macc_behav ×2
     ├─ dither_lockin_wrapper ×2               (in-loop lock-in amplifier)
     │   ├─ quadrature_dither_generator
     │   └─ dither_lockin
     ├─ multiplexer_3to1_async                 (PLL1 input source selector)
     └─ registers_read.vhd                     (legacy status/counter readback, 0x0025-0x0043)
```

`PLL_loop_filters.vhd` (non-saturating) and `system_identification.vhd`
(non-dither VNA) both still exist on disk but are **not instantiated
anywhere** in `dpll_wrapper.v` — confirmed superseded by the `_with_saturation`
/ `_with_dither2` versions above (consistent with the "superseded modules"
note in the repo's `CLAUDE.md`).

## 1. Phase detector — `DigitalPLL/DDC_wideband_filters.vhd`

The actual phase/frequency comparator for the beat note. Signal chain: raw
ADC sample -> high-pass (removes DC) -> mixed against an NCO-generated
cos/sin reference (`LO_DDS`, phase increment set by a runtime 48-bit
`reference_frequency` register) -> I and Q each low-pass filtered
(`ddc_frontend_lowpass_filter`) -> CORDIC converts I/Q to amplitude+phase ->
phase is differentiated sample-to-sample to get instantaneous frequency
(this is what feeds the loop filter — the PLL works on frequency error, not
raw phase, directly).

A special case forces a pure real "DC" reference (`cos=1, sin=0`) whenever
`reference_frequency = 0`, turning the DDC into a no-op mixer for a baseband
lock mode; the front-end high-pass filter is bypassed in this mode too, since
you don't want to high-pass the very signal you're trying to measure.

Lock-on offset handling (glitch-free "lock at zero phase error"): rather than
snapping to the new phase reference the instant `lock` is asserted (which
would spike the frequency-error term by the whole accumulated offset), the
module remembers the phase offset at the moment of lock and trickles it out
one LSB per clock cycle into the frequency-error stream:

```vhdl
if lock = '1' and lock_last = '0' then
    -- we just turned on the lock: remember the offset:
    phase_offset     <= signed(wrapped_phase_internal_last2);
    inst_freq_adjust <= (others => '0');
else
    -- trickle the offsets to the inst freq:
    if phase_offset > 0 then
        phase_offset     <= phase_offset - 1;
        inst_freq_adjust <= to_signed(1, ...);
    ...
```

`angleSelect` lets software route the CORDIC's raw phase, or a
quantized/limited I or Q value instead, into this differentiator — supporting
a "linearized" phase-detector mode for weak or non-sinusoidal beat signals.

Filter mode (`ddc_filter_select`, in `ddc_frontend_lowpass_filter.vhd`) is
selectable per channel:
- `00`: wideband boxcar chain, coefficients `[1 3 4 4 3 1]` — chosen because
  their zeros land exactly at Nyquist, which is where the image tone from
  mixing a 25 MHz input aliases to.
- `01`: narrowband adjustable 16-tap boxcar.
- `10`: 2-boxcar convolved with a 16-tap minimum-phase FIR, run at N× clock
  (`N_times_clk_FIR_wrapper`) to save DSP slices instead of a dedicated
  single-rate FIR core.

## 2. Loop filter — `DigitalPLL/PLL_loop_filters_with_saturation.vhd`

The live PII²D controller (proportional + integral + double-integral +
filtered derivative), one instance per DAC (`PLL0_loop_filters`,
`PLL1_loop_filters`). From its header comment:

```
data_out = gain_p/2^N_DIVIDE_P*data_in
         + gain_i/2^N_DIVIDE_I * cumsum(data_in)
         + gain_ii/2^N_DIVIDE_II * cumsum(cumsum(data_in))
```

The phase-error accumulator (the integral of the frequency-error input, feeding
both I and I² branches) has **no anti-windup**, by explicit design:

```vhdl
-- this is by design because having anti-windup here would mean not
-- integrating part of the error signal
```

The I² branch's own second accumulator (`integrator_with_saturation`) *does*
have anti-windup, fed back from the final output stage's rail-saturation
flags. `gain_changed OR NOT lock` synchronously clears the entire filter
state, giving bumpless gain changes and a clean re-acquisition on unlock.

Generics differ per channel, with the tuning rationale left in-line:

```verilog
PLL_loop_filters_with_saturation # (
    .N_DIVIDE_P(24-11),  // changed 2017-05-02 by JDD from 24 to 24-11 to
                         // recenter gain for RedPitaya connected to a laser
                         // with 8e8 Hz/V of VCO gain and 20 kHz of 1st order cutoff
    .N_DIVIDE_I(24), .N_DIVIDE_II(35), .N_DIVIDE_D(0), .N_OUTPUT(16)
) PLL0_loop_filters ( ... );
```

The D branch was disabled and later re-enabled as Zynq resource budget
allowed:

```vhdl
-- JDD 12-08-2016: disabled D branch to try to make it fit into the smallest Zynq (Red Pitaya)
-- JDD 15-06-2017: re-enabled D branch since there was enough DSP slices
```

## 3. DAC2 "unload" loop and output summing

DAC2 isn't driven by a phase-locked loop directly. Two `acquisition_FLL`
integrators sum onto it: one integrates the (possibly sign-flipped)
instantaneous frequency of DDC1, the other integrates DAC1's offset from the
midpoint of its rail limits (`dac2_error_computation`). This is a slow
"unloading" loop — DAC2 (a wider-range, slower actuator, e.g. thermal/piezo)
is nudged to keep DAC1 (a faster, narrower-range actuator) centered, so DAC1
doesn't rail over long timescales.

`output_summing` (×5, one set per DAC plus dither/VNA sums) is a 4-input
signed adder with `GUARD_BITS=6` of headroom before saturating against
runtime-programmable `positive_limit`/`negative_limit` registers — this is
what enforces the DAC output-rail limits and produces the "railed" status
bits consumed by the LED driver and residuals-monitor logic.

## 4. Dither lock-in — `quadrature_dither_generator.vhd`, `dither_lockin.vhd`, `dither_lockin_wrapper.vhd`

An in-loop lock-in amplifier used to continuously measure the DC gain and
sign of the actuator path. `quadrature_dither_generator` produces a
quadrature square-wave dither with sync flags; `dither_lockin` delays those
sync flags by `SYNC_DELAY` cycles (to compensate for the loop's
output→input latency) and demodulates `data_input` into `result_I`/`result_Q`
accumulators. `dither_lockin_wrapper` exposes 4 registers (enable, modulation
period, integration length, amplitude) and is instantiated twice in
`dpll_wrapper.v`, at `BASE_ADDRESS=0x8100` (DAC0) and `0x8200` (DAC1), both
with `SYNC_DELAY=60`.

## 5. VNA / system-ID — `DigitalPLL/system_identification_with_dither2.vhd`

A full swept-sine vector network analyzer plus a single-tone "dither" mode,
FSM-driven. Output frequency formula (from its header):

```
f = (first_modulation_frequency + k*modulation_frequency_step) * fs/2^FREQUENCY_WIDTH
```

Correlates `data_in` against the NCO's cos/sin (`system_identification_macc_behav`
×2) to build up real/imaginary transfer-function estimates per frequency
step. Zero-crossing detection aligns the start/stop of each frequency step's
integration window to whole numbers of modulation periods — the header notes
this matters a lot at low frequencies, where fractional-period integration
otherwise leaks energy into the wrong bins. The FSM inserts a fixed 4-cycle
pad in `START_INTEGRATING` to compensate for the multiply-accumulate core's
own pipeline latency, "so that we integrate for exactly the desired period."

## 6. Register write path and the readback CAM

Every register in `dpll_wrapper.v` is one instance of
`parallel_bus_register_32bits_or_less`, each independently comparing the bus
address to its own `ADDRESS` generic — there is no central address decoder,
so two instances that reuse the same `ADDRESS` would silently double-write
with no error indication (`sys_err` is never asserted from these instances).

Most of these registers are write-only from the CPU's perspective.
`addr_packed.vhd` fixes this generically rather than per-register: it snoops
every bus write into a FIFO (`sys_addr & sys_wdata`), and
`FSM_addr_packed.vhd` drains it into a 256-entry content-addressable RAM
(linear-scan by address) so any previously-written register can be read back
later. The RAM is pre-loaded at synthesis time from `ram_init_file.txt` so
readback works even before software ever writes a given register.

Read path performs a linear scan and returns a sentinel if nothing matches:

```vhdl
when STATE_VERIFY_READ =>
    if to_integer(unsigned(data_ram_sys_addr)) = to_integer(unsigned(wanted_sys_addr)) then
        data_out <= data_ram_sys_data; ack <= '1'; ...
    elsif to_integer(unsigned(read_address)) = to_integer(unsigned(last_addr_with_data)) then
        data_out <= x"EFFFFFFF";  -- value we send to the computer to tell we have not find anything
        ack <= '1'; ...
```

**Known dead code worth flagging**: the guard that's supposed to stop adding
new CAM entries once full (`enable_new_entries`) is commented out in
`FSM_addr_packed.vhd`. A 257th unique address would silently wrap
`last_addr_with_data` from `0xFF` to `0x00` and clobber entry 0.

`FSM_addr_packed` explicitly excludes address range `0x0025`–`0x0040` from
its own lookup, ceding those to `registers_read.vhd`'s dedicated hardware
path instead — avoiding ambiguity about which module answers a given read.

## 7. Legacy narrow-range readback — `DigitalPLL/registers_read.vhd`

Handles CPU reads of status flags, dither-lock-in results, frequency-counter
data, and DAC monitor values for the fixed address range `0x0025`–`0x0043`.
Uses a "latch-on-first-read, drain-on-last-read" pattern to keep multi-word
(>32-bit) values atomic across sequential 32-bit reads — e.g. reading
address `0x0030` snapshots several 64-bit counters and DAC monitors into
holding registers in the same cycle, so the values read out at `0x0031`-`0x0037`
are all from the same instant rather than torn across separate polls:

```vhdl
when x"00030" =>
    sys_rdata <= zdtc_samples_number_counter;
    counter0_out_reg <= counter0_out;
    counter1_out_reg <= counter1_out;
    DAC0_out_reg <= DAC0_out;
    ...
```

The external-clock-vs-ADC-clock counter (`counter_reg_to_dpll`, from
`red_pitaya_top.v`) is handshaked similarly at `0x0041`-`0x0043`: reading the
high word (`0x0043`) pulses `counter_read`, clearing `have_counter` so the
next `counter_new_data` pulse can load a fresh value. New counter samples that
arrive before the CPU has drained the previous one are simply dropped.

## 8. Frequency counters

`dual_type_frequency_counter.vhd` is the live zero-dead-time counter (the
older `zero_deadtime_counter.vhd` is fully commented out — dead). It runs two
`triangular_frequency_counter` instances in a ping-pong/interleaved fashion so
that one counter accumulates the next gate window while the other is
dumping its result — no samples are dropped between readings. Notably, the
DDC1-side counter instance is deliberately reset by `rst_frontend0` (not
`rst_frontend1`):

```verilog
// the 0 here is not a typo: we want the two counters to stay
// synchronized even during a reset
```

`digital_clock_freq_counter.vhd` reuses this same counter internally to
measure an arbitrary clock (`clk_target`) against a reference (`clk_ref`),
used at the top level for the external-reference-clock measurement.

## 9. Top-level integration and clocking — `red_pitaya_top.v`

`red_pitaya_pll` (the board's MMCM/clock-generator wrapper) produces
`adc_clk`/`adc_clk_2x`, which feed `dpll_wrapper` as `clk1`/`clk1_timesN`
(the latter used by the minimum-phase FIR path). The 8-way AXI fan-out
decodes `sys_addr[22:20]` into a one-hot chip-select gating both write- and
read-enable per slot, muxing the right 32-bit slice of the concatenated
`sys_rdata`/`sys_err`/`sys_ack` buses back to the PS:

```verilog
assign sys_cs = 8'h01 << sys_addr[22:20];
assign sys_wen = sys_cs & {8{ps_sys_wen}};
assign sys_ren = sys_cs & {8{ps_sys_ren}};
assign ps_sys_rdata = sys_rdata[sys_addr[22:20]*32+:32];
```

`dpll_wrapper`'s own `rst` input is hard-tied to `1'b0`:

```verilog
// I don't know if we can use the RedPitaya's reset because I don't know
// if it is clock-synchronous or not. In any case, it does not matter
// because we have the sys_bus driven resets
```

— i.e. `dpll_wrapper.v`'s own internal counter-based reset trees are relied
on exclusively for bringing up DPLL state cleanly.

See [external-clock-and-hv-switcher-protection.md](external-clock-and-hv-switcher-protection.md)
for the external reference clock path, the MMCM clock-select mechanism, and
the `duty_cycle_protector.vhd` HV-switcher watchdog in detail — those are
adjacent to but not part of the DPLL register map covered here.

## 10. Correctness-review notes

- `resize_with_saturation.vhd` is the universal saturating-resize primitive
  used throughout the loop filter. Its `synchronous_clear` forces output and
  rail-flag outputs to 0, not to any reset/idle value of the accumulator it's
  attached to — worth double-checking against expected behavior at unlock if
  modifying accumulator initialization.
- Every configuration parameter in `dpll_wrapper.v` is one
  `parallel_bus_register_32bits_or_less` instance with a distinct `ADDRESS`
  generic. There is no central decoder — anyone adding a new register must
  manually verify the `ADDRESS` doesn't already appear elsewhere in
  `dpll_wrapper.v`, since a collision would silently double-write with no
  error indication.
- The CAM "stop adding new entries when full" guard in `FSM_addr_packed.vhd`
  is dead code (see §6) — flagged here in case it's ever relied upon.
