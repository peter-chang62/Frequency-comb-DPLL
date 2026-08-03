# Report: phase-direct-feed implementation

**Date:** 2026-08-03
**Branch:** `Peter-edits-adding-phase-lock-option`
**Plan:** see [plan.md](plan.md) in this folder

## Request

Repurpose the dead `select_phase_or_freq0`/`select_phase_or_freq1` register
bits (background in `docs/phase-direct-feed-angleselect-proposal.md`) so each
PLL channel's loop filter can be fed the DDC's phase output directly instead
of its frequency output, then wire this through to the Python GUI as two new
radio-button controls in the DDC settings widget.

## Decisions made during planning (via clarifying questions)

1. **PLL1 mux placement**: the phase-direct override was applied only to the
   `in0_mux` branch of PLL1's existing 3-to-1 mux (register `0x9000`), not as
   a final override after that mux — so the cross-feed (`inst_frequency0`)
   and cascade (`pll0_output>>5`) modes on channel 1 are unaffected by this
   feature.
2. **Widget type**: a pair of `QRadioButton`s ("Frequency"/"Phase") per
   channel, each in its own new `QButtonGroup`, rather than a checkbox or a
   lone radio button — matches the existing visual pattern of paired options
   in the DDC settings grid (e.g. quadrature MSB/LSB) and avoids the
   un-checkable-toggle issue a lone `QRadioButton` would have.
3. **Signal rename**: `select_phase_or_freq0/1` (Verilog) and
   `residuals0/1_phase_or_freq` (Python) were renamed to
   `ddc0/1_phase_direct_select` on both sides, since the old names referenced
   the now-dead `residuals_streaming` feature.
4. **Readback scope**: extended beyond a write-only change — added full
   readback (`get_ddc_filter_select()`, `getValues()`, `loadParameters()`) so
   the new controls sync from hardware and from saved config files, not just
   from GUI clicks.

## Changes made

