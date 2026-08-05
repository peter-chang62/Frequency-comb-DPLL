# Report: standalone Red Pitaya build — retire PLL0/DAC0, remap DAC1→SMA DAC0 and DAC2→SMA DAC1

**Date:** 2026-08-04
**Branch:** `dac0-dac2-unload-rerouting-plan`
**Plan:** see [plan.md](plan.md) in this folder

## Request

Make a variant of the code that runs the Red Pitaya standalone, without its daughterboard — so only the board's own ±1V SMA outputs are available. Keep only one PLL (the current PLL1 / optical lock plus DAC2), retire PLL0 and its peripherals (manual DC offset, dither, VNA), and shift the surviving outputs down one connector: what came out of DAC1 now comes out of DAC0, what came out of DAC2 now comes out of DAC1. To the operator: one output removed, one output remapped.

The user asked that planning and instruction-writing be done by Opus 5, with the actual code edits carried out by Sonnet 5 subagents following those instructions.

## Investigation

Traced the DAC output paths from the GUI down to the pins, reading `SuperLaserLand_JD_RP.py`, `XEM_GUI3.py`/`XEM_GUI_MainWindow.py`, `dpll_wrapper.v`, `red_pitaya_top.v` and `mux_internal_vco.vhd`.

**Two findings shaped the whole approach:**

1. **The change cannot be done in Python alone.** `DACout2` never reaches an SMA — it leaves the FPGA as an SPI bitstream via `max5541_spi_dac_interface` (`red_pitaya_top.v:1096`) onto the expansion pins. The only path to the SMAs is `mux_internal_vco`, hardwired to `DACout0`/`DACout1`. Its one register, `selector_vco`, chooses which channel receives the internal VCO — not which signal feeds a channel. So a Vivado rebuild is unavoidable. This was raised with the user before planning further; they confirmed Vivado is available.

2. **Retiring PLL0 needs almost no Python work.** The CEO lock tab is constructed with `output_controls=(True, False, False)` (`XEM_GUI3.py:131`), so it already only ever writes DAC0 registers. Once `DACout0` stops reaching a pin, that tab becomes inert on its own — exactly the "swinging the scrollbar produces no output" behaviour the user described. No dither/VNA/offset teardown was needed.

The real work therefore reduced to two lines of Verilog plus scaling and label cleanup.

## Decisions made during planning (via clarifying questions)

1. **DAC2 → SMA mapping: offset binary, full span.** The firmware DAC2 datapath is 16-bit *unsigned* (0..65535 → 0..3.3V into the MAX5541, then the daughterboard HV amp); the SMA is 14-bit signed, ±1V. Mapping the full unsigned span onto −1V..+1V keeps the entire DAC2 datapath in firmware bit-identical — both integrators, `dac2_error_computation`, railing and limits all untouched — so the conversion is a single wire. Python's conversion became affine to match, so the GUI reports real SMA volts instead of the old fictitious 0–3.3V scale.
2. **Relabel the GUI and visibly disable the CEO tab**, rather than leaving stale labels and relying on the operator to remember the cabling.
3. **Keep internal register indices at 1 and 2**, changing only user-visible labels. Renaming indices would mean rewriting the register map across VHDL, `monitor-tcp.c` and Python — three independent implementations with no shared schema.

## Execution

Work was split across three Sonnet 5 subagents over disjoint file sets so they could run in parallel: firmware, Python scaling + config, and GUI labels + tab state. Each was given explicit "do not touch" guardrails (register indices, `output_controls` tuples, `LOGGER_MUX` selector strings, log-file dict keys). Verification was kept in the main session so parallel agents wouldn't collide running pytest in the same directory.

Plan mode engaged partway through, after the firmware, scaling, config and CEO-tab edits had already landed on disk. The plan file was written at that point to capture both what had been applied and what remained; the remaining GUI relabels were completed after approval.

## Changes made

### Firmware — `Firmware_Vivado_Project/redpitaya.srcs/sources_1/imports/rtl/red_pitaya_top.v`
- Added `wire signed [15:0] DACout2_signed = {~DACout2[15], DACout2[14:0]};` above the `mux_internal_vco` instantiation, with a comment block explaining the remap.
- Changed two ports: `.DACin0(DACout1)` (optical fast → SMA DAC0) and `.DACin1(DACout2_signed)` (optical slow → SMA DAC1).
- `max5541_spi_dac_interface` left wired to `DACout2` so a daughterboard still works if reattached; `duty_cycle_protector` / HV-switcher protection untouched; PLL0 logic in `dpll_wrapper.v` left intact with `DACout0` simply unconnected for Vivado to trim.

