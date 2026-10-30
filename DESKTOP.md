# MIMIR Desktop App — Design & Run Guide

> PyQt6-native shell with the same serene porcelain UI as `web/`, and six
> functional tabs wired 1:1 to the MIMIR core (`mimir/`).
>
> 📐 **UI/UX Design Specification:** See [`desktop_app/DESIGN.md`](file:///d:/Projects/MIMIR/desktop_app/DESIGN.md)  
> 🖼️ **Visual Design Mockups:** See [`desktop_app/assets/ui/`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/)

## 1. Why PyQt6 native

- The visor (`mimir/ui/visor.py`) already targets PyQt6/PySide6 with lazy
  imports; the desktop shell reuses that dependency — no Electron/Node
  toolchain, no duplicated webview bridge.
- Porcelain tokens from `DESIGN.md` / `web/index.css` render as a Qt
  stylesheet (`desktop_app/theme.py`), so desktop and web stay visually
  in sync from one palette.
- Core logic (`telemetry`, `detective`, `store`, `actor`, `actuator`,
  `voice`) is imported directly — no IPC, no REST layer to drift.

## 2. Tab map (hybrid set)

| Screen | Spec | Core wiring | Key actions |
|---|---|---|---|
| Home | DESIGN.md 5.1 | `VisorController`, `memory_stats`, `respond`, `try_listen` | Serif greeting, status card + Start/Stop, explore bar + mic + Ask + chips, KPI cards, action directory |
| Observe | DESIGN.md 5.2 | `observe_git_repo`/`observe_new` → `infer_principles` → `save` | Watch banner, Sessions/Applications segmented, timeframe filter, session feed with badges, workspace card |
| Memory | DESIGN.md 5.3 | `list_all`, `query_relevant`, `update_confidence`, `delete` | Search + category filter, principle cards with confidence badges, evidence drawer, calibrate/copy/delete |
| Replicate | DESIGN.md 5.4 | `ReplicationActor.replicate` + `prefs.record_task` | Prompt panel + mic, project/principles/min-conf targeting, Generate, guided output, clickable recent-tasks feed |
| Trust | DESIGN.md 5.5 | `redact_text`, scope prefs, `clear_memory` | Watched-folder switches, clickable storage/retention rows, confirmed emergency wipe, filter test |
| Settings | DESIGN.md 5.6 | `DesktopPrefs`, extras probes, actuator dry-run | General/Voice/Model/Appearance/Shortcuts/Integrations panels, persisted to `~/.habitous/desktop.json`; launch/tray switches drive real OS login items + tray icon |

## 3. Architecture

```
desktop_app/
  DESIGN.md        production UI/UX spec + component map (section 6)
  assets/ui/       reference mockups (01–06, home hero, overview)
  __main__.py      python -m desktop_app [--store PATH]
  app.py           MainWindow: brand rail + scroll canvas (Qt only)
  watch.py         WatchController: 15 s observation polling (Qt only)
  theme.py         production tokens + qt_stylesheet() (Qt-free)
  state.py         AppState: store/visor/voice/episodes/prefs (Qt-free)
  prefs.py         DesktopPrefs JSON store + recent tasks (Qt-free)
  presenters.py    badges, greeting, filters, formatting (Qt-free)
  tabs/_layout.py  page/card/badge/toggle/ClickableCard scaffolding
  tabs/
    home.py        greeting + status + explore + KPIs + directory
    observe.py     banner + sessions/applications + workspace
    memory.py      bank + category filter + evidence drawer
    replicate.py   prompt + targeting + output + recent tasks
    trust.py       allow-list + storage + wipe + filter test
    settings.py    6-category calibration panels
```

Voice and actuation from the former Act & Voice tab now live where the
spec puts them: mic triggers on the Home/Replicate inputs (`try_listen`),
spoken replies via Ask (`respond`), and the actuator dry-run + backend
status under Settings → Voice/Integrations.

- `AppState` is the single source of truth: one `PrincipleStore`,
  one `VisorController`, one safe `VoiceCoordinator` (dummy mic/speaker
  until hardware is attached), and the last observed episode batch.
- All hardware is lazy: importing or constructing state never touches
  the mic, speakers, cursor, or Qt. Live motion/speech degrade to
  dry-runs with clear messages when the `cobot` extras are absent.
- Safety preserved: allow-listed roots, `TelemetryBoundaryError` shown
  in-app, secrets redacted at ingestion, `run_safe_command` never uses
  a shell, `abort()` trips instantly and flips the visor to ALERT.
- Shell: 216 px brand rail (`M I M I R`, five nav buttons with lavender
  active pill, separator, pinned Settings), `Ctrl+1…6` shortcuts, default
  window 1280×800 (minimum 1024×640). Every screen shares one page
  geometry (32/28 px margins), overline + serif/sans headers, and
  `QFormLayout` rows for label alignment.
- Polish: 16 px cards with ambient shadow, pill inputs and buttons,
  rounded list rows, slim hairline scrollbars, hover/pressed/disabled/
  focus states on every control, soft status badges, and iOS-style
  permission switches.
- Freshness: pages exposing `refresh_content()` re-render from `AppState`
  on every navigation, so Home stats, Memory, and Observe never go stale.
- Watch mode: `WatchController` polls the workspace every 15 s, prepends
  only unseen episodes, and holds the OBSERVING visor state until stopped.

## 4. Run

Project-local env (per repo convention):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[cobot]   # cobot extras = PyQt6 + actuator/voice backends
```

Launch:

```powershell
python -m desktop_app                 # default store mimir_memory.db
python -m desktop_app --store C:\data\mimir.db
```

Without the extras (headless logic + tests only):

```powershell
pip install -e .
python -m pytest tests/test_desktop_app.py -q
```

## 5. Verification

- `python -m pytest tests/test_desktop_app.py -q` — theme sync, tab
  registry, store round-trip, offline replicate, intent routing, visor
  transitions, redaction, presenter formats, plus an offscreen Qt smoke
  test that skips when PyQt6 is absent.
- Full suite: `python -m pytest -q` (83 core tests + desktop tests).
