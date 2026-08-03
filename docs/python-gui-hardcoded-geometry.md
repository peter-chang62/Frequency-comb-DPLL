# Hardcoded window/widget geometry in the Python GUI

This document is a thorough inventory of everything in
`digital_servo_python_gui/` that hardcodes a pixel-based size, position, or
font — i.e. anything that could look wrong, clip, or otherwise fail to adapt
if a user resizes a window or runs the app on a screen with a different
resolution/DPI than whatever the original developer used. It supersedes an
earlier addendum in [python-gui-architecture.md](python-gui-architecture.md),
which covers the module hierarchy instead.

Scope: only files that actually feed the live, running GUI are covered in
section 2 — see section 1 for how that set was determined and what's
excluded (tests, standalone scripts, dead notes).

## 1. Which files feed the live GUI

Traced transitively from the two real entry points:

- **`XEM_GUI3.py`** (`controller` class) — the actual app bootstrap.
- **`Launcher.py`** — spawns one or more `XEM_GUI3.py` processes via
  `subprocess.Popen(...)`. It imports no local modules of its own (only
  stdlib/PyQt5/`win32*`), so it contributes no import edges beyond itself.
  It's still an entry point in the sense that a user runs it directly, so
  its own window (a command console for launching lock boxes) is included
  in section 2 even though it sits outside the `XEM_GUI3.py` import graph.

### Import graph (from `XEM_GUI3.py`)

```
XEM_GUI3.py
 +-- SuperLaserLand_JD_RP.py
 |    +-- SuperLaserLand2_JD2_PLL.py
 |    +-- RP_PLL.py
 |    +-- common.py
 +-- XEM_GUI_MainWindow.py
 |    +-- LoopFiltersUI.py
 |    |    +-- user_friendly_QLineEdit.py
 |    |    +-- SocketErrorLogger.py -> RP_PLL.py
 |    +-- DisplayVNAWindow.py
 |    |    +-- DisplayTransferFunctionWindow.py
 |    |         +-- common.py, fix_pyqt5_compatibility.py
 |    +-- LoopFiltersUI_DAC1_and_DAC2.py -> LoopFiltersUI.py
 |    +-- DisplayDitherSettingsWindow.py
 |    +-- user_friendly_QLineEdit.py
 |    +-- SpectrumWidget.py
 |    |    +-- ThermometerWidget.py
 |    |    +-- SLLSystemParameters.py
 |    |    +-- SuperLaserLand_mock.py   (imported at module scope; only *used* in a __main__ demo block)
 |    |    +-- SocketErrorLogger.py
 |    +-- RP_PLL.py
 |    +-- SocketErrorLogger.py, common.py
 +-- FreqErrorWindowWithTempControlV2.py
 |    +-- AsyncSocketComms.py
 |    +-- SocketErrorLogger.py
 |    +-- common.py
 +-- initialConfiguration_RP.py
 |    +-- UDPRedPitayaDiscovery.py
 |    +-- RP_PLL.py
 +-- SLLSystemParameters.py
 +-- DisplayDitherSettingsWindow.py
 +-- DisplayDividerAndResidualsStreamingSettingsWindow.py
 |    +-- SocketErrorLogger.py
 +-- ConfigurationRPSettingsUI.py
 |    +-- user_friendly_QLineEdit.py
 |    +-- SuperLaserLand_JD_RP.py (re-entrant, already in graph)
 |    +-- SocketErrorLogger.py
 |    +-- DataLoggingDisplayWidget.py
 +-- devicesData.py
 +-- common.py
 +-- RP_PLL.py
```

### Classification

**LIVE** — reachable transitively from `XEM_GUI3.py`, exercised in real
(non-test, non-`__main__`-demo) code paths:

