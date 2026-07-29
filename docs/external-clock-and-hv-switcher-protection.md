# External reference clock, the HV-switcher loss-of-clock fix, and firmware deployment

## How external clock selection works

**Python GUI** (`digital_servo_python_gui/ConfigurationRPSettingsUI.py:421-451`, `setClkSelect`):
- Only a two-way radio button (`qradio_internal_clk` / `qradio_external_clk`) — there is no field for entering the actual external clock frequency. `self.f_ext = 200e6` is hard-coded in `__init__` (tagged `# TODO: make this configurable from the gui and xml file`).
- Calls `self.sl.setADCclockPLL(f_source, bExternalClock, CLKFBOUT_MULT=5, CLKOUT0_DIVIDE=8)` in `SuperLaserLand_JD_RP.py:2024-2067`.
- That function writes MMCM (Xilinx Clocking Wizard, PG065) config registers: `clkw_base_addr+0x200` (DIVCLK_DIVIDE/CLKFBOUT_MULT), `+0x208` (CLKOUT0_DIVIDE), polls `+0x04` for lock, writes `clk_sel_base_addr` (`0x0003_0000`, bit0=int/ext select, bit1=reset), triggers DRP reconfig via `+0x25C`.
- **No frequency value is ever sent to the FPGA** — only multiplier/divider integers assuming a fixed 200 MHz reference (internal or external), plus the select/reset bits. Actual external clock frequency is only ever *read back* (`getExtClockFreq`, display-only, via a passive frequency counter) — never written.

**FPGA side**: the real clock-select and MMCM live in the Vivado block design (`system.bd`), not in the DPLL's custom register bus:
- `clk_wiz_0` (Clocking Wizard IP) takes `clk_in1` = internal 200 MHz PS clock, `clk_in2` = buffered external ref from pin **DIO5_P** (`exp_p_in[5]`, `clk_ext_bufg`), and outputs `clk_out1` = `clk_to_adc`, which ultimately drives the ADC/DAC clock ODDRs in `red_pitaya_top.v`.
- Select bit (`clk_in_sel`) is driven by a separate **AXI-GPIO peripheral** `axi_gpio_clk_sel` at `0x8003_0000` — a plain software-writable static bit, independent of the DPLL's own register map. (A same-named-but-unrelated `parallel_bus_register_clk_select` at DPLL address `0x0050` is a red herring/repurposed register — it drives unrelated expansion GPIOs, not the clock wizard.)
- `clk_wiz_0` does expose a `LOCKED` output per its `.xci` config, but it is **left unconnected** in the block design — not exported, not wired to any interrupt/reset/register.
- Frequency counters (`digital_clock_freq_counter.vhd`) measure the external clock and internal Din1 signal, exposed via AXI-GPIO readback (`axi_gpio_freq1/2`, `0x8004_0000`+) — purely passive telemetry, no threshold/alarm/action logic.

**Conclusion: there is no automatic fallback or hardware watchdog tied to external-clock loss for the MMCM/ADC-DAC clocking path.** If the external clock disappears while selected, `clk_wiz_0` simply loses lock and `clk_out1` (the DAC/ADC clock) stops or glitches; software would have to notice via polling and manually switch back.

## What broke the HV amplifier daughter board, and the fix

The affected signal was **not** the ADC/DAC data path clock directly, but a separate PWM/duty-cycle output used to drive the daughter board's HV switching supply, gated onto `exp_n_out[2]`.

- **Root cause**: `variable_duty_cycle_oscillator.vhd` (instantiated in `dpll_wrapper.v`) is a free-running counter/comparator clocked by `clk1`, a fabric clock ultimately derived from the same external-reference-driven MMCM chain. Its output register (`output_int`) only updates on `clk1` edges. If the external reference clock is lost or glitches, `clk1` can stop toggling or glitch/relock with phase jumps — and because `output_int` is a plain register, it **freezes at whatever level it last held**, with no guarantee it's low. Pre-fix, this signal was wired straight to the expansion pin (`assign exp_n_out[2] = osc_output;`) which drives the HV switcher's gate/PWM input. If it freezes high, the switcher's MOSFET is held in continuous conduction (100% duty cycle) instead of switching — the classic failure mode that overheats/destroys a switch-mode MOSFET and downstream HV amplifier hardware.

