# Phase-direct-feed: repurpose `select_phase_or_freq0/1` into a DDC phase/frequency loop-filter input select

## Context

`docs/phase-direct-feed-angleselect-proposal.md` documents a dead pair of
register bits — `select_phase_or_freq0`/`select_phase_or_freq1` in
`dpll_wrapper.v` (register `0x8002`, bits 4-5), mirrored by
`residuals0_phase_or_freq`/`residuals1_phase_or_freq` in
`SuperLaserLand_JD_RP.py` — left over from a superseded `residuals_streaming`
feature. They are written every time the DDC filter/angle-select register is
updated, but on the live bitstream they drive nothing (their only reader is
inside fully-commented-out code).

The goal is to repurpose these bits so each PLL channel's loop filter can be
fed the DDC's phase output (`wrapped_phase0`/`wrapped_phase1`) directly
instead of its normal, differentiated frequency output
(`inst_frequency0`/`inst_frequency1`) — giving a classic analog-style
phase-detector lock option alongside the existing CORDIC/I/Q frequency-locking
modes. This is a first cut per the design doc: wrap-safety and gain-retuning
hazards (§2 of the doc) are accepted as out of scope / a bench-test concern,
not addressed by this change.

All firmware/code-read facts below were verified directly against the current
files in this session (not taken verbatim from the doc).

## 1. Firmware — `Firmware_Vivado_Project/redpitaya.srcs/sources_1/DigitalPLL/dpll_wrapper.v`

**Rename** (repurposing, approved): `select_phase_or_freq0`/`select_phase_or_freq1` → `ddc0_phase_direct_select`/`ddc1_phase_direct_select`.

- Line 545 declaration:
  ```verilog
  wire ddc0_phase_direct_select, ddc1_phase_direct_select;
  ```
- Line 584, register packing (bit order unchanged — only identifiers renamed):
  ```verilog
  .register_output({ddc1_phase_direct_select, ddc0_phase_direct_select, ddc1_filter_select, ddc0_filter_select}),
  ```
- The only other references to the old names are inside fully-commented-out code (`residuals_streaming1`/`residuals_streaming2` instantiations, lines ~1571/1589) — rename there too for grep-cleanliness (no functional effect either way). Do **not** touch `residuals_streaming.vhd` or its testbench — their `select_phase_or_freq` is an unrelated port name on a different, superseded module.