### Firmware — `Firmware_Vivado_Project/redpitaya.srcs/sources_1/DigitalPLL/dpll_wrapper.v`
- Renamed `select_phase_or_freq0/1` → `ddc0/1_phase_direct_select` (declaration, register packing at `0x8002`, and the dead-code comment references inside the commented-out `residuals_streaming` instantiations).
- Added `pll0_loop_filter_data_in = ddc0_phase_direct_select ? wrapped_phase0 : inst_frequency0`, wired into `PLL0_loop_filters.data_in`.
- Added `pll1_loop_filter_freq_or_phase_in0 = ddc1_phase_direct_select ? wrapped_phase1 : DDC1_output`, wired into `loop_filters_1_mux.in0_mux` (channel 1's cross-feed/cascade mux inputs are untouched).

### Register layer — `digital_servo_python_gui/SuperLaserLand_JD_RP.py`
- Renamed `residuals0/1_phase_or_freq` → `ddc0/1_phase_direct_select` in `__init__`.
- `set_ddc_filter()` gained a `phase_direct_select=0` trailing parameter, setting the corresponding per-channel attribute.
- `set_ddc_filter_select_register()` packing formula updated to the new attribute names (bit positions unchanged).
- `get_ddc_filter_select()` extended to also read back bits 4-5 and return a 4-tuple `(ddc1_filter_select, ddc0_filter_select, ddc1_phase_direct_select, ddc0_phase_direct_select)`.

### GUI — `digital_servo_python_gui/DisplayDividerAndResidualsStreamingSettingsWindow.py`
- Added a Frequency/Phase `QRadioButton` pair per channel (`qchk_freq0/phase0`, `qchk_freq1/phase1`), each in a new `QButtonGroup` (`qddc0/1_phase_group`), defaulting to Frequency.
- Grid layout: inserted a new row after each channel's inphase MSB/LSB row (new row 3 for DDC0, new row 7 for DDC1 after DDC1's block shifted down), occupying columns 2-3.
- `ddcClicked()`: added an `isChecked()` check per channel, threaded into the now 4-argument `set_ddc_filter()` call.
- `getValues()`: unpacks the new 4-tuple from `get_ddc_filter_select()` and sets the Frequency/Phase radio buttons from hardware state.
- `loadParameters()`: reads a new `Phase_direct_select` XML key, guarded with `try/except AttributeError` so a config file saved before this change (in the repo or already on a user's machine) degrades to the default (Frequency) instead of crashing.

### Saved-config schema
- `digital_servo_python_gui/SLLSystemParameters.py`: added `Phase_direct_select` (`DAC0='0'`, `DAC1='0'`) to `populateDefaults()`.
- Added the same `<Phase_direct_select DAC0="0" DAC1="0" />` element to all 7 bundled config files: `system_parameters_RPD.xml`, `system_parameters_RP_1.xml`, `system_parameters_RP_8bits.xml`, `system_parameters_RP_C1.xml`, `system_parameters_RP_CMC1.xml`, `system_parameters_RP_CMC2.xml`, `system_parameters_RP_Default.xml`.

### Tests
- Added `test_ddc_phase_direct_select_register_bits()` to `digital_servo_python_gui/SuperLaserLand_JD_RP_test.py`: monkeypatches `send_bus_cmd_16bits` on a `SuperLaserLand_mock` instance and asserts bit 4 (channel 0) / bit 5 (channel 1) of the packed `0x8002` register value are set/clear correctly, including the default (`phase_direct_select` omitted) case.

## Verification performed

- `pytest SuperLaserLand_JD_RP_test.py` — **2 passed** (existing test + new phase-direct-select test), using `C:\WinPython-64bit-3.6.1.0Qt5\python-3.6.1.amd64\python.exe`.
- `pytest gui_test.py` — 6 passed, 2 failed. Confirmed by stashing all changes and re-running against the unmodified baseline that these same 2 failures pre-exist (an unrelated float/int display-rounding assertion and a Qt teardown `ReferenceError` in `pyqtgraph` cleanup) — not caused by this change.
- `pytest XEM_GUI_MainWindow_test.py` — 12 passed, 2 failed (`AttributeError: 'XEM_GUI_MainWindow' object has no attribute 'slowStart100VSwitchingSupply'`) — unrelated to any file touched in this session; the 12 passing tests include construction of the main window that owns the modified DDC settings widget, confirming no regression there.
- Grep check: zero remaining hits for `residuals0_phase_or_freq|residuals1_phase_or_freq` in `digital_servo_python_gui/`, and zero for `select_phase_or_freq0|select_phase_or_freq1` in `dpll_wrapper.v`.
- Firmware changes were statically reviewed (widths, single-driver, no combinational loops) but **not synthesized or bench-tested** — no Vivado toolchain or physical board available in this environment. Per the design doc's open items, bench verification at reduced loop-filter gain is still required before trusting phase-direct mode at full bandwidth.

## Files changed

```
Firmware_Vivado_Project/redpitaya.srcs/sources_1/DigitalPLL/dpll_wrapper.v
digital_servo_python_gui/DisplayDividerAndResidualsStreamingSettingsWindow.py
digital_servo_python_gui/SLLSystemParameters.py
digital_servo_python_gui/SuperLaserLand_JD_RP.py
digital_servo_python_gui/SuperLaserLand_JD_RP_test.py
digital_servo_python_gui/system_parameters_RPD.xml
digital_servo_python_gui/system_parameters_RP_1.xml
digital_servo_python_gui/system_parameters_RP_8bits.xml
digital_servo_python_gui/system_parameters_RP_C1.xml
digital_servo_python_gui/system_parameters_RP_CMC1.xml
digital_servo_python_gui/system_parameters_RP_CMC2.xml
digital_servo_python_gui/system_parameters_RP_Default.xml
```

No commit was made — changes remain unstaged on `Peter-edits-adding-phase-lock-option`.
