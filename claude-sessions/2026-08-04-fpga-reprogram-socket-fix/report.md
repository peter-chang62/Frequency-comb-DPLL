# Report: fix spurious OSError 10038 on repeated FPGA reprogram

**Date:** 2026-08-04
**Branch:** `Peter-edits-adding-phase-lock-option`
**Plan:** see [plan.md](plan.md) in this folder

## Request

User reported that "Update FPGA firmware" works the first time (on initial connect), but throws
`OSError [WinError 10038]: An operation was attempted on something that is not a socket` if the
communication menu is reopened and the same action is repeated, followed by a spurious
disconnect/reconnect and transient bad register reads (`Warning! You received the default value...`).
User asked to double-check the proposed root cause and fix it.

## Investigation

Diagnosed by reading `RP_PLL.py` (the `RP_PLL_device` socket wrapper) and
`initialConfiguration_RP.py` (the connection/reprogram dialog), then verified with an Explore agent
that traced the full reconnection call chain and grepped the whole `digital_servo_python_gui/` tree
for other instances of the same pattern.

**Root cause**: `RP_PLL_device.send()`/`read()` only guard on `self.valid_socket` before touching
`self.sock`. Three methods in `initialConfiguration_RP.py`
(`qbtn_compare_versions_clicked()`, `programFPGAClicked()`, `programCPUClicked()`) closed the socket
directly via `self.dev.sock.shutdown()`/`.close()` instead of calling
`self.dev.CloseTCPConnection()`, leaving `valid_socket` stuck at `True` while the underlying OS
handle was already dead. On first launch no periodic timer is running yet, so nothing touches the
socket in that window. On a later reprogram from an already-open session, the 100ms dither timer and
500ms XADC timer are both already running and fire immediately after the raw close returns, hitting
`sendall()` on the dead handle. The crash is caught and self-heals via the existing
`socketErrorEvent()` → `CloseTCPConnection()` → 3s reconnect timer → `OpenTCPConnection()` chain,
which is why the log showed a full automatic reconnect — but it's a spurious, avoidable error/reconnect
cycle.

Confirmed via full-repo grep that these were the only 3 live call sites with this bug pattern (a 4th
instance exists in `RP_PLL.py`'s `if __name__ == '__main__':` demo script, but it's unreachable from
the GUI and was left untouched). Also confirmed the socket **open** path has no analogous bug: every
connection to the board goes through the single `OpenTCPConnection()` implementation
(`RP_PLL.py:79-92`), which atomically creates the socket, connects, and sets `valid_socket` together
— there's no raw `socket.socket(...)` call anywhere else in the codebase for this transport.

## Fix

Added `self.dev.CloseTCPConnection()` immediately after the existing manual
`shutdown()`/`close()` in all three call sites, so the explicit graceful TCP close is preserved but
`valid_socket`/`sock` are also correctly reset to their "disconnected" state. Any timer/read that
fires before the next legitimate `OpenTCPConnection()` now cleanly hits the existing `CommsError`
guard instead of calling a method on a dead socket handle.

## Files changed

```
digital_servo_python_gui/initialConfiguration_RP.py
```

## Verification performed

- Re-read all 3 edited call sites and confirmed the diff matches the plan exactly (`git diff`).
- Could not run `pytest` in this session — no usable Python interpreter was available in the sandbox
  (only a Windows Store stub `python.exe`; the user's own `env38` environment lives in their
  terminal, not here). The change is a single added line per site calling an existing, already-used
  method, with no signature or control-flow changes, so risk is low, but the user should run
  `pytest RP_PLL_test.py` / `pytest gui_test.py` and/or reproduce on real hardware (reprogram FPGA
  twice in a row from an already-open communication menu) to confirm.
