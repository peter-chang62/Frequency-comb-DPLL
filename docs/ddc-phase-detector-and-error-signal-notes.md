# DDC phase detector and PLL error-signal notes

This document supplements
[pll-firmware-architecture.md](pll-firmware-architecture.md) with a deeper,
applied dive into how the DDC's phase detector (`DDC_wideband_filters.vhd`)
actually behaves, what error signal the loop filter really servos on, and
the practical resolution/bandwidth limits this creates — motivated by a
concrete troubleshooting question: locking a ~30 MHz oscillator with only
~10 Hz of short-timescale frequency fluctuation, where the digital lock
"basically doesn't see the error" while an external analog phase lock (fed
from a double-balanced mixer) works fine.

## 1. Signal widths through the DDC

- `I_filtered`/`Q_filtered` (mixer output, into the CORDIC) stay at the full
  `INPUT_DATA_WIDTH := 16` bits.
- The CORDIC's raw phase output is 12 bits (`WRAPPED_PHASE_WIDTH+2`); the top
  two are dropped as documented "useless" bits per the Xilinx CORDIC
  datasheet, leaving the final `wrapped_phase`/`wrapped_phase_internal` at
  **10 bits** (`WRAPPED_PHASE_WIDTH := 10`), signed, representing
  `(phi/2π) × 2^10` — i.e. -512..+511 covering -π..just-under-+π.
- The `angleSelect` mux (`with angleSelect select ... when b"0000" | ...`,
  a VHDL selected/concurrent signal assignment, not an `if`) picks what
  populates `wrapped_phase_internal`: the true CORDIC angle (`0000`), or one
  of `Q_quantized`/`Q_limited`/`I_quantized`/`I_limited` (`0001`-`0100`) —
  all truncated/derived down to the same 10-bit width regardless of which is
  selected. This is exposed to the operator via the GUI in
  `DisplayDividerAndResidualsStreamingSettingsWindow.py` as radio buttons
  (`cordic`, `quadrature_msb`/`quadrature_lsb`, `inphase_msb`/`inphase_lsb`),
  writing register `0x8004` (`BUS_ADDR_ddc_angle_select`).

## 2. `quantizer.vhd` vs `limiter.vhd` ("MSB" vs "LSB" modes)

Both reduce a 16-bit I or Q value to 10 bits, but via different mechanisms
with different failure modes:

- **`quantizer.vhd` ("MSB")**: a dithered, noise-shaped bit-width reducer —
  adds PRBS dither, keeps an error-feedback accumulator
  (`feedback <= feedback + qerr`), then takes the top 10 bits
  (`qi_quant <= qo(15 downto 6)`). This is a plain two's-complement bit-slice
  with **no saturation**. Because it's a straight truncation, a value that
  exceeds the 10-bit range doesn't clip — it **wraps around** (equivalent to
  the value mod 2^10). A large spike therefore produces a sawtooth of
  repeated ±511/-512 wraps, with the number of wraps proportional to how far
  the spike exceeds the range — a serious problem downstream, since the
  loop's frequency-difference computation can't distinguish a genuine
  multi-cycle phase wrap from this kind of amplitude-overflow aliasing.
- **`limiter.vhd` ("LSB")**: hard-clamps the 16-bit value against
  `di_min`/`di_max` *before* truncating, so the subsequent 10-bit slice is
  lossless (the value is already guaranteed in range). No dithering, no
  wraparound — large excursions saturate flat at the rail instead.

**When to use which**, and why (grounded in the I/Q relationship, see §3
below): quantized/dithered modes suit a clean signal expected to stay
bounded near its operating point; limited/clamped modes are the safer choice
when the signal may have occasional large transients (mode hops, cycle
slips) that must not be allowed to alias into a spurious huge "frequency
error" that could kick the loop hard.

## 3. Why I vs Q matters, and what `reference_frequency = 0` does