### Python scaling — `digital_servo_python_gui/SuperLaserLand_JD_RP.py`
- Replaced `Vref_DAC2 = 3.3` with `DAC2_fullscale_volts = 1.0` and `DAC2_midscale_counts = 2**15`, keeping a comment recording the old daughterboard meaning.
- Rewrote the four DAC2 branches: `convertDACCountsToVolts` and `convertDACVoltsToCounts` are now affine; `getDACGainInCountsPerVolts` and `getDACGainInVoltsPerCounts` are pure slopes with no offset.
- `DAC2_gain = 2` confirmed unused; annotated rather than deleted.

**Call-site audit** (required because `convertDACVoltsToCounts` went from proportional to affine for DAC2): the only callers outside the definition are in `SLLSystemParameters.sendToFPGA`, converting absolute `Output_limits_low`/`Output_limits_high` volt values. Absolute values → the affine form is correct. No misuse found, nothing else changed.

> **Correction (see "Review follow-up" below).** This audit was incomplete. It covered only
> `convertDACVoltsToCounts`; `convertDACCountsToVolts` also became affine, in the opposite direction,
> and was never audited. One of its callers (`DisplayVNAWindow.py:129`) uses it as a gain, which was a
> real regression. Fixed in the follow-up pass.

### Config defaults
- `SLLSystemParameters.py` `populateDefaults()`, DAC2 only: limits `0`/`55` → `-1.0`/`1.0`, offset `27` → `0`; added a note that the DAC2 VCO gain needs re-measuring.
- `system_parameters_RP_Default.xml`, DAC2 only: limits `0`/`3.3` → `-1`/`1`; same XML comment on the VCO gain line.

### GUI
- `XEM_GUI3.py`: CEO tab retitled "CEO Lock (disabled)", `setTabEnabled(False)`, tooltip added, Optical Lock set as the initially-selected tab. Verified by grep that no other code path (timers, `closeEvent`, `setCustomShorthand`, `pushDefaultValues`/`getActualValues`/`pushActualValues`/`stopCommunication`) depends on that tab being selectable — all operate on the widget object directly.
- `XEM_GUI_MainWindow.py`: `'VCO Gains (DAC1, DAC2HV) [Hz/V]:'` → `'VCO Gains (SMA DAC0, SMA DAC1) [Hz/V]:'`.
- `FreqErrorWindowWithTempControlV2.py`: checkbox labels and the two-output plot-title format string changed to the SMA names. `self.DAC2_history`, `self.curve_dac2`, `self.qchk_show_DAC2` and the `self.output_files['DAC2']` dict keys deliberately left alone — those keys generate the binary log filenames `load_logging_data.py:66` parses back. The `output_number == 0` title string was left untouched (dead path).
- `SpectrumWidget.py`: the `['ADC0','ADC1','DAC0','DAC1','DAC2']` combo left unchanged, with a comment added explaining these are `LOGGER_MUX` firmware-datapath keys parsed via `int(input_select[3])` in `scaleADCorDACDataToVolts` and must not be renamed to the SMA labels.

## Verification performed

- `python -m py_compile` on all six edited Python modules — **passed**.
- All edited modules import cleanly under the user's `frog` conda env (PyQt5 + numpy + pyqtgraph present).
- **DAC2 conversion verified numerically against the firmware wire**: counts 0 → −1.0V, 32768 → 0.0V, 65535 → +0.99997V; slope 32768 counts/V; volts-per-count 3.0517578125e-05; round-trip error 0.0 across the full range. Matches `{~DACout2[15], DACout2[14:0]}` exactly (0 → 0x8000 = −32768, 32768 → 0x0000, 65535 → 0x7FFF = +32767).
- **Range-overflow check**: `convertDACVoltsToCounts(2, +1.0)` returns 65536, one past 16-bit range. Confirmed by reading `set_dac_limits` (`SuperLaserLand_JD_RP.py:1141-1150`) that it clamps against `limits_unsigned(16)` before transmission, so the value lands at 65535 rather than wrapping to 0.
- Grep confirmed no dangling `Vref_DAC2` code reference remains (only the historical comment).
- **pytest was NOT run** during this pass — no environment on this machine had pytest installed. It was installed and the suite run during the review follow-up below.
- Firmware changes were statically reviewed only — **not synthesized, not bench-tested**. No Vivado in this environment.