| File | Why |
|---|---|
| `XEM_GUI3.py` | entry point |
| `Launcher.py` | separate entry point (spawns `XEM_GUI3.py`); no local imports of its own |
| `SuperLaserLand_JD_RP.py` | imported by `XEM_GUI3.py`, `ConfigurationRPSettingsUI.py` |
| `SuperLaserLand2_JD2_PLL.py` | imported by `SuperLaserLand_JD_RP.py` |
| `RP_PLL.py` | imported by `XEM_GUI3.py`, `SuperLaserLand_JD_RP.py`, `XEM_GUI_MainWindow.py`, `SocketErrorLogger.py`, `initialConfiguration_RP.py` |
| `common.py` | imported by `XEM_GUI3.py`, `SuperLaserLand_JD_RP.py`, `XEM_GUI_MainWindow.py`, `FreqErrorWindowWithTempControlV2.py`, `DisplayTransferFunctionWindow.py` |
| `XEM_GUI_MainWindow.py` | imported by `XEM_GUI3.py` |
| `LoopFiltersUI.py` | imported by `XEM_GUI_MainWindow.py`, `LoopFiltersUI_DAC1_and_DAC2.py` |
| `LoopFiltersUI_DAC1_and_DAC2.py` | imported by `XEM_GUI_MainWindow.py` |
| `DisplayVNAWindow.py` | imported by `XEM_GUI_MainWindow.py` |
| `DisplayTransferFunctionWindow.py` | imported by `DisplayVNAWindow.py` |
| `fix_pyqt5_compatibility.py` | imported **and called at module scope** (not `__main__`-gated) by `DisplayTransferFunctionWindow.py` — genuinely live |
| `user_friendly_QLineEdit.py` | imported by `XEM_GUI_MainWindow.py`, `LoopFiltersUI.py`, `ConfigurationRPSettingsUI.py` |
| `SpectrumWidget.py` | imported and instantiated inside `XEM_GUI_MainWindow.py` |
| `ThermometerWidget.py` | imported by `SpectrumWidget.py` |
| `SLLSystemParameters.py` | imported by `XEM_GUI3.py`, `SpectrumWidget.py` |
| `SocketErrorLogger.py` | imported by six live files |
| `FreqErrorWindowWithTempControlV2.py` | imported by `XEM_GUI3.py` |
| `AsyncSocketComms.py` | imported by `FreqErrorWindowWithTempControlV2.py` |
| `initialConfiguration_RP.py` | imported by `XEM_GUI3.py` |
| `UDPRedPitayaDiscovery.py` | imported by `initialConfiguration_RP.py` |
| `DisplayDitherSettingsWindow.py` | imported by `XEM_GUI3.py` |
| `DisplayDividerAndResidualsStreamingSettingsWindow.py` | imported by `XEM_GUI3.py` |
| `ConfigurationRPSettingsUI.py` | imported by `XEM_GUI3.py` |
| `DataLoggingDisplayWidget.py` | imported by `ConfigurationRPSettingsUI.py` |
| `devicesData.py` | imported by `XEM_GUI3.py` |

Two files are LIVE overall but contain a dead sub-part worth flagging:
- `ConfigurationRPSettingsUI.py`'s `if __name__ == '__main__':` block
  (standalone demo) is never invoked from anywhere else.
- `LoopFiltersUI.py`'s trailing `main()`/`__main__` block is entirely
  commented out — inert, not even an active guard.

**TEST** (test-only files, or only reachable from them):

| File | Why |
|---|---|
| `RP_PLL_test.py` | pytest suite; imports `RP_PLL`, `AsyncSocketComms`, `SuperLaserLand_JD_RP`, `XEM_GUI3` — drives the app under test, not part of the runtime graph |
| `SuperLaserLand_JD_RP_test.py` | pytest suite; imports `SuperLaserLand_mock` |
| `XEM_GUI_MainWindow_test.py` | pytest suite |
| `gui_test.py` | pytest suite; imports `SuperLaserLand_mock`, `XEM_GUI_MainWindow`, `TestHelpers` |
| `TestHelpers.py` | only imported by the two test files above |
| `SuperLaserLand_mock.py` | imported at module scope by the live `SpectrumWidget.py`, but only ever *instantiated* in `SpectrumWidget.py`'s own `__main__` demo block and in test files — classified TEST since it's never exercised by a real code path, despite being loaded into memory whenever the live app starts |
| `automated_test.py` | standalone hardware-in-the-loop acceptance suite; not part of the interactive GUI |
| `html_report.py`, `text_report.py`, `mux_board.py`, `rigol_scope_tools.py`, `socket_tools.py`, `site_settings.py` | only reachable from `automated_test.py` |

**STANDALONE / UNUSED:**

| File | Why |
|---|---|
| `PythonSoup.py` | standalone GPIB/VISA counter-control dialog; not imported anywhere, runs only as its own script |
| `list_funcs.py` | one-off dev script with no imports; parses `XEM_GUI_MainWindow.py` as text |
| `load_logging_data.py` | standalone plotting script with a hardcoded local path; not imported anywhere |
| `"Functions in XEM_GUI_MainWindow.py that interact with the socket.py"` | plain-text dev notes, not valid importable Python |
| `"Moving code outside of the MainWindow into separate Widget classes.py"` | plain-text design notes, same reasoning |