With `I = A·cos(θ)`, `Q = A·sin(θ)` (θ = beat-note phase relative to the LO):
`dQ/dθ` is maximal at θ=0 (Q is a good zero-crossing discriminator there),
while `dI/dθ` is maximal at θ=π/2. So the choice of I vs Q for the
quantized/limited modes fixes *which phase point the loop locks to* — pick
whichever channel crosses zero with a good slope at your desired lock phase.

`reference_frequency = 0` is a special case in `DDC_wideband_filters.vhd`:
the NCO is forced to a fixed `cos=1, sin=0` reference (instead of an
oscillating one), and the front-end high-pass filter is bypassed. The
practical effect: `I` becomes just the (low-pass-filtered) raw ADC signal
itself, and `Q` becomes exactly zero always — a degenerate case of the same
θ=0 rule above (Q carries no information there). This mode exists to
repurpose the DPLL infrastructure to lock a baseband/DC-ish error signal
directly (e.g. from an external analog mixer or other error detector), using
`angleSelect` set to `I_quantized`/`I_limited`, not CORDIC.

## 4. The loop filter's actual error signal, and the lock-offset "trickle"

`PLL0_loop_filters`/`PLL1_loop_filters` (`PLL_loop_filters_with_saturation.vhd`)
are wired to `data_in(inst_frequency0/1)` in `dpll_wrapper.v` — **not**
`wrapped_phase`. `inst_frequency` is computed, regardless of `angleSelect`,
as a same-clock-cycle difference:
```
inst_freq_internal <= wrapped_phase_internal - wrapped_phase_internal_last + inst_freq_adjust;
```
The loop filter then *re-integrates* this internally (its own
`phase_error_accumulator`, deliberately built with no anti-windup) to
reconstruct a phase-like quantity for its I and I² branches.

**The lock-offset "trickle"** (same process, lines ~358-393): naively
zeroing the phase reference the instant `lock` is asserted would be a step
function in phase — and a step's derivative is an impulse, i.e. a one-cycle
glitch that would kick the actuator. Instead, the module captures the
phase value present at the moment of lock (`phase_offset`) and walks it to
zero one LSB per cycle (`inst_freq_adjust` = ±1 each cycle), injecting the
same total correction as a ramp instead of a step. Net effect: the loop's
internal accumulator ends up representing phase relative to a fixed,
LO-defined zero — not "whatever phase happened to be present when lock was
asserted" — with no single-cycle transient. Crucially, this doesn't require
knowing anything about phase history: `wrapped_phase` is a fresh, stateless
CORDIC/mixer computation every cycle, not an accumulated quantity, so
"the phase right now" is always a valid, instantaneous measurement, however
unpredictable its value while off-frequency and unlocked.

## 5. What P, I, I², D actually servo, depending on what feeds `data_in`

The loop filter's own transfer function (independent of what the input
physically means) is `G(s) = P + I/s + II/s² + D·s·Hlpf(s)`.

- **Today (frequency-fed, `F(s) = s·Θ(s)`)**: substituting gives
  `Y(s)/Θ(s) = P·s + I + II/s + D·s²·Hlpf(s)`. Relative to phase, each
  branch's role is shifted by one order: the "P" branch behaves like a
  phase-rate/derivative term, "I" behaves like a plain phase-proportional
  term, "I²" behaves like the true phase-integral term (the one actually
  responsible for zero steady-state phase error under a constant frequency
  disturbance — type-2 behavior), and "D" behaves like a phase
  second-derivative/acceleration term.
- **Hypothetical (phase-fed directly, `F(s) = Θ(s)`, i.e. skip the
  differencing)**: `Y(s)/Θ(s) = G(s)` unchanged — no order shift. Each
  branch takes its plain textbook PID meaning relative to phase: P is
  proportional-to-phase, I is integral-of-phase (type-2, nulls steady-state
  phase error under a constant frequency disturbance), I² is
  double-integral-of-phase (type-3, nulls steady-state error under a
  *ramping* frequency disturbance), and D is derivative-of-phase — which is
  just frequency error again, now used purely as a damping term.

