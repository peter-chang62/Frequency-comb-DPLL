# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This is a **legacy branch** (`NIST_DCS_2020`) for Comb Lock Boxes (CLBs) shipped by Waxwing Instruments Inc. before 2026 — no longer actively supported upstream. Treat changes conservatively: this code controls physical hardware (a Red Pitaya-based digital PLL box with a high-voltage switcher), so correctness in register addressing, sign/scaling conventions, and startup sequencing matters more than style.

## Repository layout (three independent subsystems)

This project spans three toolchains that don't build together — know which one you're in before reaching for a build/test command:

1. **`digital_servo_python_gui/`** — PyQt5 desktop GUI (Python 3), the operator-facing control software. This is where almost all day-to-day development happens.
2. **`Firmware_Vivado_Project/`** — VHDL/Verilog FPGA source for the Red Pitaya's Zynq programmable logic (PL side), built with Xilinx Vivado (tested with 2015.4). `redpitaya.srcs/sources_1/DigitalPLL/` contains the actual PLL/lock-in/servo logic; the rest is imported Red Pitaya boilerplate (AXI plumbing, IP cores, board definition).
3. **`Zynq software/`** — C code (`monitor-tcp`, `udp_discovery`) that runs on the Zynq's embedded Linux (PS/ARM side), bridging the Python GUI's TCP/UDP protocol to memory-mapped FPGA registers. Building these requires the full Red Pitaya SDK tree (`../../shared/`) and an ARM cross-compiler (`CROSS_COMPILE`) — neither is present in this repo, so these are edited but not built here.

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
- **`SuperLaserLand_JD_RP.py`** (`SuperLaserLand_JD_RP` class) — the board abstraction. Owns all `BUS_ADDR_*` register address constants (the FPGA register map — must stay in sync with the VHDL address decoding in `Firmware_Vivado_Project/.../DigitalPLL/registers_read.vhd` and `addr_packed.vhd`), gain/offset/limit constants for the ADCs/DACs, and high-level operations (dither lock-in, VNA/system-ID sweeps, frequency counters). Delegates PLL-specific logic to `SuperLaserLand2_JD2_PLL.py` (`PLL0_module`, `PLL1_module`, `PLL2_module`).
- **`RP_PLL.py`** (`RP_PLL_device` class) — the wire protocol layer. Talks to the Zynq's `monitor-tcp` server over a raw TCP socket using a magic-bytes framed protocol (`MAGIC_BYTES_WRITE_REG`/`READ_REG`/`READ_BUFFER`/`WRITE_FILE`/`READ_FILE`/`SHELL_COMMAND`/`REBOOT_MONITOR`, all `0xABCD12xx`). `FPGA_BASE_ADDR` (0x40000000) and `FPGA_BASE_ADDR_XADC` (0x80000000) are the two AXI memory-mapped regions exposed by the PS↔PL bridge; register addresses elsewhere in the codebase are offsets into these.
- **`initialConfiguration_RP.py`** — device discovery/selection dialog. Uses `UDPRedPitayaDiscovery.py` to broadcast-discover boards on the LAN (see `common.findMostLikelyLANBroadcastIPAddress()` for the subnet-guessing heuristic), then hands off the chosen board's IP to `RP_PLL_device` and can push new FPGA bitstreams/Zynq binaries via the same file-transfer protocol.
- **`devicesData.py`** / **`devices_data.xml`** — static registry mapping a board's serial number to its UI color, shorthand name, and config file, so multiple physical boxes can be told apart in the GUI.
- **`XEM_GUI_MainWindow.py`** is the main window; most other `Display*Window.py` / `*UI.py` files are separate dialogs/widgets it owns (dither settings, transfer function/VNA plotting, loop filter tuning, data logging, temperature control, etc.) — each is a fairly self-contained PyQt widget class.

## FPGA firmware / Zynq software protocol coupling

The FPGA register map, the `monitor-tcp` C server, and the Python `RP_PLL_device`/`SuperLaserLand_JD_RP` classes are three independent implementations of the same protocol/address map — there is no shared schema. When changing a register address, width, or the framing protocol, all three must be updated together:
- FPGA side: `Firmware_Vivado_Project/redpitaya.srcs/sources_1/DigitalPLL/registers_read.vhd`, `addr_packed.vhd`, `FSM_addr_packed.vhd`
- Zynq side: `Zynq software/monitor-tcp/monitor-tcp.c` (magic-bytes constants and `read_write_loop_minimum()`/related handlers)
- Python side: `digital_servo_python_gui/RP_PLL.py` (magic bytes) and `SuperLaserLand_JD_RP.py` (`BUS_ADDR_*` constants)

`revisions.md` tracks shipped firmware/software version pairs with SHA256 hashes per release — update it when cutting a new firmware/software pair, following the existing format.

## Licensing provenance

This codebase originates from two public-domain sources, both under `Licenses/`: the NIST digital control box software (most FPGA firmware and all Python GUI code) and Red Pitaya's own software/firmware (Zynq embedded software and supporting FPGA firmware). Keep this in mind if reorganizing or attributing code.