Only the LIVE set (plus `Launcher.py`'s own window) is covered below.

## 2. Everything that hardcodes pixel-based appearance

### Window-level geometry (`resize`/`move`/`setGeometry` on a top-level window)

| File | `resize()` live? | position (`move`) live? | Net effect |
|---|---|---|---|
| `XEM_GUI_MainWindow.py` | No — [line 1091](../digital_servo_python_gui/XEM_GUI_MainWindow.py:1091) commented out | No — `center()` is defined with a live `.move()` inside, but the only call site ([line 1092](../digital_servo_python_gui/XEM_GUI_MainWindow.py:1092)) is commented out | Main window is entirely auto-sized/placed by Qt from its `QGridLayout` (`self.setLayout(grid)`); no hardcoded geometry actually runs. |
| `XEM_GUI3.py` | No | Yes — [line 200](../digital_servo_python_gui/XEM_GUI3.py:200) unconditionally moves the outer tabbed window to `availableGeometry().topLeft() + QPoint(800-300, 0)` | Only the outer container window's *position* is hardcoded; size comes from its child tabs' layouts. |
| `DisplayTransferFunctionWindow.py` | **Yes** — live `self.resize(1200, 500)` ([line 367](../digital_servo_python_gui/DisplayTransferFunctionWindow.py:367)) | **Yes** — `center()` is called; its `cp = ...center()` ([line 375](../digital_servo_python_gui/DisplayTransferFunctionWindow.py:375)) is computed but unused, superseded by the live `.move(...QPoint(800+100, 50))` ([line 379](../digital_servo_python_gui/DisplayTransferFunctionWindow.py:379)) | The one window with a fully hardcoded, functioning size *and* position. |
| `DisplayVNAWindow.py` | No — [line 484](../digital_servo_python_gui/DisplayVNAWindow.py:484) commented out | **Yes** — `center()` is called, `cp` at [line 494](../digital_servo_python_gui/DisplayVNAWindow.py:494) computed-but-unused, live `.move(...QPoint(50, 50))` at [line 497](../digital_servo_python_gui/DisplayVNAWindow.py:497) | Position hardcoded, size auto. |
| `DisplayDitherSettingsWindow.py` | No (commented, [line 241](../digital_servo_python_gui/DisplayDitherSettingsWindow.py:241)) | No — `center()` is called ([line 242](../digital_servo_python_gui/DisplayDitherSettingsWindow.py:242)) but both `.move()` lines inside it ([253](../digital_servo_python_gui/DisplayDitherSettingsWindow.py:253)-[254](../digital_servo_python_gui/DisplayDitherSettingsWindow.py:254)) are commented out; `cp` at [line 251](../digital_servo_python_gui/DisplayDitherSettingsWindow.py:251) computed but unused | Fully auto-sized/placed — the call is a no-op. |
| `DisplayDividerAndResidualsStreamingSettingsWindow.py` | No (commented, [line 309](../digital_servo_python_gui/DisplayDividerAndResidualsStreamingSettingsWindow.py:309)) | No — same dead-`center()` pattern ([line 310](../digital_servo_python_gui/DisplayDividerAndResidualsStreamingSettingsWindow.py:310), unused `cp` at [316](../digital_servo_python_gui/DisplayDividerAndResidualsStreamingSettingsWindow.py:316), dead moves at [318](../digital_servo_python_gui/DisplayDividerAndResidualsStreamingSettingsWindow.py:318)-[319](../digital_servo_python_gui/DisplayDividerAndResidualsStreamingSettingsWindow.py:319)) | Fully auto-sized/placed. |
| `ConfigurationRPSettingsUI.py` | No — `w.resize(800, 300)` ([line 510](../digital_servo_python_gui/ConfigurationRPSettingsUI.py:510)) exists only in the file's `if __name__ == '__main__':` demo block, not the real class | No — `self.center()` call is commented out ([line 346](../digital_servo_python_gui/ConfigurationRPSettingsUI.py:346)); `cp` at [line 411](../digital_servo_python_gui/ConfigurationRPSettingsUI.py:411) computed but unused; both `.move()` lines ([413](../digital_servo_python_gui/ConfigurationRPSettingsUI.py:413)-[414](../digital_servo_python_gui/ConfigurationRPSettingsUI.py:414)) commented | Fully auto-sized/placed in the real app. |
| `LoopFiltersUI.py` | No — the only `resize()` call ([line 938](../digital_servo_python_gui/LoopFiltersUI.py:938)) sits inside an entirely commented-out `main()` block | — | Fully auto-sized/placed. |
| `Launcher.py` | No `resize`/`move`/`setGeometry` found for its own window | No | Fully auto-sized/placed by Qt. |

All live screen-geometry queries go through the **deprecated**
`QtWidgets.QDesktopWidget().availableGeometry()` API — none use the newer
`QScreen`. The pattern everywhere is `availableGeometry().topLeft() +
QPoint(<hardcoded offset>)`, so the window's anchor adapts to which monitor
is primary, but the fixed pixel offset stacked on top (10, 50, or ~800px) is
not DPI- or resolution-aware. In five files
(`ConfigurationRPSettingsUI.py`, `DisplayTransferFunctionWindow.py`,
`DisplayDitherSettingsWindow.py`,
`DisplayDividerAndResidualsStreamingSettingsWindow.py`,
`XEM_GUI_MainWindow.py`) a screen-center point (`cp`) is computed and then
never used — a leftover half of a copy-pasted center/move pattern.

### Per-field width/height caps

`setMaximumWidth`/`setMinimumWidth`/`setMinimumHeight` on individual
`QLineEdit`/`QLabel` fields, live in every case, no shared constant behind
any of them:

| File | Count | Representative lines |
|---|---|---|
| `XEM_GUI_MainWindow.py` | 8 | [743](../digital_servo_python_gui/XEM_GUI_MainWindow.py:743), [757](../digital_servo_python_gui/XEM_GUI_MainWindow.py:757), [761](../digital_servo_python_gui/XEM_GUI_MainWindow.py:761), [774](../digital_servo_python_gui/XEM_GUI_MainWindow.py:774), [808](../digital_servo_python_gui/XEM_GUI_MainWindow.py:808), [959](../digital_servo_python_gui/XEM_GUI_MainWindow.py:959), [964](../digital_servo_python_gui/XEM_GUI_MainWindow.py:964), [976](../digital_servo_python_gui/XEM_GUI_MainWindow.py:976), [979](../digital_servo_python_gui/XEM_GUI_MainWindow.py:979), [984](../digital_servo_python_gui/XEM_GUI_MainWindow.py:984) — all `.setMaximumWidth(60)` |
| `LoopFiltersUI.py` | 5 fields + 3 spacers (see below) | [167](../digital_servo_python_gui/LoopFiltersUI.py:167)-[171](../digital_servo_python_gui/LoopFiltersUI.py:171): `qedit_kp/fi/fii/fd/fdf.setMaximumWidth(100)` |
| `DisplayTransferFunctionWindow.py` | 7 live + 1 dead | [258](../digital_servo_python_gui/DisplayTransferFunctionWindow.py:258) `qedit_k` (60px), [264](../digital_servo_python_gui/DisplayTransferFunctionWindow.py:264)/[273](../digital_servo_python_gui/DisplayTransferFunctionWindow.py:273)/[278](../digital_servo_python_gui/DisplayTransferFunctionWindow.py:278) `qedit_f1/f0/zeta` (120px), [283](../digital_servo_python_gui/DisplayTransferFunctionWindow.py:283)/[294](../digital_servo_python_gui/DisplayTransferFunctionWindow.py:294)/[299](../digital_servo_python_gui/DisplayTransferFunctionWindow.py:299) `qedit_T/pgain/icorner` (60px); [305](../digital_servo_python_gui/DisplayTransferFunctionWindow.py:305) `qedit_comment` (80px) is commented out |
| `DisplayVNAWindow.py` | 7 | [302](../digital_servo_python_gui/DisplayVNAWindow.py:302), [310](../digital_servo_python_gui/DisplayVNAWindow.py:310), [317](../digital_servo_python_gui/DisplayVNAWindow.py:317), [322](../digital_servo_python_gui/DisplayVNAWindow.py:322), [327](../digital_servo_python_gui/DisplayVNAWindow.py:327), [365](../digital_servo_python_gui/DisplayVNAWindow.py:365), [371](../digital_servo_python_gui/DisplayVNAWindow.py:371) — all 60px |
| `FreqErrorWindowWithTempControlV2.py` | 7 | [275](../digital_servo_python_gui/FreqErrorWindowWithTempControlV2.py:275), [298](../digital_servo_python_gui/FreqErrorWindowWithTempControlV2.py:298), [306](../digital_servo_python_gui/FreqErrorWindowWithTempControlV2.py:306), [358](../digital_servo_python_gui/FreqErrorWindowWithTempControlV2.py:358), [363](../digital_servo_python_gui/FreqErrorWindowWithTempControlV2.py:363), [367](../digital_servo_python_gui/FreqErrorWindowWithTempControlV2.py:367), [371](../digital_servo_python_gui/FreqErrorWindowWithTempControlV2.py:371) — all 40px |
| `ConfigurationRPSettingsUI.py` | 4 | [228](../digital_servo_python_gui/ConfigurationRPSettingsUI.py:228) (100px), [233](../digital_servo_python_gui/ConfigurationRPSettingsUI.py:233) (60px), [275](../digital_servo_python_gui/ConfigurationRPSettingsUI.py:275) (100px), [279](../digital_servo_python_gui/ConfigurationRPSettingsUI.py:279) (300px) |
| `DisplayDitherSettingsWindow.py` | 3 | [185](../digital_servo_python_gui/DisplayDitherSettingsWindow.py:185), [191](../digital_servo_python_gui/DisplayDitherSettingsWindow.py:191), [197](../digital_servo_python_gui/DisplayDitherSettingsWindow.py:197) — all 100px |
| `SpectrumWidget.py` | 1 | [144](../digital_servo_python_gui/SpectrumWidget.py:144) `qedit_rawdata_length` (60px) |
| `ThermometerWidget.py` | 2 | [194](../digital_servo_python_gui/ThermometerWidget.py:194)-[195](../digital_servo_python_gui/ThermometerWidget.py:195) — part of its own self-geometry, see below |

### Layout spacing/spacers (new)

- `LoopFiltersUI.py` — three fixed-pixel spacer widgets used for manual
  layout gaps: [line 138](../digital_servo_python_gui/LoopFiltersUI.py:138)
  `qlabel_spacerh.setMinimumWidth(30)`,
  [line 140](../digital_servo_python_gui/LoopFiltersUI.py:140)
  `qlabel_spacerh2.setMinimumWidth(7)`,
  [line 142](../digital_servo_python_gui/LoopFiltersUI.py:142)
  `qlabel_spacerv.setMinimumHeight(25)`. Also
  [lines 79](../digital_servo_python_gui/LoopFiltersUI.py:79)-[80](../digital_servo_python_gui/LoopFiltersUI.py:80)
  floor the transfer-function `pg.PlotWidget` to a 100×100px minimum.
- `XEM_GUI_MainWindow.py:1079` — `grid.setSpacing(10)`, a hardcoded 10px
  inter-widget gap on the main layout grid.
- `SpectrumWidget.py:224` — `grid.setSpacing(5)`, a hardcoded 5px gap.

### Hardcoded fonts

- `XEM_GUI_MainWindow.py` — the lock-status indicator gets
  `setStyleSheet('font-size: 18pt; color: white; background-color:
  green/red')` repeatedly as lock state changes:
  [lines 502](../digital_servo_python_gui/XEM_GUI_MainWindow.py:502),
  [504](../digital_servo_python_gui/XEM_GUI_MainWindow.py:504),
  [564](../digital_servo_python_gui/XEM_GUI_MainWindow.py:564),
  [686](../digital_servo_python_gui/XEM_GUI_MainWindow.py:686),
  [780](../digital_servo_python_gui/XEM_GUI_MainWindow.py:780) (all live);
  [line 781](../digital_servo_python_gui/XEM_GUI_MainWindow.py:781) is a
  commented-out duplicate.
- `Launcher.py` — command-output text areas get a hardcoded
  `font.setPointSize(16)` ([lines 173](../digital_servo_python_gui/Launcher.py:173)-[175](../digital_servo_python_gui/Launcher.py:175); a commented alternative at line 173 shows an earlier attempt at the same thing).
- `PythonSoup.py` — `'font-size: 24pt; font-family: courier; font-weight:
  bold;'` applied via `setStyleSheet`
  ([lines 270](../digital_servo_python_gui/PythonSoup.py:270)-[271](../digital_servo_python_gui/PythonSoup.py:271)).

Elsewhere, `setStyleSheet(...)` is used extensively (`XEM_GUI3.py`,
`XEM_GUI_MainWindow.py`, `ConfigurationRPSettingsUI.py`,
`DisplayDitherSettingsWindow.py`,
`DisplayDividerAndResidualsStreamingSettingsWindow.py`,
`FreqErrorWindowWithTempControlV2.py`, `LoopFiltersUI.py`) but almost all of
it is color-only (status/lock indicators flashing red/green/orange) with no
pixel values — those are noted here for completeness but aren't a
resolution/DPI hazard.

### Self-painted widget geometry (intrinsic to the widget, not app layout)

- `ThermometerWidget.py` — a custom-painted gauge that computes its own
  pixel geometry throughout (`setFixedWidth`/`setFixedHeight`/`.move()`
  math for tick marks, labels, and the bar itself): e.g.
  [line 133](../digital_servo_python_gui/ThermometerWidget.py:133),
  [135](../digital_servo_python_gui/ThermometerWidget.py:135),
  [137](../digital_servo_python_gui/ThermometerWidget.py:137),
  [150](../digital_servo_python_gui/ThermometerWidget.py:150)-[151](../digital_servo_python_gui/ThermometerWidget.py:151),
  [160](../digital_servo_python_gui/ThermometerWidget.py:160),
  [165](../digital_servo_python_gui/ThermometerWidget.py:165)-[166](../digital_servo_python_gui/ThermometerWidget.py:166),
  [177](../digital_servo_python_gui/ThermometerWidget.py:177)-[178](../digital_servo_python_gui/ThermometerWidget.py:178),
  [192](../digital_servo_python_gui/ThermometerWidget.py:192)-[198](../digital_servo_python_gui/ThermometerWidget.py:198),
  [204](../digital_servo_python_gui/ThermometerWidget.py:204). Also includes
  an inline QSS border with a parametric pixel width:
  [line 189](../digital_servo_python_gui/ThermometerWidget.py:189)
  `'border: %dpx solid black' % self.border_width`. This is all how the
  widget draws itself, not an app-level layout decision — but it does mean
  the gauge won't rescale if the surrounding window is resized or DPI
  changes.
- `SpectrumWidget.py` [lines 171](../digital_servo_python_gui/SpectrumWidget.py:171)-[173](../digital_servo_python_gui/SpectrumWidget.py:173)
  — the embedded IQ-plot `pg.PlotWidget` is pinned to exactly 100×100px
  (`setMinimumSize(50,50)`, `setMaximumSize(200,200)`,
  `setFixedSize(100,100)`) regardless of window size or DPI.

### HighDPI-awareness posture

**Zero references** exist anywhere in `digital_servo_python_gui/` to
`AA_EnableHighDpiScaling`, `AA_UseHighDpiPixmaps`, `devicePixelRatio`,
`QT_SCALE_FACTOR`, or `QT_AUTO_SCREEN_SCALE_FACTOR`. The app has no explicit
HighDPI handling at all — it relies entirely on whatever Qt/PyQt5's default
behavior is for the installed version, meaning none of the hardcoded pixel
values above are compensated for by any scaling layer.

### Confirmed absent categories

Searched for and **not found** anywhere in the live files:
`setColumnWidth`, `setRowHeight`, splitter `setSizes()`, `setIconSize`,
`QPixmap(...).scaled(...)`, `setContentsMargins`, and pyqtgraph
axis-width/`setPreferredHeight` hardcoding. Noted here so it's clear these
were checked rather than overlooked.

### One dynamic (non-hazard) call, noted for completeness

`XEM_GUI_MainWindow.py:1107` — `self.qplt_DDC0_spc_right_viewbox.setGeometry(
p1.vb.sceneBoundingRect())` — this is pyqtgraph's standard pattern for
syncing a twin y-axis viewbox to the real plot's current bounding rect. It's
live, but the argument is a live value computed from another widget's
current geometry, not a fixed pixel constant, so it self-corrects on resize
rather than being a hazard.

## 3. Bottom line

There's no shared stylesheet/QSS file, no `.ui` files for any top-level
window, and no HighDPI-awareness code anywhere in the package. Appearance
across different window sizes and screen resolutions depends entirely on
Qt's default layout auto-sizing, plus a long tail of individually hardcoded
pixel constants scattered across nearly every dialog file.

- **Window-level position/size** hardcoding is fairly *concentrated* — most
  of it is dead code (commented out or never invoked), with
  `DisplayTransferFunctionWindow.py` the only window that hardcodes a fully
  live size *and* position, and `XEM_GUI3.py`/`DisplayVNAWindow.py`
  hardcoding just a screen position.
- **Per-field width/height caps and hardcoded fonts** are the more
  pervasive, genuinely *scattered* pattern — present in essentially every
  dialog file, each with its own one-off pixel values and no shared
  constant or style layer tying them together.
