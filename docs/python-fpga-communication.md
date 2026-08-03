# How the Python GUI talks to the FPGA

This describes the runtime communication path between `digital_servo_python_gui/` and the Red Pitaya board — i.e. what actually happens on the wire once the box is running (as opposed to firmware/software *deployment*, covered in [external-clock-and-hv-switcher-protection.md](external-clock-and-hv-switcher-protection.md)).

There are two independent, unrelated channels, each with a Zynq-side server and a Python-side client. Both are client/server, not peer-to-peer: the Zynq daemons sit listening and only respond when the Python side initiates.

## Channel 1: `monitor-tcp` — the register protocol (port 5000, TCP)

This is the channel that matters for day-to-day operation — every register read/write the GUI performs (PLL gains, DDC settings, loop filter coefficients, status readback, etc.) goes through it.

- **Server**: `Zynq software/monitor-tcp/monitor-tcp.c`, a forking TCP daemon listening on port 5000. It `mmap()`s `/dev/mem` to get direct access to the two AXI-mapped register regions exposed by the PS↔PL bridge (`0x40000000` — the DPLL/FPGA register space — and `0x80000000` — XADC/clock-wizard). `monitor-tcp.h` additionally defines the stock Red Pitaya oscilloscope register layout at `0x00100000`, used by leftover scope-related code paths, not by the DPLL-specific protocol.
- **Client**: `digital_servo_python_gui/RP_PLL.py` (`RP_PLL_device` class). Opens a TCP socket to the board and issues requests; `SuperLaserLand_JD_RP.py` sits above it and translates named `BUS_ADDR_*`/`ENDPOINT_*` constants into raw register addresses.

**Protocol**: every request is a fixed 12-byte header, `struct.pack('=III', magic, addr_or_len, data_or_reserved)`, using magic bytes in the `0xABCD12xx` family:

| Magic byte | Command | Purpose |
|---|---|---|
| `0x33` | `WRITE_REG` | write a 32-bit value to a register address |
| `0x34` | `READ_REG` | read back a 32-bit register |
| `0x35` | `READ_BUFFER` | read a block of consecutive registers/samples |
| `0x37` | `WRITE_FILE` | upload a file to the SD card (filename + payload appended after the header) |
| `0x38` | `SHELL_COMMAND` | run a shell command on the Zynq (command string appended after the header) |
| `0x39` | reserved | not called from Python today |
| `0x3A` | `REBOOT_MONITOR` | restart the `monitor-tcp` daemon |
| `0x40` | `READ_FILE` | download a file from the SD card |

A few more magic bytes exist server-side (flank-servo, IGM/phase streaming, avg_spc, beamshot, continuous_pll_updates) with no live Python caller — dead/experimental as far as the current GUI is concerned.

Python always initiates: it sends a 12-byte request (with an optional payload for file/shell commands), and `monitor-tcp.c` does the actual `mmap()`'d register access or file/shell operation and writes a reply back over the same TCP connection. Data flows both directions (write commands carry data to the board, read commands carry data back), but only in response to a Python-side request — the Zynq never pushes register data unsolicited.

`RP_PLL.py` also carries an Opal-Kelly-compatibility shim (`SetWireInValue`/`GetWireOutValue`/etc.), kept so code written for the project's earlier Opal-Kelly USB FPGA board still works unmodified against this TCP transport underneath.

Also see: [pll-firmware-architecture.md](pll-firmware-architecture.md) for how the FPGA side actually decodes/services these register addresses once they land on the AXI bus (the `registers_read.vhd` legacy readback range vs. the generic `addr_packed.vhd`/`FSM_addr_packed.vhd` write-cache-and-lookup mechanism).

## Channel 2: `udp_discovery` — LAN board discovery (UDP, ports 1952/1953)

Unrelated to the register protocol. Its only job is letting the GUI's startup dialog find boards on the local network without the user typing in an IP address.

- **Server**: `Zynq software/udp_discovery/udp_discovery.c`, listening on UDP port 1952.
- **Client**: `digital_servo_python_gui/UDPRedPitayaDiscovery.py`, used from `initialConfiguration_RP.py` (the board-selection dialog shown at startup). `common.findMostLikelyLANBroadcastIPAddress()` guesses the local subnet's broadcast address.

**Protocol**: Python broadcasts an empty UDP packet to the LAN broadcast address on port 1952. Any Red Pitaya running `udp_discovery.c` hears it and replies with its `eth0` MAC address to port 1953. There's no register data involved — it's purely a "who's out there" ping/reply used to populate the list of discoverable boards in the startup dialog.

`Zynq software/udp_discovery/monitor.c` is a stray/unbuildable stock Red Pitaya leftover in this folder and not part of the discovery daemon (per the top-level `CLAUDE.md`).

## Summary

| | `monitor-tcp` | `udp_discovery` |
|---|---|---|
| Transport | TCP, port 5000 | UDP, ports 1952 (request) / 1953 (reply) |
| Zynq-side file | `monitor-tcp.c` | `udp_discovery.c` |
| Python-side client | `RP_PLL.py` / `SuperLaserLand_JD_RP.py` | `UDPRedPitayaDiscovery.py` |
| Purpose | Register read/write, file transfer, shell commands, reboot — the actual FPGA control path | Board discovery only (returns a MAC address) |
| Used for | Every GUI control/readback during normal operation | Populating the board list in the startup dialog |
