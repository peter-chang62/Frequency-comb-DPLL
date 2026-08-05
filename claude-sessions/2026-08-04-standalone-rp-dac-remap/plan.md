# Standalone Red Pitaya build: retire PLL0/DAC0, remap DAC1→SMA DAC0 and DAC2→SMA DAC1

## Context

The box today is a Red Pitaya + daughterboard. The daughterboard carries a MAX5541 16-bit SPI DAC fed over the digital expansion pins (0–3.3V), amplified to a high-voltage rail — that is the signal the codebase calls **DAC2**. The Red Pitaya's own two SMA outputs (±1V, 14-bit signed) are **DAC0** and **DAC1**.

The goal is to run the Red Pitaya **standalone**, with no daughterboard. That leaves only two physical outputs, so the two-PLL layout has to collapse to one:

- **PLL0 / DAC0** (the CEO / f_ceo lock) is retired — its tab stays visible but drives nothing.
- **PLL1** (the optical lock) survives unchanged. Its two internal outputs get new physical homes:
  - internal **DACout1** (fast) → **SMA DAC0**
  - internal **DACout2** (slow) → **SMA DAC1**

To the operator this reads as: one output disappeared, and the remaining two moved down one connector. They re-cable DAC0 to whatever previously took DAC1, and DAC1 to whatever previously took DAC2. On the CEO lock tab, controls stop producing any output voltage swing.

### Why this cannot be done in Python alone

`DACout2` never reaches an SMA. It leaves the FPGA as an SPI bit-stream via `max5541_spi_dac_interface` (`red_pitaya_top.v:1096`) onto `exp_p_out`/`exp_n_out`. The only path to the SMAs is `mux_internal_vco`, which is hardwired to `DACout0`/`DACout1` and exposes no runtime-selectable source (`mux_internal_vco.vhd` — its one register is `selector_vco`, which picks which channel gets the internal VCO, not which signal feeds a channel). So the remap requires an edit to `red_pitaya_top.v` and a Vivado rebuild of the `.bit`.

### Decisions taken during planning (via clarifying questions)

1. **Firmware rebuild is in scope** — Vivado is available.
2. **Offset-binary, full span** for the DAC2 → SMA mapping: counts 0 → −1V, 32768 → 0V, 65535 → +1V. Chosen specifically so the entire DAC2 datapath in firmware stays bit-identical (both integrators, `dac2_error_computation`, railing, limits); the conversion becomes a single wire. Python's DAC2 volts↔counts conversion becomes affine to match, so the GUI reports real SMA volts rather than the old fictitious 0–3.3V scale.
3. **Relabel the GUI and visibly disable the CEO tab** — rather than leaving labels stale and relying on the operator to remember the cabling.

### Constraint held throughout

Internal register indices stay `1` and `2`; only user-visible labels say SMA DAC0/DAC1. Renaming the indices would mean rewriting the register map in three independently maintained implementations (VHDL, `monitor-tcp.c`, Python) with no schema tying them together — not worth the risk for a cosmetic gain. Every edited site carries a comment noting the deliberate mismatch.

## 1. Firmware — `Firmware_Vivado_Project/redpitaya.srcs/sources_1/imports/rtl/red_pitaya_top.v`

`mux_internal_vco` takes signed-16 in and emits `DACin(16-1 downto 2)` — the top 14 bits — to the SMAs. Add above the instantiation:

```verilog
wire signed [15:0] DACout2_signed = {~DACout2[15], DACout2[14:0]};  // 0..65535 unsigned -> -32768..32767 signed
```

and change two ports:

```verilog
.DACin0 ( DACout1        ),  // optical-lock fast output -> SMA DAC0
.DACin1 ( DACout2_signed ),  // optical-lock slow output -> SMA DAC1
```

Do **not** touch: the `max5541_spi_dac_interface` instantiation or its `exp_p_out`/`exp_n_out` assignments (keep the daughterboard path wired so a board can still be reattached); the `duty_cycle_protector` / `osc_output` HV-switcher protection; `mux_internal_vco.vhd` itself; any `.bd` or `.xdc`. Leave PLL0 logic in `dpll_wrapper.v` intact — `DACout0` simply becomes unconnected and Vivado will trim it.

## 2. Python scaling — `digital_servo_python_gui/SuperLaserLand_JD_RP.py`

Replace `Vref_DAC2 = 3.3` with the new mapping constants:

```python
DAC2_fullscale_volts = 1.0   # SMA spans -1V..+1V; the 16-bit unsigned DAC2 datapath maps 0..65535 onto it
DAC2_midscale_counts = 2**15
```

Update the four DAC2 branches — two affine, two pure slopes:

- `convertDACCountsToVolts`: `(counts - mid) / mid * fullscale`
- `convertDACVoltsToCounts`: `voltage / fullscale * mid + mid`
- `getDACGainInCountsPerVolts`: `mid / fullscale` (slope — **no** offset)
- `getDACGainInVoltsPerCounts`: `fullscale / mid` (slope — **no** offset)

`DAC2_gain = 2` is unused; annotate rather than delete.

**Required audit:** `convertDACVoltsToCounts` becomes affine where it was previously proportional for DAC2. Every call site must be checked — absolute voltages (limits, setpoints, manual offsets) are correct under the affine form; a voltage *difference* or a gain would be a bug and must use `getDACGainInCountsPerVolts` instead.