Feeding phase directly would need entirely re-tuned gains (the existing
values were hand-tuned for frequency-referred branch roles and units), and
would also need a genuinely **unwrapped** phase (see §7) rather than the raw
wrapped CORDIC/I/Q value, since a raw wrap would look like a huge phase-error
step to a directly-fed servo, not just at lock-on (as the trickle handles)
but continuously, every cycle the phase wraps.

## 6. Why the digital lock couldn't see a ~10 Hz fluctuation on a 30 MHz beat

`inst_frequency` is a 10-bit difference of adjacent (1 ADC-clock-cycle
apart, 8 ns) phase samples. One LSB of this "instantaneous frequency"
corresponds to a real frequency of:
```
Fs / 2^N_bits = 125 MHz / 1024 ≈ 122 kHz per LSB
```
A ~10 Hz fluctuation is over four orders of magnitude below this — the true
per-sample phase step (~5×10⁻⁷ rad) is ~12,000× smaller than even 1 LSB of
`wrapped_phase` itself. So `inst_frequency` reads ~0 almost every cycle, with
only a sparse ±1 blip roughly every ~100 µs as true phase slowly crosses a
quantization boundary — this fully explains "the Red Pitaya basically
doesn't see the error," independent of which `angleSelect` mode is used or
whether down-conversion happens in analog or digital hardware (both funnel
into the same per-ADC-cycle 10-bit differencer before reaching the loop
filter).

General formula: `freq_LSB = Fs / (K × 2^N_bits)`, where `K` is how many ADC
cycles apart the two differenced samples are (currently `K=1`) and `N_bits`
is the phase width (currently 10). Both `K` and `N_bits` are independent
levers — increasing either improves resolution.

- **Fixing via bits alone (`K=1`)**: for ~1 Hz resolution, `N_bits ≥
  log2(125e6/1) ≈ 27` — not realistic; a single 8 ns ADC sample simply
  doesn't carry that much genuine phase information (the ADC itself is only
  14 bits, and CORDIC/front-end averaging gains a few more at best, nowhere
  near 27).
- **Fixing via a longer baseline (`K`), same `N_bits`**: for ~1 Hz
  resolution, `K ≥ 125e6/(1×1024) ≈ 122,000 cycles ≈ 1 ms` — fully
  achievable with existing 10-bit phase precision. This is the same
  principle behind this codebase's own `dual_type_frequency_counter.vhd`
  (frequency resolution = 1/gate-time; `N_gate_time` there is set to 1
  second for sub-Hz counter resolution).

**Implementation matters**: a naive block/decimating approach (only compute
a new value every K cycles, hold it in between) genuinely reduces the loop's
update rate to `Fs/K` (~1 kHz for K≈122,000) and introduces a stair-stepped,
zero-order-hold signal. The correct implementation is a **sliding
accumulator** — an add-newest/subtract-oldest running sum over the last K
per-cycle increments, the same technique this firmware's own boxcar filters
already use — which produces a fresh output every single clock cycle (full
125 MHz update rate preserved), mathematically equivalent to a low-pass
filter with cutoff ~1/(window length). A 10 Hz signal sits deep inside that
passband and passes through essentially unattenuated; what gets suppressed
is the short-timescale quantization noise that was dominating the raw
per-cycle derivative. The real (and honest) remaining cost is added
**latency** (~K/2 cycles, roughly half the window — ~0.5 ms for a 1 ms
window), which eats into loop phase margin/achievable bandwidth, but is a
small price for tracking something as slow as 10 Hz.

Note the distinction from classic "gate time": a literal gate-time counter
(like `dual_type_frequency_counter.vhd`) reports one value per gate period
(slow output-update rate, fine for a display/logging counter) even with
zero dead-time input coverage; the sliding-accumulator approach gets the
same averaging/resolution benefit while keeping a fresh value every cycle,
which is what a real-time servo actually needs.

