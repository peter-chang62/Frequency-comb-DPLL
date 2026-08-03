# Proposal: feed phase directly to the loop filter when `angleSelect` is I/Q

**Status: proposal only — no firmware or GUI code has been changed.** This
document captures the investigation and design discussion for adding a
"phase-direct-feed" mode to the DPLL, for whoever picks this up next.

## 1. Motivation

`angleSelect` (register `0x8004`, `BUS_ADDR_ddc_angle_select`) currently
picks what value populates `wrapped_phase_internal` in
`DDC_wideband_filters.vhd` — the true CORDIC angle, or a quantized/limited
I or Q value:

```vhdl
-- DDC_wideband_filters.vhd:302-308
with angleSelect select
wrapped_phase_internal <= wrapped_phase_cordic(wrapped_phase_internal'range) when b"0000",
                          Q_quantized                                        when b"0001",
                          Q_limited                                          when b"0010",
                          I_quantized                                        when b"0011",
                          I_limited                                          when b"0100",
                          (others => '0')                                    when others;
```

Regardless of which mode is selected, this value is **unconditionally
differentiated** sample-to-sample before it ever reaches the loop filter
(`DDC_wideband_filters.vhd:358-393`, feeding `inst_frequency`/`inst_freq_internal`,
wired to `PLL0_loop_filters`/`PLL1_loop_filters`'s `data_in` in
`dpll_wrapper.v:947` / `:1081`). `wrapped_phase0`/`wrapped_phase1` themselves
are computed and exposed as separate module outputs but never reach the loop
filter today — see `dpll_wrapper.v:610-611`, `:745-746`.

For CORDIC mode this differencing is essential (it's how the design turns a
periodic phase measurement into a frequency error the loop can servo on,
safely handling wraps via modular arithmetic — see
[ddc-phase-detector-and-error-signal-notes.md §7](ddc-phase-detector-and-error-signal-notes.md)).
For the I/Q modes, though, this looks like an artifact of the I/Q modes
being bolted onto a pipeline built around CORDIC's needs
([ddc-phase-detector-and-error-signal-notes.md §8](ddc-phase-detector-and-error-signal-notes.md)):
when a user selects `I_limited`/`Q_limited`, they are implementing something
closer to a classic analog phase-detector (double-balanced-mixer-style) lock,
where the natural thing to feed a loop filter is the phase-like value itself,
not its derivative followed by the loop filter's own re-integration.

## 2. Hazards of feeding phase directly (why this isn't just a rewire)