- **The fix** (commit `20b1279`, "Firmware update to protect against loss-of-clock event damaging the HV switching supply", 2024-05-15, present in this branch's history): a new hardware watchdog module `duty_cycle_protector.vhd`, clocked from `fclk[0]` — a Zynq PS-generated 125 MHz clock that is **independent of the external reference/MMCM**, so it keeps running even if the ext clock disappears. It monitors `osc_output` and forcibly clamps it low if it stays high longer than `MAX_CYCLES_ON` cycles (configured as 2000 cycles ≈ 16 µs at 125 MHz), then holds it low for a fixed timeout (`2^21` cycles ≈ 16.8 ms) before allowing normal operation to resume. `red_pitaya_top.v` was changed to route `osc_output` through this protector (`osc_output_protected`) before driving `exp_n_out[2]`, instead of connecting it directly.

- **Files changed by commit `20b1279`** (confirmed via `git show --stat`): `duty_cycle_protector.vhd` (new), `duty_cycle_protector_tb.vhd` (new testbench, not synthesized), `red_pitaya_top.v`, `Firmware_Vivado_Project/redpitaya.xpr`, `revisions.md`. **No changes to `Zynq software/` or `digital_servo_python_gui/`** — this fix is entirely FPGA-fabric logic (VHDL/Verilog); getting it onto a board only requires rebuilding and re-deploying the bitstream (see below), not updating the Zynq-side C daemon or the Python GUI.

- Note: a more comprehensive fix exists on sibling branches (`clk_manager.vhd` + `clock_presence_detector.vhd`, an FSM that automatically falls back the *entire* design's clocking to the internal oscillator when the external reference is detected absent) — `origin/4Ch-counter`, `origin/hotfix-extclk-failsafe`. It was **never merged into `NIST_DCS_2020`**. This branch only has the narrower `duty_cycle_protector` watchdog, which protects the HV-switcher output specifically but does not address the general loss-of-external-clock condition for the rest of the design (e.g. the ADC/DAC data path clock itself still has no loss-of-clock protection).

## Deploying a new bitstream to a board

Routine `.bit` updates are automated end-to-end from the Windows GUI — **no SSH required**:

1. Build the `.bit` file from `Firmware_Vivado_Project/` in Vivado (tested with 2015.4).
2. In `digital_servo_python_gui/initialConfiguration_RP.py`, point the firmware path field (`qedit_firmware`) at the new `.bit` file and click the "reprogram FPGA" button (`qbtn_reprogram_fpga` → `programFPGAClicked()`, lines 396-416). This:
   - Uploads the local `.bit` file over the existing TCP connection to a fixed remote path on the Zynq, `/opt/red_pitaya_top.bit` (via `RP_PLL.write_file_on_remote()`, the `MAGIC_BYTES_WRITE_FILE` protocol command handled by `monitor-tcp.c`).
   - Waits ~2s (comment: "to handle slow SD cards").
   - Sends a remote shell command, `cat /opt/red_pitaya_top.bit > /dev/xdevcfg`, via `RP_PLL.send_shell_command()` — `/dev/xdevcfg` is the standard Xilinx Zynq PS "devcfg" driver; writing a bitstream to it live-reconfigures the FPGA fabric. No board reboot needed.
3. Optionally verify with the "compare versions" button (`qbtn_compare_versions_clicked()`), which SHA256-hashes the local file against `/opt/red_pitaya_top.bit` on the board (and `/opt/monitor-tcp` for the CPU-side binary) so you can confirm the deployed hash matches your build. `revisions.md` tracks shipped firmware/software version pairs with these hashes — update it after deploying, following the existing format.

A separate "reprogram CPU" button (`qbtn_reprogram_cpu` → `programCPUClicked()`, lines 419-439) exists for updating the `monitor-tcp` Zynq-side binary itself, but is **not needed for this particular fix** since it only touches FPGA fabric logic.

The only manual/out-of-band procedure in this repo is MicroSD-card flashing, and per `README.md` that is solely for *initial provisioning of a brand-new/blank board* — Waxwing-shipped CLBs already ship with base firmware, so it should not be needed for an ordinary firmware update like this one.
