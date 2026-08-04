# Fix: stale `valid_socket` after raw socket close in initialConfiguration_RP.py

## Context

Reprogramming the FPGA a second time (from the already-open "communication menu") crashes with
`OSError [WinError 10038]: An operation was attempted on something that is not a socket`, followed
by a spurious disconnect/reconnect cycle and bad register reads (`Warning! You received the default
value...`).

Root cause (confirmed by direct code reading + an Explore-agent verification pass):

- `RP_PLL_device.send()`/`read()` (`RP_PLL.py:210-235`) only check `self.valid_socket` before touching
  `self.sock`. The intended invariant (per the repo's own design notes,
  `Functions in XEM_GUI_MainWindow.py that interact with the socket.py`) is that `valid_socket` is
  `False` exactly when the socket must not be used.
- Three methods in `initialConfiguration_RP.py` close the TCP socket **directly** instead of going
  through `RP_PLL_device.CloseTCPConnection()`:
  - `qbtn_compare_versions_clicked()` — lines 235-236
  - `programFPGAClicked()` — lines 414-415
  - `programCPUClicked()` — lines 440-441

  Each does:
  ```python
  self.dev.sock.shutdown(socket.SHUT_RDWR)
  self.dev.sock.close()
  ```
  This closes the OS-level handle but leaves `self.dev.valid_socket == True` and `self.dev.sock`
  pointing at the now-dead socket object (`RP_PLL_device.CloseTCPConnection()`, `RP_PLL.py:74-77`, is
  the only thing that resets `valid_socket = False` / `sock = None`, and it's never called here).
- On first launch this never surfaces because no periodic timer is running yet when
  `programFPGAClicked()` fires from the startup dialog. On a later "reprogram" from an already-open
  session, `timerIDDither` (100ms, started by `getValues()`/`pushDefaultValues()` in
  `XEM_GUI_MainWindow.py`) and `timerXADCEvent` (500ms, `ConfigurationRPSettingsUI.py:352-369`) are
  both already running. The very next tick after `programFPGAClicked()` returns calls into
  `send()`/`read()`, sails past the stale `valid_socket == True` guard, and hits `sendall()` on the
  dead handle — the observed `OSError 10038`.
- The crash is self-healing (caught by `RP_PLL_device.socketErrorEvent()` →
  `Controller.socketErrorEvent()` → `stopCommunication()` → `CloseTCPConnection()` → 3s reconnection
  timer → `getActualValues()` → fresh `OpenTCPConnection()`), which is why the log shows a full
  reconnect happening automatically — but it's a spurious error + reconnect cycle that shouldn't
  happen, and it also explains the transient bad register reads right after (reconnecting before the
  board has actually finished reprogramming/rebooting `monitor-tcp`).

Confirmed this is the complete list of affected call sites in the live GUI runtime (a 4th instance
exists in `RP_PLL.py`'s `if __name__ == '__main__':` demo block, but that's a standalone script never
imported/reached by the GUI — out of scope, not touched).

## Fix

In each of the 3 call sites, add `self.dev.CloseTCPConnection()` immediately after the existing
manual `shutdown()`/`close()`, so the explicit graceful TCP close is preserved but the device's own
state flags (`valid_socket = False`, `sock = None`) are also correctly reset:

```python
self.dev.sock.shutdown(socket.SHUT_RDWR)
self.dev.sock.close()
self.dev.CloseTCPConnection()
```

This means any timer/read that fires in the window before the next legitimate
`OpenTCPConnection()` will cleanly hit the existing `raise CommsError` guard in `send()`/`read()`
(`RP_PLL.py:211-212`, `224-225`) — which is already handled gracefully elsewhere — instead of calling
a method on a dead socket handle.

Files to change:
- `digital_servo_python_gui/initialConfiguration_RP.py` — 3 edits:
  - `qbtn_compare_versions_clicked()` (~line 236)
  - `programFPGAClicked()` (~line 415)
  - `programCPUClicked()` (~line 441)

No other files need changes — `RP_PLL.py`'s `CloseTCPConnection()` already does exactly what's needed.

## Verification

- Run the existing unit test suites to make sure nothing regresses:
  ```
  cd digital_servo_python_gui
  pytest RP_PLL_test.py
  pytest gui_test.py
  ```
- Static check: re-read the 3 edited call sites to confirm `self.dev.CloseTCPConnection()` is called
  after the manual close in all three, and that `socket` import / `self.dev` are already in scope
  (they are, per current code).
- If the user has access to the physical board: reproduce the original scenario — connect, use
  "Update FPGA firmware" from the communication menu twice in a row (second time while the main
  window/dither timers are already running) — and confirm no `OSError 10038` traceback appears, and
  the reconnect after the second reprogram succeeds cleanly.