1. **Wrap safety.** `wrapped_phase`/I/Q values can jump the full
   representable range in one cycle (e.g. during acquisition, far from
   lock). The existing differencing step is exactly what makes this safe —
   fixed-width two's-complement subtraction recovers the correct
   shortest-path Δφ even across a wrap (worked example in
   [ddc-phase-detector-and-error-signal-notes.md §7](ddc-phase-detector-and-error-signal-notes.md)).
   Feed `wrapped_phase` directly into the loop filter and a wrap looks like a
   full-scale phase-error impulse to a filter whose main accumulator has
   **no anti-windup by design**
   (`PLL_loop_filters_with_saturation.vhd`, comment: *"this is by design
   because having anti-windup here would mean not integrating part of the
   error signal"*) — a real risk of kicking the actuator hard. This risk is
   specific to CORDIC's `wrapped_phase` (guaranteed periodic); I/Q values are
   bounded and don't have this particular failure mode, but have their own
   (oscillating/sign-flipping error away from lock, §7 of the same doc).
2. **Gain retuning.** The existing P/I/I²/D gains
   (`N_DIVIDE_P/I/II/D` in `PLL_loop_filters_with_saturation.vhd`) were
   hand-tuned assuming a frequency-error input (see the
   *"changed 2017-05-02... to recenter gain"* comment in `dpll_wrapper.v`).
   Feeding phase directly shifts every branch's role by one integration
   order (detailed derivation in
   [ddc-phase-detector-and-error-signal-notes.md §5](ddc-phase-detector-and-error-signal-notes.md)) —
   gains would need re-tuning, not just a scale factor.
3. **Lock-offset trickle mismatch.** The "trickle the lock-offset to zero"
   FSM (`DDC_wideband_filters.vhd:358-393`) injects `inst_freq_adjust` into
   the *differentiator's* output specifically to avoid a glitch on lock-on.
   If the loop filter is fed phase directly (bypassing the differentiator),
   this mechanism no longer touches the signal being fed to the loop filter —
   an equivalent offset-subtraction would need to be built directly on the
   phase path, or lock-on will reintroduce the step-function glitch this
   code was written to avoid.

**Conclusion:** a mux to route phase directly to the loop filter is simple
to build, but should be treated as a first cut requiring bench verification
at low bandwidth/gain — not a drop-in swap — until the above three points
are addressed or explicitly accepted as out of scope.

## 3. The `select_phase_or_freq0`/`select_phase_or_freq1` bits — dead code, safe to repurpose

While looking for a control bit to drive the new mux, we found a
pre-existing, fully-plumbed-but-inert pair of signals that looks tailor-made
for this:

- **FPGA side**: `select_phase_or_freq0`/`select_phase_or_freq1`
  (`dpll_wrapper.v:545`) are packed into real register bits 4-5 of `0x8002`
  (`BUS_ADDR_ddc_filter_select`), alongside `ddc0_filter_select`/
  `ddc1_filter_select` (bits 0-3):
  ```verilog
  // dpll_wrapper.v:574-586
  parallel_bus_register_32bits_or_less # (
      .REGISTER_SIZE(6),
      .REGISTER_DEFAULT_VALUE(32'b0),
      .ADDRESS(16'h8002)
  )
  parallel_bus_register_ddc_filter_select (
       .clk(clk1), .bus_strobe(cmd_trig), .bus_address(cmd_addr),
       .bus_data({cmd_data2in, cmd_data1in}),
       .register_output({select_phase_or_freq1, select_phase_or_freq0, ddc1_filter_select, ddc0_filter_select}),
       .update_flag()
       );
  ```
  A write to this address genuinely lands in FPGA flip-flops — but the only
  two places that ever *read* `select_phase_or_freq0/1` are both fully
  commented out (`dpll_wrapper.v:1560-1595`, `1864`, `1882`): the two
  `residuals_streaming` instantiations, for the now-superseded
  `residuals_streaming.vhd` module (superseded by the `ram_data_logger`
  path, per this repo's `CLAUDE.md`). Nothing else references these wires.
  **On the live bitstream, these bits are written but drive nothing.**
- **Python side**: `residuals0_phase_or_freq`/`residuals1_phase_or_freq`
  (`SuperLaserLand_JD_RP.py:329-330`) are hardcoded to `0` at `__init__` and
  never set anywhere else in the codebase (no setter, no GUI control):
  ```python
  # SuperLaserLand_JD_RP.py:1809-1810, inside set_ddc_filter_select_register()
  register_value = self.ddc0_filter_select + (self.ddc1_filter_select<<2) + (self.residuals0_phase_or_freq<<4) + (self.residuals1_phase_or_freq<<5)
  self.send_bus_cmd_16bits(self.BUS_ADDR_ddc_filter_select, register_value)
  ```
  `get_ddc_filter_select()` (`SuperLaserLand_JD_RP.py:1821`) never reads
  bits 4-5 back either.

Bit ordering between the two sides was checked and lines up correctly
(Python bit4 → `select_phase_or_freq0` → FPGA register bit4; Python bit5 →
`select_phase_or_freq1` → FPGA register bit5) — it's not a wiring bug, just
a fully dead feature. The bits occupy positions disjoint from
`ddc0/1_filter_select`, so today's writes of `0` into them have zero
observable effect on anything, including the filter-select bits.

**Decision**: reuse these bits for phase-direct-feed rather than adding a
new register address. Rename on both sides for clarity when implementing
(e.g. `select_phase_or_freq0` → `ddc0_phase_direct_select`,
`residuals0_phase_or_freq` → `ddc0_phase_direct_select`) so the git history
doesn't read as if it were still about the dead residuals-streaming feature.

## 4. Proposed firmware edit — `dpll_wrapper.v`

Two 2-way muxes, using Verilog's ternary operator (the Verilog equivalent of
VHDL's `x when cond else y`, or Python's `x if cond else y` / `np.where`):

**Channel 0** — insert ahead of `PLL0_loop_filters.data_in`
(`dpll_wrapper.v:943-957`, currently `.data_in(inst_frequency0)`):

```verilog
wire [9:0] pll0_loop_filter_data_in;
assign pll0_loop_filter_data_in = select_phase_or_freq0 ? wrapped_phase0 : inst_frequency0;
// ... then PLL0_loop_filters.data_in(pll0_loop_filter_data_in)
```

**Channel 1** — hook into the existing `loop_filters_1_mux` 3-to-1 async mux
(`dpll_wrapper.v:768-775`) rather than adding a second mux stage, since
`DDC1_output` (the current `in0_mux` input) is already just one of three
selectable inputs to `PLL1_loop_filters.data_in`:

```verilog
wire [9:0] pll1_loop_filter_freq_or_phase_in0;
assign pll1_loop_filter_freq_or_phase_in0 = select_phase_or_freq1 ? wrapped_phase1 : DDC1_output;
// ... then loop_filters_1_mux.in0_mux(pll1_loop_filter_freq_or_phase_in0)
```

`wrapped_phase0`/`wrapped_phase1` and `inst_frequency0`/`DDC1_output` are
all already `[9:0]` — no width mismatch or sign-extension needed.

## 5. Proposed Python edit — `SuperLaserLand_JD_RP.py`

`set_ddc_filter_select_register()` (line 1803) is the single place that
writes register `0x8002`, and it is only ever called from `set_ddc_filter()`
(line 1789), which is only ever called from
`DisplayDividerAndResidualsStreamingSettingsWindow.ddcClicked()`
(`DisplayDividerAndResidualsStreamingSettingsWindow.py:133-173`).

Critically, `ddcClicked()` is connected via `.clicked.connect(self.ddcClicked)`
to **every one of the 16 radio buttons** in the DDC settings group — both
filter-bandwidth and `angleSelect`, for both channels — and each invocation
re-reads and re-sends the *entire* current UI state as one combined register
write. This means:

- Any future `ddc0_phase_direct_select` control must have its checked-state
  read **inside** `ddcClicked()` itself, the same way the angle-select radio
  buttons are read (lines 142-151) — not via a standalone setter method.
  Otherwise, clicking an unrelated filter/angle radio button will silently
  stomp the phase-direct bit back to whatever `ddcClicked()` currently
  hardcodes, since it always sends the full packed word.
- `pushValues()` → `ddcClicked()` (line 35-36) also fires this on startup /
  "push defaults", so the new control's default state needs to be
  initialized consistently with whatever widget represents it.

## 6. Proposed GUI layout — `DisplayDividerAndResidualsStreamingSettingsWindow.py`

The DDC settings widget is a single `QGridLayout` (lines 268-296) inside one
`QGroupBox` (`self.qgroupbox_ddc`), instantiated once in `XEM_GUI3.py:125`
and placed into the app's **"Settings" tab** (`XEM_GUI3.py:193`).

Current grid (0-indexed row/col, matching the literal `grid.addWidget(widget, row, col)` calls):

| row | col0 | col1 | col2 | col3 |
|---|---|---|---|---|
| 0 | DDC0 filter label | Wideband0 | Narrowband0 | WidebandFIR0 |
| 1 | DDC0 angle label | cordic0 | quad_msb0 | quad_lsb0 |
| 2 | *(empty)* | *(empty)* | inphase_msb0 | inphase_lsb0 |
| 3 | DDC1 filter label | Wideband1 | Narrowband1 | WidebandFIR1 |
| 4 | DDC1 angle label | cordic1 | quad_msb1 | quad_lsb1 |
| 5 | *(empty)* | *(empty)* | inphase_msb1 | inphase_lsb1 |

Target layout — one new row per channel, inserted immediately after that
channel's inphase_msb/inphase_lsb row, in columns 2-3 (the same columns as
the existing I/Q radio buttons):

