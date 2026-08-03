# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This is a **legacy branch** (`NIST_DCS_2020`) for Comb Lock Boxes (CLBs) shipped by Waxwing Instruments Inc. before 2026 — no longer actively supported upstream. Treat changes conservatively: this code controls physical hardware (a Red Pitaya-based digital PLL box with a high-voltage switcher), so correctness in register addressing, sign/scaling conventions, and startup sequencing matters more than style.

## Repository layout (three independent subsystems)

This project spans three toolchains that don't build together — know which one you're in before reaching for a build/test command:

1. **`digital_servo_python_gui/`** — PyQt5 desktop GUI (Python 3), the operator-facing control software. This is where almost all day-to-day development happens.
2. **`Firmware_Vivado_Project/`** — VHDL/Verilog FPGA source for the Red Pitaya's Zynq programmable logic (PL side), built with Xilinx Vivado (tested with 2015.4). `redpitaya.srcs/sources_1/DigitalPLL/` contains the actual PLL/lock-in/servo logic — `dpll_wrapper.v` is the top-level module instantiating everything else (DDC front-end filters, PLL loop filters, dither lock-in, system-ID/VNA, frequency counters, residuals monitoring); the rest of `redpitaya.srcs/sources_1/` is imported Red Pitaya boilerplate (AXI plumbing, IP cores, board definition) plus `addr_packed.vhd`/`FSM_addr_packed.vhd`, the generic register-readback mechanism described below. A number of `DigitalPLL/` modules are superseded and no longer instantiated — e.g. `PLL_loop_filters.vhd` (→ `PLL_loop_filters_with_saturation.vhd`), `system_identification.vhd` (→ `system_identification_with_dither2.vhd`), `zero_deadtime_counter.vhd`, `crash_monitor_v1.vhd` (never implemented — see the comment in `registers_read.vhd`), and `residuals_streaming.vhd` (→ the `ram_data_logger` path); don't assume every file here is part of the live bitstream.
3. **`Zynq software/`** — C code that runs on the Zynq's embedded Linux (PS/ARM side). `monitor-tcp/monitor-tcp.c` is the real bridge — a forking daemon on TCP port 5000 that `mmap()`s `/dev/mem` and speaks the magic-bytes register protocol (see "FPGA firmware / Zynq software protocol coupling" below). `udp_discovery/udp_discovery.c` is a separate, protocol-unrelated UDP responder on port 1952 used only for LAN board discovery (its counterpart is `UDPRedPitayaDiscovery.py`, see below). `monitor-tcp/monitor-tcp - Copy.c`, `udp_discovery/monitor.c`, and `monitor-tcp/scpi-server.c` are stray/unbuildable stock Red Pitaya utility leftovers, not part of either daemon. Building requires the full Red Pitaya SDK tree (`../../shared/`) and an ARM cross-compiler (`CROSS_COMPILE`) — neither is present in this repo, so these are edited but not built here.

## Running the GUI

```
cd digital_servo_python_gui
python XEM_GUI3.py
```

Historically tested with WinPython-64bit (WinPython-64bit-3.6.1.0Qt5, later WPy64-312100) on Windows — PyQt5-based, but not Windows-specific in principle. `fix_pyqt5_compatibility.py` papers over a QtGui/QtWidgets API split between WinPython Qt versions; keep that in mind if you see odd `QtGui.Q*` usages that look like they belong to `QtWidgets`.

`Launcher.py` spawns one or more `XEM_GUI3.py` instances (e.g. to run two lock boxes side by side) and is hard-coded per-machine via `strCombLocksFolder` — not portable, don't treat it as the canonical entry point.

## Tests (Python GUI)

Tests are pytest-based and live alongside the GUI code (`*_test.py`, `gui_test.py`). There's no `pytest.ini`/`conftest.py`/requirements file — just run pytest from `digital_servo_python_gui/`:

```
cd digital_servo_python_gui
pytest gui_test.py
pytest RP_PLL_test.py
pytest XEM_GUI_MainWindow_test.py
pytest automated_test.py
pytest -k test_slowStart100VSwitchingSupply   # run a single test
```