## Review follow-up (2026-08-04, same day)

A review pass over this work found three defects and two label gaps. All are fixed; the firmware
change was re-verified and needed no change.

### Defects found and fixed

1. **`DisplayVNAWindow.py:129` — regression, the significant one.**
   `output_volts_per_counts = convertDACCountsToVolts(idx, 1)` passes a single count to obtain a
   volts-per-count *slope*. That was valid while the DAC2 conversion was proportional; once it became
   affine it returned `-0.999969` for DAC 2 instead of `3.0517578125e-05`. Line 165 divides the measured
   transfer function by it, so a VNA sweep on DAC 2 came out ~32768× too small and sign-flipped — and
   that sweep is exactly how outstanding item 4 (re-measuring the DAC2 VCO gain) has to be done.
   Now uses `getDACGainInVoltsPerCounts()`, which is a pure slope for all three DACs and is
   bit-identical to the old expression for DAC0/DAC1 (verified numerically).
2. **`FreqErrorWindowWithTempControlV2.py:683`** computed the DAC2 voltage as
   `counts/(high-low)*2`, which has the right slope but no offset — it read 0…2 V instead of −1…+1 V,
   i.e. +1 V high, in the very plot title this session had relabelled to "SMA DAC1". Pre-existing (it was
   also wrong on the 3.3 V scale) but it contradicted this report's claim that the GUI reports real SMA
   volts, and it would have failed bench check 2. Now uses `convertDACCountsToVolts(2, ...)`.
3. **`SpectrumWidget.py:110-114`** hardcoded the DAC2 thermometer tick labels as `[0,1,2,3]` — the old
   0–3.3 V unipolar scale. The widget's numeric range was already correct (derived from
   `convertDACCountsToVolts`), so it was drawing a −1…+1 V range against 0–3 labels. The `k == 2` special
   case is removed; all three DACs share `[-1,-0.5,0,0.5,1]`.
4. **`XEM_GUI_MainWindow.py:1408`** did `setValue(int(current_output_in_volts))`, truncating the
   thermometer to −1/0/1 on a ±1 V scale. `ThermometerWidget.setValue` takes a float; the `int()` is gone.
   This turned out to be a long-standing bug — it was the reason `gui_test.py::test_displayDAC` failed
   at `HEAD`, and removing it makes that test pass.

### Label gaps closed

`SpectrumWidget.py:103,119` (`Output/Offset DAC %d [V]`, the labels beside the manual offset sliders)
and `LoopFiltersUI_DAC1_and_DAC2.py:351,365` (`Fast PZT (DAC1)` / `Slow PZT (DAC2)`) now use the SMA
names. `DisplayVNAWindow`'s `DAC 0/1/2` combos were deliberately left alone — those indices are passed
straight through as internal DAC indices — with a comment recording the mismatch.

### pytest — installed and run

`pytest 8.3.5` installed into the `frog` conda env; suite run with `QT_QPA_PLATFORM=offscreen`.

| | `HEAD` (before any of this work) | after this work + follow-up |
|---|---|---|
| passed | 19 | 20 |
| failed | 5 | 4 |

The 4 remaining failures are a strict subset of the 5 at `HEAD` — all pre-existing, none touched:
`test_grabAndDisplayADC`, `test_slowStart100VSwitchingSupply`,
`test_slowStart100VSwitchingSupply_with_exception`, `test_timerDitherEvent`.
`test_displayDAC` failed at `HEAD` and now passes.

Two tests were re-baselined because they encode the old 3.3 V DAC2 scale, and their new values were
checked against the exact expected factor `3.3*32768/65535 = 1.650033` rather than simply accepting
observed output:
- `XEM_GUI_MainWindow_test.py` — `q_dac_offset[2]` steps `1515/51` → `2500/83`. These now match DAC1's
  `2500/83` exactly, which is the meaningful check: DAC1 and DAC2 are both ±1 V SMA outputs with the
  same counts-per-volt and the same 65535-count span, so their slider steps must agree.