| row | col0 | col1 | col2 | col3 |
|---|---|---|---|---|
| 0 | DDC0 filter label | Wideband0 | Narrowband0 | WidebandFIR0 |
| 1 | DDC0 angle label | cordic0 | quad_msb0 | quad_lsb0 |
| 2 | *(empty)* | *(empty)* | inphase_msb0 | inphase_lsb0 |
| **3 (new)** | *(empty/label)* | *(empty)* | **phase_direct0** | |
| 4 | DDC1 filter label | Wideband1 | Narrowband1 | WidebandFIR1 |
| 5 | DDC1 angle label | cordic1 | quad_msb1 | quad_lsb1 |
| 6 | *(empty)* | *(empty)* | inphase_msb1 | inphase_lsb1 |
| **7 (new)** | *(empty/label)* | *(empty)* | **phase_direct1** | |

DDC0's new row is inserted between old rows 2 and 3, shifting the entire old
DDC1 block down by one; DDC1's new row is appended as the new final row of
the grid (row 7).

## 7. Open items before implementing

- Decide how to handle the wrap-safety and gain-retuning hazards from §2
  (e.g. bench-test at reduced gain first, or add an explicit unwrap/offset
  mechanism for the phase path before trusting it at full bandwidth).
- Decide exact widget type/label for the new GUI control (checkbox vs.
  folding into the existing radio-button group) and finalize the rename of
  `select_phase_or_freq0/1` / `residuals0_phase_or_freq` discussed in §3.
- None of the edits in §4-§6 have been applied to any file as of this
  writing — this document is a plan, not a changelog.