Tests instantiate real Qt widgets (`QtWidgets.QApplication`) and mock the hardware layer rather than requiring physical hardware:
- `SuperLaserLand_mock.py` subclasses `SuperLaserLand_JD_RP` and overrides methods that need physical FPGA/socket access, optionally injecting `RP_PLL.CommsError` via `bIntroduceCommsException` flags to test error handling paths.
- `RP_PLL_test.py` instead mocks at the transport layer via `AsyncSocketComms` (`AsyncSocketServer`/`AsyncSocketClient`) plus a `Hardware_mock` that maps register addresses to Python read/write handlers — use this style when a test needs to exercise the actual wire protocol.
- `TestHelpers.py` has shared assertion helpers, notably `close_enough()` (float/array comparison with slop) and `compare_struct_fields()`.

`automated_test.py` and `html_report.py`/`text_report.py` implement a hardware-in-the-loop acceptance test suite (`TestGUIController`) that drives a real board via `mux_board.py` and Rigol scope tooling (`rigol_scope_tools.py`) — these require physical hardware and site-specific config in `site_settings.py` (IP address, COM port, report output folder), not just mocks.

## Python GUI architecture

- **`XEM_GUI3.py`** — entry point. `controller` class owns the Qt application, the hardware comms object, and all top-level windows.
- **`SuperLaserLand_JD_RP.py`** (`SuperLaserLand_JD_RP` class) — the board abstraction. Owns ~60 `BUS_ADDR_*`/`ENDPOINT_*` register address constants (the FPGA register map — must stay in sync with the VHDL address decoding in `Firmware_Vivado_Project/.../DigitalPLL/registers_read.vhd`/`dpll_wrapper.v` and `addr_packed.vhd`), grouped by function: core bus/mux/status/trigger addresses, frequency counters, DAC current monitors, VCO control, system-ID/VNA (`0x5000` range), ADC/DAC front end and PGA gains (`0x6000` range), PLL loop-filter/integrator registers consumed by `SuperLaserLand2_JD2_PLL.py` (`0x7000` range), DDC/dual-comb phase generation (`0x8000` range), dither/lock-in per DAC, and `openLoopGain`/mux (`0x9000` range) — plus a separate AXI-space block for XADC/clock-wizard registers (added to `FPGA_BASE_ADDR_XADC`). All actual I/O funnels through `send_bus_cmd`/`send_bus_cmd_32bits`, which multiply the register-map address by 4 before calling into `RP_PLL_device` (undoing the divide-by-4 the VHDL applies internally — see the protocol section below). Delegates PLL-specific gain/limit math to `SuperLaserLand2_JD2_PLL.py`'s `PLL0_module`/`PLL1_module`/`PLL2_module` (fixed `bus_base_address` `0x7000`/`0x7010`/`0x7020` each), which never touch sockets or raw addresses directly — they call back into the owning `SuperLaserLand_JD_RP` instance for every register access.
- **`RP_PLL.py`** (`RP_PLL_device` class) — the wire protocol layer. Talks to the Zynq's `monitor-tcp` server over a raw TCP socket using a magic-bytes framed protocol (`MAGIC_BYTES_WRITE_REG`/`READ_REG`/`READ_BUFFER`/`WRITE_FILE`/`READ_FILE`/`SHELL_COMMAND`/`REBOOT_MONITOR`, all `0xABCD12xx`) — every request is a fixed 12-byte header (`struct.pack('=III', magic, addr_or_len, data_or_reserved)`), with file/shell commands appending raw filename/command/payload bytes after the header. `FPGA_BASE_ADDR` (0x40000000) and `FPGA_BASE_ADDR_XADC` (0x80000000) are the two AXI memory-mapped regions exposed by the PS↔PL bridge; register addresses elsewhere in the codebase are offsets into these. Also carries an Opal-Kelly-compatibility shim (`SetWireInValue`/`GetWireOutValue`/etc.) kept so code originally written for the project's earlier Opal-Kelly USB FPGA board still works unmodified against this TCP transport.
- **`initialConfiguration_RP.py`** — device discovery/selection dialog. Uses `UDPRedPitayaDiscovery.py` to broadcast-discover boards on the LAN — it sends an empty UDP packet to the broadcast address on port 1952 and listens on 1953 for replies (the Zynq-side counterpart is `Zynq software/udp_discovery/udp_discovery.c`, which echoes back its `eth0` MAC address; see `common.findMostLikelyLANBroadcastIPAddress()` for the subnet-guessing heuristic) — then hands off the chosen board's IP to `RP_PLL_device` and can push new FPGA bitstreams/Zynq binaries via the same file-transfer protocol, or compare local vs. remote SHA-256 hashes.
- **`devicesData.py`** / **`devices_data.xml`** — static registry mapping a board's serial number to its UI color, shorthand name, and config file, so multiple physical boxes can be told apart in the GUI.
- **`XEM_GUI_MainWindow.py`** is the main window; most other `Display*Window.py` / `*UI.py` files are separate dialogs/widgets it owns (dither settings, transfer function/VNA plotting, loop filter tuning, data logging, temperature control, etc.) — each is a fairly self-contained PyQt widget class.