- `XEM_GUI_MainWindow_test.py` — detected DAC2 VCO gain `4.8e+09` → `8.0e+09` (scales by 1.650033).
  The step-size asserts in the same test are unchanged because the detected gain appears in their
  denominator too, so the two factors cancel.
- `gui_test.py::test_displayDAC` — DAC2 `1.254e-3 V` → `-0.99924 V`. The mock's DAC2 reading is ~24.9
  counts; ~25 counts out of 65535 is essentially the negative rail under offset binary.

### Numeric verification against `HEAD`

- `getDACGainInVoltsPerCounts(0)` and `(1)` are bit-identical before and after
  (`3.051850947600e-05`) — the VNA fix is a no-op for the signed DACs, as required.
- `getDACGainInVoltsPerCounts(2) == 1.0/32768` exactly.
- `convertDACCountsToVolts(2, [0, 32768, 65535])` → `[-1.0, 0.0, +0.999969]`, matching the firmware wire.
- The old VNA expression `convertDACCountsToVolts(2, 1)` returns `-0.999969`, confirming the magnitude
  and sign of the regression.

### Firmware re-verified, unchanged

`red_pitaya_top.v` was re-checked and is correct. `DACout2` is genuinely 16-bit unsigned
(`dpll_wrapper.v:1358-1380` computes at 17 bits signed then takes `[15:0]`);
`{~DACout2[15], DACout2[14:0]}` is exact offset binary and the widths sum to 16, so assigning it to a
`wire signed [15:0]` preserves the bit pattern with no extension hazard; `mux_internal_vco.vhd:14-15`
takes `std_logic_vector(15 downto 0)`, so the mixed-language bind is bit-exact; and `DACout1` /
`DACout2` are confirmed to be `pll1_output` and the slow-integrator sum respectively, so the
"shift down one connector" mapping is right.

## Files changed

```
Firmware_Vivado_Project/redpitaya.srcs/sources_1/imports/rtl/red_pitaya_top.v
digital_servo_python_gui/FreqErrorWindowWithTempControlV2.py
digital_servo_python_gui/SLLSystemParameters.py
digital_servo_python_gui/SpectrumWidget.py
digital_servo_python_gui/SuperLaserLand_JD_RP.py
digital_servo_python_gui/XEM_GUI3.py
digital_servo_python_gui/XEM_GUI_MainWindow.py
digital_servo_python_gui/system_parameters_RP_Default.xml
```

Added by the review follow-up:

```
digital_servo_python_gui/DisplayVNAWindow.py
digital_servo_python_gui/LoopFiltersUI_DAC1_and_DAC2.py
digital_servo_python_gui/XEM_GUI_MainWindow_test.py
digital_servo_python_gui/gui_test.py
```

No commit was made — changes remain unstaged on `dac0-dac2-unload-rerouting-plan`.

## Outstanding before this can be trusted on hardware

1. ~~Run the pytest suite (see plan §5).~~ **Done** in the review follow-up — 20 passed, 4 failed, all
   4 pre-existing at `HEAD`.
2. Rebuild the bitstream in Vivado and deploy it — nothing in this change takes effect until then.
3. Update the five site/unit-specific `system_parameters_RP_*.xml` files, which still carry daughterboard-era DAC2 limits (0–55V, or 0–2V for the CMC pair) and will clamp the new SMA output incorrectly. Table of current values is in plan.md.
4. Re-measure DAC2's VCO gain. `9e6 Hz/V` describes a 0–55V amplified HV drive; a direct ±1V SMA is a different transfer function, and `LoopFiltersUI_DAC1_and_DAC2.py` designs the loop filter from that number.
5. Bench-verify per plan §5 — in particular the second offset slider sweeping SMA DAC1 across −1V…+1V with mid-slider at ≈0V, which validates the offset-binary wire and the affine Python conversion together.
6. **`automated_test.py:323` still lists a `DAC2_100V` output** in the hardware-in-the-loop acceptance
   suite (`TestGUIController`). On a standalone board there is no 100 V rail to measure, so that step
   needs rethinking. Not touched here — it requires the `mux_board.py` hardware and site-specific
   config in `site_settings.py`, so it can't be validated in this environment.