**Practical implication for the three options originally considered**:
neither "digitally mix at reference_frequency=30 MHz + `angleSelect=I_limited`"
nor "externally mix in analog + `reference_frequency=0` + `angleSelect=I_limited`"
fixes the resolution problem — both still funnel into the identical
per-ADC-cycle, 10-bit differencer before reaching the loop filter, regardless
of where/how the down-conversion to baseband happens. The actual bottleneck
is the differencing scheme itself (its bit width × its 1-cycle baseline),
which would need a firmware change (widen the baseline via a sliding
accumulator, as above) to fix.

## 7. Wraparound-safe differencing — how it works, and why it doesn't apply to I/Q

CORDIC's `wrapped_phase` is a **linear**, modular (mod 2π) encoding of the
true phase θ — it still wraps (sawtooths from +511 to -512), it isn't
"unwrapped" in any smooth sense. The reason differencing it works anyway:
fixed-width two's-complement subtraction is inherently modulo-2^N
arithmetic, and since this representation is a linear mod-2π/mod-2^N
mapping of θ, an ordinary signed subtraction "automatically" recovers the
correct shortest-path Δθ, with zero extra logic — *provided* the true phase
change between the two samples is under half a cycle (magnitude < 512
codes). Worked example: phase advancing from code 510 through a true value
of 525 wraps to -499; `(-499) - (+510) = -1009`, which modulo 1024 is
exactly `+15` — the correct answer, recovered "for free" from ordinary
fixed-width arithmetic.

This has a direct consequence for §6's baseline-widening fix: you must not
directly difference two *far-apart* raw wrapped-phase samples (risks
exceeding the half-cycle safe margin over a longer gap, and would silently
alias). The safe implementation accumulates many already-correct,
single-cycle-safe differences into a wide, non-wrapping running total,
rather than differencing distant wrapped samples directly.

**This mechanism does not apply to `I_limited`/`Q_limited`.** `I = A·cos(θ)`
is smooth and bounded — no discontinuity, so there's nothing for the
modular-arithmetic trick to fix (and no need for it). But this doesn't make
differencing I safe either — it just fails for a different reason: `dI/dt =
-A·sin(θ)·dθ/dt`, so even a constant true frequency error produces an
*oscillating, sign-flipping* signal at the loop filter's input as θ sweeps
through a cycle, unrelated in any simple way to the true, possibly
constant-sign frequency error. This is why I/Q-based `angleSelect` modes are
only valid as a *local, small-signal* phase-error proxy near the intended
lock point (where sin/cos stay roughly constant across the residual
excursion) — not as a general-purpose, full-range acquisition-time detector
the way CORDIC is.

## 8. Why does the firmware still difference the signal when `I_limited` is selected?

`I_limited` is conceptually a classic analog-phase-detector-style output
(compare: a double-balanced mixer feeding a loop filter directly, no
derivative stage) — traditionally you'd feed it straight into a PID.
Architecturally, though, `angleSelect` only chooses *what value* populates
`wrapped_phase_internal`; the differencing step downstream is one shared,
unconditional pipeline applied regardless of that choice — there's no
alternate direct-feed path in the RTL for I/Q modes. This looks like an
artifact of I/Q modes being added onto a pipeline originally built around
CORDIC's needs (where differencing is essential), not a data path purpose-built
for a direct phase lock.

Practically, near the intended lock point this is mostly harmless: `I` is
approximately linear in the residual phase error there, so
differentiate-then-reintegrate recovers essentially the same small-signal
error a direct feed would have given — a redundant round trip, not a wrong
one. The real cost shows up away from lock, where it produces the
oscillating/sign-flipping behavior from §7. One plausible (if unconfirmed —
no comment in the code states this explicitly) benefit of keeping the
differencing even for I/Q modes: it gives free rejection of any constant DC
offset on the I/Q channel (mixer/ADC bias), since differentiating a constant
yields exactly zero — analogous to AC-coupling a mixer output in an analog
design. A purpose-built direct-phase-fed design (§5's "hypothetical"
scenario, retuned gains) would eliminate the far-from-lock oscillation risk
entirely, at the cost of needing some other explicit way to trim out any
DC offset on that channel.