**Channel 0 mux** — insert directly above the `PLL0_loop_filters` instantiation (before line 935's `// Finally the PLL itself:` comment), and change its `data_in` port:
```verilog
wire [9:0] pll0_loop_filter_data_in;
assign pll0_loop_filter_data_in = ddc0_phase_direct_select ? wrapped_phase0 : inst_frequency0;
```
then change line 947 from `.data_in(inst_frequency0),` to `.data_in(pll0_loop_filter_data_in),`.

**Channel 1 mux** — PLL1 already has a 3-to-1 mux (`loop_filters_1_mux`, register `0x9000`) selecting `PLL1_loop_filters.data_in` between `DDC1_output` (DDC1's own frequency), `inst_frequency0` (cross-feed from PLL0's DDC), and `pll0_output >> 5` (cascade mode). Per your decision, the phase-direct override applies **only to DDC1's own branch** (`in0_mux`), leaving the cross-feed/cascade modes untouched. Insert before line 768 (`loop_filters_1_mux` instantiation):
```verilog
wire [9:0] pll1_loop_filter_freq_or_phase_in0;
assign pll1_loop_filter_freq_or_phase_in0 = ddc1_phase_direct_select ? wrapped_phase1 : DDC1_output;
```
then change line 771 from `.in0_mux (DDC1_output ),` to `.in0_mux (pll1_loop_filter_freq_or_phase_in0 ),`. `PLL1_loop_filters.data_in` itself (line 1081, reads `inst_frequency1`) is unchanged.

**Why the ternary (`? :`) is the right tool**: this is exactly Verilog's equivalent of Python's `x = a if cond else b` for combinational logic — a plain `assign` with a ternary, same style already used elsewhere in this file (e.g. the `multiplexer_3to1_async` mux it sits beside). `np.where`-style vectorized selection has no bearing here since Verilog vectors are native bit ranges, not arrays needing elementwise selection — the scalar-condition ternary already applies across the full `[9:0]` width with no extra work. No new module or generate block is needed.

**Verified safe**: `wrapped_phase0`, `wrapped_phase1`, `inst_frequency0`, `DDC1_output` are each driven exactly once (their DDC instance's output port) and are all `[9:0]` — no width mismatch, no multiple-driver conflict, no combinational loop (the new wires only fan out into `.data_in`/`.in0_mux`, never feed back upstream).

## 2. Register/comms layer — `digital_servo_python_gui/SuperLaserLand_JD_RP.py`

- Lines 329-330 (`__init__`): rename `self.residuals0_phase_or_freq`/`self.residuals1_phase_or_freq` → `self.ddc0_phase_direct_select`/`self.ddc1_phase_direct_select`, still initialized to `0`.
- Lines 1789-1801, `set_ddc_filter()`: add a new trailing parameter, following the same pattern as `angle_select`, so it remains the single implementation method that writes phase-direct state into `self.sl`-equivalent attributes (per your instruction to keep this consistent with how filter/angle select already work):
  ```python
  def set_ddc_filter(self, adc_number, filter_select, angle_select = 0, phase_direct_select = 0):
      ...
      if adc_number == 0:
          self.ddc0_filter_select = filter_select
          self.ddc0_angle_select = angle_select
          self.ddc0_phase_direct_select = phase_direct_select
      elif adc_number == 1:
          self.ddc1_filter_select = filter_select
          self.ddc1_angle_select = angle_select
          self.ddc1_phase_direct_select = phase_direct_select
      self.set_ddc_filter_select_register()
  ```
- Lines 1803-1815, `set_ddc_filter_select_register()`: rename only, same shifts/bit positions:
  ```python
  register_value = self.ddc0_filter_select + (self.ddc1_filter_select<<2) + (self.ddc0_phase_direct_select<<4) + (self.ddc1_phase_direct_select<<5)
  ```
- Lines 1817-1825, `get_ddc_filter_select()`: extend to also unpack bits 4-5 and return a 4-tuple, per your decision to add full readback:
  ```python
  def get_ddc_filter_select(self):
      data = self.read_RAM_dpll_wrapper(self.BUS_ADDR_ddc_filter_select)
      self.ddc0_filter_select = (data   ) & int('11', 2)
      self.ddc1_filter_select = (data>>2) & int('11', 2)
      self.ddc0_phase_direct_select = (data>>4) & 1
      self.ddc1_phase_direct_select = (data>>5) & 1
      return (self.ddc1_filter_select, self.ddc0_filter_select, self.ddc1_phase_direct_select, self.ddc0_phase_direct_select)
  ```

## 3. GUI — `digital_servo_python_gui/DisplayDividerAndResidualsStreamingSettingsWindow.py`

**New widgets** in `initUI()`: a radio-button *pair* per channel ("Frequency"/"Phase"), each in its own new `QButtonGroup` — matches the visual pattern of existing paired options (quadrature MSB/LSB, in-phase MSB/LSB) and avoids the un-checkable-toggle problem a lone radio button would have. Use distinctly-named groups (`qddc0_phase_group`/`qddc1_phase_group`), not the existing `qddc0_group`/`qddc1_group` (which are already reused/shadowed between the filter-BW and angle-select rows — don't add to that pattern).

Inserted after the DDC0 angle-select block (after current line 222):
```python
self.qlbl_ddc0phase = Qt.QLabel('DDC 0 loop filter input:')
self.qchk_freq0 = Qt.QRadioButton('Frequency')
self.qchk_phase0 = Qt.QRadioButton('Phase')
self.qddc0_phase_group = Qt.QButtonGroup(self)
self.qddc0_phase_group.addButton(self.qchk_freq0)
self.qddc0_phase_group.addButton(self.qchk_phase0)

self.qchk_freq0.setChecked(True)
self.qchk_phase0.setChecked(False)

self.qchk_freq0.clicked.connect(self.ddcClicked)
self.qchk_phase0.clicked.connect(self.ddcClicked)
```
Mirrored for DDC1 (`..._1`/`..._phase1`) after the DDC1 angle-select block (after current line 264). Default = Frequency checked, matching the `0` default in `SuperLaserLand_JD_RP.__init__` — startup / "push defaults" sends bit-identical register writes to today's behavior until a user flips a Phase button.

**Grid layout** — new row inserted immediately after each channel's inphase_msb/lsb row, cols 2-3 only (col1 left empty, label in col0):

| row | col0 | col1 | col2 | col3 |
|---|---|---|---|---|
| 0 | DDC0 filter label | Wideband0 | Narrowband0 | WidebandFIR0 |
| 1 | DDC0 angle label | cordic0 | quad_msb0 | quad_lsb0 |
| 2 | *(empty)* | *(empty)* | inphase_msb0 | inphase_lsb0 |
| **3 (new)** | `qlbl_ddc0phase` | *(empty)* | `qchk_freq0` | `qchk_phase0` |
| 4 | DDC1 filter label | Wideband1 | Narrowband1 | WidebandFIR1 |
| 5 | DDC1 angle label | cordic1 | quad_msb1 | quad_lsb1 |
| 6 | *(empty)* | *(empty)* | inphase_msb1 | inphase_lsb1 |
| **7 (new)** | `qlbl_ddc1phase` | *(empty)* | `qchk_freq1` | `qchk_phase1` |

i.e. today's DDC1 block (rows 3-5) shifts down to rows 4-6, and the new row 7 is appended at the end. Update all the `grid.addWidget(...)` row indices for the DDC1 block accordingly.

**`ddcClicked()`** (lines 133-173): add a 2-way check per channel right alongside the existing `angle_select` if/elif block, and thread it into the now-4-argument `set_ddc_filter()` call:
```python
if self.qchk_phase0.isChecked():
    phase_direct_select = 1
else:
    phase_direct_select = 0
self.sl.set_ddc_filter(adc_number, filter_select, angle_select, phase_direct_select)
```
(and the same for channel 1). Plain `if/else` is correct here (not `if/elif`) since a `QButtonGroup` guarantees exactly one of the two is always checked once initialized.

**`getValues()`** (lines 88-131, hardware readback): update to unpack the new 4-tuple from `get_ddc_filter_select()` and set the new radio buttons accordingly:
```python
(filter_select_1, filter_select_0, phase_direct_select_1, phase_direct_select_0) = self.sl.get_ddc_filter_select()
...
if phase_direct_select_0 == 1:
    self.qchk_phase0.setChecked(True)
else:
    self.qchk_freq0.setChecked(True)
```
(mirrored for channel 1).

**`loadParameters()`** (lines 42-86, saved-config readback): add a matching `Phase_direct_select` XML key lookup, following the existing `Filter_select`/`Angle_select` pattern — see §4 below for the backward-compatibility guard this needs, since old saved config files on real boxes in the field won't have this key.

## 4. Saved-config schema — `SLLSystemParameters.py` + bundled `system_parameters_*.xml` files

`loadParameters()` reads settings via `self.sp.getValue(key, param)`, which does
`self.tree.find(strKey).attrib[strParameter]` — this **raises `AttributeError`
if the XML element doesn't exist** (`.find()` returns `None`). Since this repo
ships to physical boxes with their own saved config files in the field, adding
a new `Phase_direct_select` lookup to `loadParameters()` must not crash when
loading a pre-existing config file that predates this change.

- Add the new default element to `SLLSystemParameters.py`'s `populateDefaults()` (next to line 56-57's `Filter_select`/`Angle_select`):
  ```python
  self.root.append(ET.Element('Phase_direct_select', DAC1='0', DAC0='0'))
  ```
- Add `<Phase_direct_select DAC0="0" DAC1="0" />` to all 7 bundled `system_parameters_*.xml` files (RPD, RP_8bits, RP_CMC2, RP_1, RP_CMC1, RP_C1, RP_Default), next to their existing `Filter_select`/`Angle_select` lines, so the repo's own configs stay internally consistent.
- In `loadParameters()`, guard the new lookup so a config file from before this change (in the repo or already saved on a user's machine) degrades gracefully to the default (Frequency mode) instead of crashing:
  ```python
  try:
      phase_direct_select_0 = int(self.sp.getValue('Phase_direct_select', "DAC0"))
  except AttributeError:
      phase_direct_select_0 = 0
  ```
  (and the DAC1 equivalent). This is a small, local guard — no new generic API needed on `SLLSystemParameters`.

## 5. Verification

- **Firmware**: no Vivado/board available in this sandbox — limited to static inspection (already done: widths match, no multi-driver/loop issues, syntax mirrors existing patterns in the file). Real verification requires synthesizing, flashing a board, and bench-testing at reduced loop-filter gain per the design doc's open items — out of scope here.
- **Python register layer**: add a test to `digital_servo_python_gui/SuperLaserLand_JD_RP_test.py` following its existing style (`SuperLaserLand_mock()` instance). Since `SuperLaserLand_mock` doesn't override `send_bus_cmd_16bits` (it would otherwise try to hit a real device handle), monkeypatch it on the instance to capture the packed register value, then call `sl.set_ddc_filter(0, 0, 0, phase_direct_select=1)` / `sl.set_ddc_filter(1, 0, 0, phase_direct_select=1)` and assert bit 4 / bit 5 of the captured value, plus a default-args case asserting those bits are 0.
- **GUI**: run `pytest gui_test.py` / `XEM_GUI_MainWindow_test.py` (existing suites already instantiate real `QtWidgets.QApplication` instances against a mocked hardware layer) to confirm nothing regresses when the DDC settings window is constructed; manually exercise the new radio buttons if a Qt display is available (click Phase0/Phase1, confirm `sl.ddc0_phase_direct_select`/`ddc1_phase_direct_select` update and an unrelated click, e.g. a filter-BW button, doesn't stomp them back to 0).
- Final grep check after all edits: `residuals0_phase_or_freq|residuals1_phase_or_freq` and `select_phase_or_freq0|select_phase_or_freq1` should return zero hits in `digital_servo_python_gui/` and `Firmware_Vivado_Project/redpitaya.srcs/sources_1/DigitalPLL/dpll_wrapper.v` respectively (aside from the intentionally-untouched `residuals_streaming.vhd`/testbench).