## FPGA firmware / Zynq software protocol coupling

The FPGA register map, the `monitor-tcp` C server, and the Python `RP_PLL_device`/`SuperLaserLand_JD_RP` classes are three independent implementations of the same protocol/address map — there is no shared schema. When changing a register address, width, or the framing protocol, all three must be updated together.

**Address scheme:** the Zynq PS's GP0 AXI master (`0x40000000` = Python's `FPGA_BASE_ADDR`) is fanned out in `red_pitaya_top.v` via `sys_addr[22:20]` into 8 slave slots of 1MB each — slot 0 is `dpll_wrapper` (the custom DPLL logic), slot 1 `ram_data_logger`, slot 2 `addr_packed`, slots 3/4 the stock Red Pitaya housekeeping/XADC blocks, 5/6 `mux_internal_vco`, 7 unused. `FPGA_BASE_ADDR_XADC` (`0x80000000`, GP1) is a separate AXI master for XADC/clock-wizard, unrelated to this slot fan-out.

**Two independent read paths inside the DPLL slot** — the reason most registers have no visible VHDL read logic: `registers_read.vhd` decodes a fixed, narrow legacy address range (`0x0025`–`0x0043`, divided by 4 vs. the Zynq's byte address) for status/counter readback; everything else is serviced by `addr_packed.vhd` + `FSM_addr_packed.vhd` (slot 2), a generic FIFO-fed CAM that caches every write and, on read, looks up "last value written to this address" rather than having dedicated per-register read logic. Writes themselves are decoded per-register via dozens of `parallel_bus_register` instances in `dpll_wrapper.v`, each hardwired to one `.ADDRESS(16'hXXXX)` constant (roughly `0x0040s`, `0x6000s`, `0x7000s` for the PLL loop filters — see `SuperLaserLand2_JD2_PLL.py`'s `bus_base_address` 0x7000/0x7010/0x7020 — `0x8000s` for DDC/comb phase, `0x9000s`).

Files that must be kept in sync:
- FPGA side: `Firmware_Vivado_Project/redpitaya.srcs/sources_1/DigitalPLL/registers_read.vhd`, `dpll_wrapper.v`, plus `Firmware_Vivado_Project/redpitaya.srcs/sources_1/addr_packed.vhd`/`FSM_addr_packed.vhd`
- Zynq side: `Zynq software/monitor-tcp/monitor-tcp.c` — a forking TCP daemon on port 5000 that `mmap()`s `/dev/mem` at both AXI regions and speaks a 12-byte-header magic-bytes protocol (`0xABCD1233` write_reg, `1234` read_reg, `1235` read_buffer, `1237` write_file, `1238` shell_command, `1239` reboot, `1240` read_file are the ones actually used end-to-end; a few more magic bytes exist server-side — flank-servo, IGM/phase streaming, avg_spc, beamshot, continuous_pll_updates — with no live Python caller, treat as dead/experimental)
- Python side: `digital_servo_python_gui/RP_PLL.py` (same magic bytes, `FPGA_BASE_ADDR`/`FPGA_BASE_ADDR_XADC`) and `SuperLaserLand_JD_RP.py` (`BUS_ADDR_*` constants, the write-side register map matching the VHDL `.ADDRESS` constants)

(Vestigial/superseded files on both the FPGA and Zynq side are called out in "Repository layout" above — don't assume every file in `DigitalPLL/` or `Zynq software/` is part of the deployed bitstream/binary.)

`revisions.md` tracks shipped firmware/software version pairs with SHA256 hashes per release — update it when cutting a new firmware/software pair, following the existing format.

See [docs/pll-firmware-architecture.md](docs/pll-firmware-architecture.md) for a detailed file-by-file walkthrough of the DPLL firmware itself — the DDC/phase-detector front end, the PII²D loop filters, the dither lock-in and VNA subsystems, and the register write/readback mechanisms (`addr_packed.vhd`/`FSM_addr_packed.vhd`/`registers_read.vhd`).

See [docs/python-fpga-communication.md](docs/python-fpga-communication.md) for how the two runtime channels between the Python GUI and the board actually work: the `monitor-tcp` register/file/shell protocol (port 5000, TCP) versus the unrelated `udp_discovery` LAN board-discovery broadcast (ports 1952/1953, UDP).

## External reference clock and the HV-switcher loss-of-clock fix

The external TTL reference clock enters on expansion pin DIO5_P (`exp_p_in[5]` in `red_pitaya_top.v`) and feeds `clk_in2` of `clk_wiz_0`, a Xilinx Clocking Wizard MMCM instantiated inside the Vivado block design (`redpitaya.srcs/sources_1/bd/system/system.bd`) — not the DPLL's custom register bus. `clk_in1` is the internal 200MHz PS clock; the select bit is a separate AXI-GPIO peripheral (`axi_gpio_clk_sel`, base `0x8003_0000`) written directly by software, independent of the DPLL's own register map. (A same-named-looking register at DPLL address `0x0050`, `parallel_bus_register_clk_select` in `dpll_wrapper.v`, is a red herring — it actually drives unrelated expansion GPIOs, not the clock wizard.) `clk_wiz_0`'s `clk_out1` (`clk_to_adc`) drives the ADC/DAC clock ODDRs. The wizard exposes a `LOCKED` output but it is left unconnected in the block design — there is no in-fabric watchdog or automatic fallback to the internal oscillator if the external clock is lost; software would have to poll the passive frequency counters (`digital_clock_freq_counter.vhd`, readback via `axi_gpio_freq1/2` at `0x8004_0000`+) and react manually.

On the Python side, `ConfigurationRPSettingsUI.setClkSelect()` only toggles a two-way internal/external radio button — there is no GUI field for the actual external clock frequency (`self.f_ext = 200e6` is hard-coded). `SuperLaserLand_JD_RP.setADCclockPLL()` writes MMCM multiplier/divider registers and the select/reset bits; the frequency itself is never transmitted to hardware, only derived integers assuming a fixed 200MHz reference.

**Known hazard (fixed):** a separate PWM signal (`osc_output` from `variable_duty_cycle_oscillator.vhd`) drives the daughter board's HV switching supply via `exp_n_out[2]`. It's clocked by `clk1`, itself derived from the same external-reference MMCM chain — so a lost/glitched external clock could freeze `osc_output`'s output register at an arbitrary level, including stuck-high, holding the HV switcher's MOSFET in continuous conduction and destroying it. Commit `20b1279` (2024-05-15, "Firmware update to protect against loss-of-clock event damaging the HV switching supply") added `duty_cycle_protector.vhd`, a watchdog clocked from `fclk[0]` (a Zynq PS clock independent of the external reference) that clamps `osc_output` low if it's high for more than ~16µs and holds it low for a ~16.8ms timeout; `red_pitaya_top.v` now routes `osc_output` through this protector before driving `exp_n_out[2]`. Note this only protects the HV-switcher PWM output specifically — a broader fix that auto-falls-back the *entire* clock tree to internal on loss of external reference (`clk_manager.vhd`/`clock_presence_detector.vhd`) exists on sibling branches (`origin/4Ch-counter`, `origin/hotfix-extclk-failsafe`) but was never merged into `NIST_DCS_2020`; the ADC/DAC data-path clock itself still has no loss-of-clock protection.

See [docs/external-clock-and-hv-switcher-protection.md](docs/external-clock-and-hv-switcher-protection.md) for the full writeup, including how to build and deploy a new `.bit` file to a board (no SSH required — it's automated via `initialConfiguration_RP.py`'s "reprogram FPGA" button).

## Licensing provenance

This codebase originates from two public-domain sources, both under `Licenses/`: the NIST digital control box software (most FPGA firmware and all Python GUI code) and Red Pitaya's own software/firmware (Zynq embedded software and supporting FPGA firmware). Keep this in mind if reorganizing or attributing code.