7. **The remaining pytest failures pre-date all of this work** and were deliberately not fixed:
   `test_grabAndDisplayADC`, `test_slowStart100VSwitchingSupply`,
   `test_slowStart100VSwitchingSupply_with_exception`. Worth a separate look —
   the two `slowStart100VSwitchingSupply` ones fail on a missing attribute, which suggests the test and
   the code have drifted apart. `test_timerDitherEvent` is the fourth; it is order-dependent rather
   than flaky (see the third review pass below).

## Second review pass (2026-08-04)

An independent proof of the above, re-deriving rather than re-reading: the firmware wire, the four
DAC2 conversion functions, every conversion call site, and the test baseline against a stashed `HEAD`.

**The core work verified correct.** `{~DACout2[15], DACout2[14:0]}` is exact offset binary, is exactly
16 bits wide so the assignment to `wire signed [15:0]` has no extension hazard, and `mux_internal_vco.vhd`
does a plain `DACin(15 downto 2)` truncation with no arithmetic — the bit pattern reaches the SMA intact.
The Python side reproduces it exactly (0 → −1.0 V, 32768 → 0.0 V, 65535 → +0.999969 V, round-trip error
0.0, DAC0/DAC1 slopes bit-identical to `HEAD`). The independent call-site audit across all four
conversion functions found no further misuse beyond the four the first review pass had already fixed.

**Test tally "corrected" — wrongly; see the third review pass.** This pass reported baseline
**20 passed / 4 failed** → working tree **21 passed / 3 failed**, claiming `test_timerDitherEvent`
passes at `HEAD`. That measurement was taken with the test files run separately, which hides an
order dependency. The original 19/5 → 20/4 tally was correct. Net effect is unchanged in substance
either way: one test fixed, no regressions.

### Defects found and fixed in this pass

1. **Retiring PLL0 was incomplete — the CEO subsystem was still live on the wire.** The claim above
   that the disabled tab "becomes inert on its own" was wrong. `self.xem_gui_mainwindow` kept running
   its 100 ms dither-readback timer and `self.freq_error_window1` kept a 500 ms timer calling
   `read_dual_mode_counter(0)` and writing CEO beat/DAC log files to disk indefinitely, for a lock that
   no longer exists. Added `controller.killRetiredCEOTimers()` in `XEM_GUI3.py`, called from four
   sites: after the tab-disable block, and at the end of `pushDefaultValues()`, `getActualValues()`
   and `pushActualValues()`. The last three are essential, not belt-and-braces — each loops over a
   `target_windows` list containing both retired windows and calls `pushDefaultValues`/`getValues`/
   `pushActualValues` on them, every one of which calls `startTimers()` again, so a one-shot kill at
   construction would be silently undone on the first reconnect. Also cleared `self.timerID` after
   `killTimer()` in `FreqErrorWindowWithTempControlV2.killTimers()`, since repeated kills of a stale
   Qt timer id can collide with a reused id belonging to a live window.
2. **`SLLSystemParameters.populateDefaults()` created an unreachable element.** It appended
   `ET.Element('VCO gain', ...)` — with a space — while every reader calls `getValue('VCO_gain', ...)`
   and every `system_parameters_RP_*.xml` on disk uses `VCO_gain`, so `tree.find()` returned `None`
   and the lookup raised `AttributeError`. Long-standing latent bug, and it meant the DAC2
   re-measurement note added in the first pass sat on a dead element. Renamed to `VCO_gain`; verified
   `getValue('VCO_gain', 'DAC0')` now returns `2e8` and that save/load round-trips. The remaining ~18
   element names in `populateDefaults()` were audited three-way (creator / readers / on-disk XML) and
   all agree.
3. **Label collision.** `SpectrumWidget.dac_display_names[0]` read `'DAC 0'`, which after the remap
   means something different from `'SMA DAC0'` (= internal DAC1) elsewhere in the same application.
   Now `'DAC0 (retired)'`. Stale "DAC2 HV" / "DAC 1 HV" daughterboard-era comments in
   `LoopFiltersUI_DAC1_and_DAC2.py:229-230` reworded to the SMA names.

Test tally after these fixes, re-run serially: **21 passed / 3 failed / 4 skipped** — unchanged, with
the same three pre-existing failures.

### Additional note for bench work