## 3. Config defaults

`SLLSystemParameters.py` `populateDefaults()` and `system_parameters_RP_Default.xml`, DAC2 entries only: limits → `-1`/`1`, offset → `0`. Annotate the DAC2 VCO gain as needing re-measurement. Leave DAC0/DAC1 alone, and leave the site/unit-specific XML files alone (see Follow-ups).

## 4. GUI — labels and tab state only

- `XEM_GUI3.py` (~line 191): retitle the CEO tab "CEO Lock (disabled)", `setTabEnabled(..., False)`, add an explanatory tooltip, and make Optical Lock the initially-selected tab. Verify nothing else (timers, `closeEvent`, `setCustomShorthand`) depends on that tab being selectable.
- `XEM_GUI_MainWindow.py:750`: `'VCO Gains (DAC1, DAC2HV) [Hz/V]:'` → `'VCO Gains (SMA DAC0, SMA DAC1) [Hz/V]:'`.
- `FreqErrorWindowWithTempControlV2.py`: checkbox labels `'DAC1'`/`'DAC2'` → `'SMA DAC0'`/`'SMA DAC1'`; the two-output plot-title format string (~line 783) likewise. Leave the `output_number == 0` title (~line 768) alone — that path is now dead.
- **Guardrail:** display strings only. Do not rename `self.DAC2_history`, `self.qchk_show_DAC2`, `self.curve_dac2`, or the `self.output_files['DAC2']` dict keys — those keys generate the binary log filenames that `load_logging_data.py:66` parses back.
- `SpectrumWidget.py:156`: leave `['ADC0','ADC1','DAC0','DAC1','DAC2']` **unchanged**. These are raw firmware-datapath selectors, parsed back by `SuperLaserLand_JD_RP.scaleADCorDACDataToVolts` via `int(input_select[3])` and keyed into `LOGGER_MUX`. Add a comment saying so.

## 5. Verification

```bash
cd digital_servo_python_gui
python -m py_compile XEM_GUI3.py XEM_GUI_MainWindow.py FreqErrorWindowWithTempControlV2.py SpectrumWidget.py SuperLaserLand_JD_RP.py SLLSystemParameters.py
pytest gui_test.py XEM_GUI_MainWindow_test.py SuperLaserLand_JD_RP_test.py RP_PLL_test.py
```

Triage any failure against `git stash` to establish whether it pre-dates these changes; do not fix pre-existing breakage as part of this work.

Numerically confirm the Python conversion agrees with the firmware wire at the endpoints and midpoint (0 → −1V, 32768 → 0V, 65535 → +1V) and that the round-trip is lossless.

### Firmware rebuild and deploy

Per `Firmware_Vivado_Project/peter_firmware_notes.txt`: regenerate IP output products (`reset_target all [get_files *.bd]` / `generate_target all [get_files *.bd]`) and implement with strategy `Performance_ExplorePostRoutePhysOpt`. Push the resulting `.bit` with the "reprogram FPGA" button in `initialConfiguration_RP.py` — no SSH needed, see `docs/external-clock-and-hv-switcher-protection.md`.

### Bench verification (requires hardware)

1. Lock off — sweep the optical tab's **first** manual offset slider; confirm swing on **SMA DAC0**, and confirm the CEO tab's controls produce nothing anywhere.
2. Sweep the **second** (ex-DAC2) slider; confirm SMA DAC1 sweeps monotonically across roughly −1V…+1V and that the GUI's reported volts track the scope, with mid-slider at ≈0V. This is the highest-value single check — it validates the offset-binary wire and the new affine Python conversion at once.
3. Close the optical lock and confirm the acquisition sequence still works: the DAC2 frequency-locked loop grabs the beat, then hands off to the DAC1 PLL plus the DAC2 second integrator (the `qradio_mode_slow` → `qradio_mode_both` transition, `XEM_GUI_MainWindow.py:577-593`). The second integrator centers DACout1 against `positive/negative_limit_dac1`, so SMA DAC0 should settle near mid-range while SMA DAC1 absorbs the DC drift.

## Follow-ups left to the user

**Site/unit-specific config files are deliberately not touched.** Each still carries daughterboard-era DAC2 values and will clamp the new SMA output incorrectly:

| File | VCO_gain DAC2 | limits_low | limits_high | offset |
|---|---|---|---|---|
| `system_parameters_RP_1.xml` | 9e6 | 0 | 55 | 0 |
| `system_parameters_RP_8bits.xml` | 9e6 | 0 | 55 | 0 |
| `system_parameters_RP_C1.xml` | 9e6 | 0 | 55 | 0 |
| `system_parameters_RP_CMC1.xml` | 4e8 | 0 | 2 | 1 |
| `system_parameters_RP_CMC2.xml` | 6.5e8 | 0 | 2 | 1 |

**DAC2's VCO gain must be re-measured.** `9e6 Hz/V` describes an actuator driven by a 0–55V amplified HV rail. Driving it directly from a ±1V SMA is a different transfer function, and the loop filter design in `LoopFiltersUI_DAC1_and_DAC2.py` depends on this number.

**The internal-VCO feature on channel b becomes meaningless.** `mux_internal_vco`'s `selector_vco = "10"` routes `DACin1` into the VCO — now the slow integrator rather than a PLL output. Harmless if unused, but worth knowing.