**Power-on output level changed and is not documented above.** Before the GUI pushes a manual offset,
`DACout2 = 0`. On the daughterboard that was the bottom of a 0–3.3 V unipolar span; on the SMA it is
**−1 V**. Same "bottom of span" semantics, different absolute voltage into whatever gets cabled to
SMA DAC1 — worth knowing before step 5 above.

**Thermometer ticks vs. range on un-migrated sites.** `update_dac_thermo_scales()` derives the widget
range from `DACs_limit_*`, but the ticks are now hardcoded ±1 V. With the five site XMLs still at DAC2
`0`–`55` V (→ counts 32768–65535 after clamping), the widget draws a 0…+1 V range against −1…+1
labels. Cosmetic, but it reinforces that outstanding item 3 is not optional: those files also silently
halve the usable output range to 0…+1 V.

## Third review pass (2026-08-04)

Another independent proof, re-deriving the firmware bit path and the Python numerics rather than
re-reading the two passes above. **No new defects found in the change itself.** Everything the first
two passes fixed is confirmed correct, and the four conversion functions' call sites were audited a
third time independently with no further misuse.

### The missing link in the firmware proof

Both earlier passes traced `DACout2` → `{~DACout2[15], DACout2[14:0]}` → `mux_internal_vco`'s
`DACin1(15 downto 2)` truncation and stopped there. There is one more stage:
`red_pitaya_top.v:471-472` registers the DAC outputs as `dac_dat_a <= {dac_a[13], ~dac_a[12:0]}` —
the stock Red Pitaya sign-bit-plus-inverted-magnitude output encoding. It is applied identically to
both channels, and SMA DAC1 was already carrying a signed `DACout1` through it, so the remap is
unaffected. Verifying this closes the last gap: `DACout2 = 0` → offset-binary `0x8000` → 14-bit
`-8192` → −1 V at the connector, exactly as claimed.

### Test tally, measured a third time

Run as a single pytest invocation over
`gui_test.py XEM_GUI_MainWindow_test.py RP_PLL_test.py SuperLaserLand_JD_RP_test.py`
(`QT_QPA_PLATFORM=offscreen`, `frog` env, numpy 1.24.3), stashing the whole working tree for the
baseline:

| | stashed `HEAD` | working tree |
|---|---|---|
| passed | 19 | 20 |
| failed | 4 + `test_displayDAC` = 5 | 4 |

The **first** pass's 19/5 → 20/4 was right; the second pass's correction to 20/4 → 21/3 was wrong.
`test_timerDitherEvent` is **order-dependent, not flaky**: it passes when `XEM_GUI_MainWindow_test.py`
runs alone and fails whenever `gui_test.py` runs first in the same session — at `HEAD` and after this
work alike, so it is neither a regression nor a fix. Running the files separately (as the second pass
did) hides it. Lesson for future passes: always measure the baseline and the working tree with the
*same* pytest invocation.

### A non-defect, recorded so it isn't re-raised

`convertDACCountsToVolts(2, …)` is affine, and DAC2 logger samples are `np.uint16`
(`SuperLaserLand_JD_RP.py:875`), so `counts - 2**15` would wrap to the wrong sign if the arithmetic
ever ran on the raw unsigned array. It does not: lines 1280–1285 of the function already cast both
ndarray and scalar inputs to float first (with `np.float` restored by the module-level
`np.float = float` shim at line 27). Verified directly — a raw `uint16` array in gives
`[-1.0, …, +0.99997]` out. **Do not "fix" this**; but do not remove the cast either.

### Minor items left unfixed

- `XEM_GUI_MainWindow.py:423` carries the same `('ADC0','ADC1','DAC0','DAC1','DAC2')` LOGGER_MUX
  selector list that `SpectrumWidget.py:158-161` got a "must not be renamed" guardrail comment for,
  but has no such comment. Same trap, half-documented.
- `killRetiredCEOTimers()`'s bare `except Exception: print(...)` silently absorbs failures in
  something that must re-apply on every reconnect; a rename or typo would degrade to "the retired
  CEO timers quietly come back" with only a console line.
- `SLLSystemParameters.sendToFPGA()` still calls `slowStart100VSwitchingSupply()` unconditionally —
  harmless with no daughterboard attached (it drives the watchdog-protected PWM on `exp_n_out[2]`),
  but it is the last daughterboard-era action left in the startup path and is not in the
  outstanding-items list above.