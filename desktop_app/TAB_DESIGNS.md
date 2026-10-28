# MIMIR Desktop: Standalone Tab Design Specifications

> **Document:** `desktop_app/TAB_DESIGNS.md`  
> **Source Mockup:** [`desktop_app/assets/ui/mimir_desktop_overview.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_overview.jpg)  
> **Scope:** High-fidelity architectural and visual breakdown for each of the 6 standalone surfaces in the MIMIR desktop application.

---

## Navigation & Master Layout

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ MIMIR                                                                 ─  □  ✕    │
├───────────────┬──────────────────────────────────────────────────────────────────┤
│ ⌂  Home       │                                                                  │
│ 👁  Observe    │  [ Standalone Active Tab Viewport: Scrollable Canvas ]           │
│ 📖  Memory     │                                                                  │
│ ⎘  Replicate  │                                                                  │
│ 🛡  Trust      │                                                                  │
│ ───────────── │                                                                  │
│ ⚙  Settings   │                                                                  │
└───────────────┴──────────────────────────────────────────────────────────────────┘
```

The shell provides a fixed 216px left navigation rail with spaced wordmark `M I M I R`, six primary routes with rounded 10px hover/checked pill indicators (`#F0EFFF` active lavender background, `#4338CA` active indigo text), and a scrollable right content canvas.

---

## 01. Home Cockpit

![01. Home Screen](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_01_home.jpg)

* **Standalone Asset:** [`desktop_app/assets/ui/mimir_desktop_01_home.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_01_home.jpg)
* **Code Implementation:** [`desktop_app/tabs/home.py`](file:///d:/Projects/MIMIR/desktop_app/tabs/home.py)

### Layout Wireframe
```
┌──────────────────────────────────────────────┬───────────────────────────────┐
│ GOOD EVENING,                                │  ● Resting · Not observing    │
│ Arin                                         │  Your workspace isn't being   │
│ I'm MIMIR. I observe how you work, learn     │  observed right now.          │
│ your style, and apply it when you need me.   │  [ ▷ Start Observing ]        │
└──────────────────────────────────────────────┴───────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│  ✦   What would you like to explore?                                  [ 🎙 ] │
└──────────────────────────────────────────────────────────────────────────────┘
 [✦ Explain this code]  [📄 Plan a feature]  [💡 Brainstorm ideas]  [··· More]

┌──────────────────────┬───────────────────────┬───────────────────────────────┐
│ [▤] 2                │ [ılı] 0               │ [📈] 0.60                     │
│     Principles       │       Episodes        │      Average confidence       │
│     learned        > │       observed      > │                             > │
└──────────────────────┴───────────────────────┴───────────────────────────────┘

WHAT WOULD YOU LIKE TO DO?
┌───────────────┬──────────────────────┬───────────────────────┬───────────────┐
│ [👁] Observe  │ [📖] Memory          │ [⎘] Replicate         │ [🛡] Trust     │
│     Watch…  > │     Your standards > │     Apply style…    > │     Local…  > │
└───────────────┴──────────────────────┴───────────────────────┴───────────────┘
```

### Components & Visual Details
1. **Hero Header:**
   - Overline: `GOOD EVENING,` in 11px uppercase tracked slate.
   - User Name: `Arin` in Georgia 38px serif type.
   - Lede: Subtitle in warm Slate-700.
2. **Status Card:**
   - White porcelain container with drop-shadow.
   - Status badge dot: Green `#059669` (pulsing when active).
   - Button: `▷ Start Observing` with lavender pill background (`#F0EFFF`) and indigo text.
3. **Explore Search Bar:**
   - Pill input container (`QLineEdit#explore_bar`) with leading sparkle `✦` and trailing microphone button (`QPushButton#mic_btn`).
   - Quick action prompt chips with hover focus.
4. **KPI Metric Tiles (1x3 Row):**
   - Soft colored icon boxes (`#icon_box_purple`, `#icon_box_blue`, `#icon_box_green`).
   - Large bold counter values (`#kpi_number`), descriptive labels, and chevron navigators.
5. **Quick Action Directory (1x4 Row):**
   - 4 horizontal cards linking directly to `Observe`, `Memory`, `Replicate`, and `Trust`.

---

## 02. Observe Screen

![02. Observe Screen](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_02_observe.jpg)

* **Standalone Asset:** [`desktop_app/assets/ui/mimir_desktop_02_observe.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_02_observe.jpg)
* **Code Implementation:** [`desktop_app/tabs/observe.py`](file:///d:/Projects/MIMIR/desktop_app/tabs/observe.py)

### Layout Wireframe
```
OBSERVE
Control what MIMIR can see.
Choose your workspace, manage observation, and review captured sessions.

┌──────────────────────────────────────────────────────────────────────────────┐
│ ● Resting · Not observing                                                    │
│ Your workspace isn't being observed right now.         [ ▷ Start Observing ] │
└──────────────────────────────────────────────────────────────────────────────┘

 [ Sessions ] [ Applications ]                                    [ All time ▾ ]

┌──────────────────────────────────────────────────────────────────────────────┐
│ 💻  VS Code Session                                              [ Analyzed ] │
│     Today, 4:00 PM · 42 minutes                                            > │
├──────────────────────────────────────────────────────────────────────────────┤
│ 🌐  Chrome Session                                              [ Processed ] │
│     Today, 2:10 PM · 18 minutes                                            > │
├──────────────────────────────────────────────────────────────────────────────┤
│ 📁  Project: AlgoFight                                          [ Processed ] │
│     Yesterday, 8:15 PM · 1 hour 12 minutes                                 > │
├──────────────────────────────────────────────────────────────────────────────┤
│ ⌨   Terminal Session                                             [ Ignored ] │
│     Yesterday, 6:01 PM · 24 minutes                                        > │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Components & Visual Details
1. **Status Banner:** Full-width porcelain card showing telemetry state and `Start Observing` action pill.
2. **Segmented Controller:** Rounded pill switcher between `Sessions` and `Applications`, plus `All time` filter.
3. **Session Rows:**
   - App glyphs (`💻`, `🌐`, `📁`, `⌨`).
   - Title and relative duration metadata.
   - Status badges:
     - `Analyzed`: Soft sky blue (`#EFF6FF` bg, `#2563EB` text)
     - `Processed`: Soft emerald sage (`#ECFDF5` bg, `#059669` text)
     - `Ignored`: Neutral slate (`#F1F5F9` bg, `#64748B` text)

---

## 03. Memory Screen

![03. Memory Screen](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_03_memory.jpg)

* **Standalone Asset:** [`desktop_app/assets/ui/mimir_desktop_03_memory.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_03_memory.jpg)
* **Code Implementation:** [`desktop_app/tabs/memory.py`](file:///d:/Projects/MIMIR/desktop_app/tabs/memory.py)

### Layout Wireframe
```
MEMORY
What MIMIR knows about you.
These are the principles learned from your work, with supporting evidence.

 [ 🔍 Search principles...                                    ]   [ All ▾ ]

┌──────────────────────────────────────────────────────────────────────────────┐
│ [</>]  Prefers modular, independently testable components.     [ Confirmed ] │
│        Observed in 4 sessions · Confidence 0.85                            > │
├──────────────────────────────────────────────────────────────────────────────┤
│ [📄]   Writes clear, concise functions.                         [ Learning ] │
│        Observed in 3 sessions · Confidence 0.77                            > │
├──────────────────────────────────────────────────────────────────────────────┤
│ [📁]   Organizes project structure by feature.                  [ Learning ] │
│        Observed in 2 sessions · Confidence 0.62                            > │
├──────────────────────────────────────────────────────────────────────────────┤
│ [🔄]   Refactors for readability.                             [ Unconfirmed ]│
│        Observed in 2 sessions · Confidence 0.58                            > │
├──────────────────────────────────────────────────────────────────────────────┤
│ [💡]   Prefers simple, practical solutions.                     [ Learning ] │
│        Observed in 1 session · Confidence 0.51                             > │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Components & Visual Details
1. **Search & Filtration:** Search input and category dropdown.
2. **Principle Cards:**
   - Category icon inside soft rounded box (`#icon_box_purple`, `#icon_box_blue`, `#icon_box_neutral`).
   - Latent invariant rule title.
   - Evidentiary citation (`Observed in X sessions · Confidence Y.YY`).
   - Epistemic status pill:
     - `Confirmed`: Conf >= 0.80 (Soft green)
     - `Learning`: 0.50 <= Conf < 0.80 (Soft purple)
     - `Unconfirmed`: Conf < 0.50 (Neutral gray)

---

## 04. Replicate Screen

![04. Replicate Screen](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_04_replicate.jpg)

* **Standalone Asset:** [`desktop_app/assets/ui/mimir_desktop_04_replicate.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_04_replicate.jpg)
* **Code Implementation:** [`desktop_app/tabs/replicate.py`](file:///d:/Projects/MIMIR/desktop_app/tabs/replicate.py)

### Layout Wireframe
```
REPLICATE
Apply your style to new tasks.
Describe what you want, and MIMIR will use your learned principles.

┌──────────────────────────────────────────────────────────────────────────────┐
│ What would you like MIMIR to create or change?                        [ 🎙 ] │
│ [ e.g. Implement an authentication flow...                                 ] │
│                                                                              │
│ Project: [ AlgoFight ▾ ]   Use principles: [ Auto-selected (3) ▾ ]           │
│                                                          [ Generate → ]      │
└──────────────────────────────────────────────────────────────────────────────┘

RECENT TASKS                                                        View all >
┌──────────────────────────────────────────────────────────────────────────────┐
│ [</>]  Implement authentication flow                           [ Completed ] │
│        AlgoFight · 2 days ago                                              > │
├──────────────────────────────────────────────────────────────────────────────┤
│ [</>]  Improve error handling                                  [ Completed ] │
│        MIMIR · 4 days ago                                                  > │
├──────────────────────────────────────────────────────────────────────────────┤
│ [💡]   Design system ideas                                      [ Reviewed ] │
│        Personal Notes · 6 days ago                                         > │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Components & Visual Details
1. **Synthesis Card:**
   - Prompt input with dictation mic button.
   - Context controls: Project selector, principle conditioning combobox, and `Generate →` button (`#6366F1` indigo).
2. **Audit Feed:**
   - Recent tasks with project origin, relative timestamp, and status badges (`Completed` / `Reviewed`).

---

## 05. Trust Screen

![05. Trust Screen](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_05_trust.jpg)

* **Standalone Asset:** [`desktop_app/assets/ui/mimir_desktop_05_trust.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_05_trust.jpg)
* **Code Implementation:** [`desktop_app/tabs/trust.py`](file:///d:/Projects/MIMIR/desktop_app/tabs/trust.py)

### Layout Wireframe
```
TRUST
Your data, your control.
Manage permissions, storage, and privacy settings.

Access
Choose what MIMIR can observe and interact with.
┌──────────────────────────────────────────────────────────────────────────────┐
│ 🟦  VS Code                                                                  │
│     D:\Projects                                                      [ (●) ] │
├──────────────────────────────────────────────────────────────────────────────┤
│ 🌐  Google Chrome                                                            │
│     Browsing activity (titles & structure)                           [ (●) ] │
├──────────────────────────────────────────────────────────────────────────────┤
│ ⌨   Terminal / Command Line                                                  │
│     Commands and context (no sensitive content)                      [ (○) ] │
├──────────────────────────────────────────────────────────────────────────────┤
│ + Add application                                                          > │
└──────────────────────────────────────────────────────────────────────────────┘

Data
See how your data is stored and managed.
┌──────────────────────────────────────────────────────────────────────────────┐
│ 💾  Storage location:                                    Local (This device) │
│ 🕒  Retention period:                                       Until you delete │
│ 🗑  Clear all learned data:                                        [ Clear ] │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Components & Visual Details
1. **Access Card:** Per-application switches allowing instantaneous exclusion of IDEs, browsers, and terminals.
2. **Data Governance Card:** Local-only storage confirmation, retention policy, and destructive `Clear` action pill with confirmation.

---

## 06. Settings Screen

![06. Settings Screen](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_06_settings.jpg)

* **Standalone Asset:** [`desktop_app/assets/ui/mimir_desktop_06_settings.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_06_settings.jpg)
* **Code Implementation:** [`desktop_app/tabs/settings.py`](file:///d:/Projects/MIMIR/desktop_app/tabs/settings.py)

### Layout Wireframe
```
SETTINGS
Customize your experience.
Control how MIMIR looks, sounds, and works.

┌─────────────────┬────────────────────────────────────────────────────────────┐
│ [ General     ] │ General                                                    │
│ [ Voice       ] │ Basic preferences for MIMIR.                               │
│ [ Model       ] │ Name:                      [ MIMIR                       ] │
│ [ Appearance  ] │ Language:                  [ English (US) ▾              ] │
│ [ Shortcuts   ] │ Start on system launch:    [ (●) ]                         │
│ [ Integrations] │ Show in system tray:       [ (●) ]                         │
│                 │                                                            │
│                 │ Updates                                                    │
│                 │ Version 1.0.0                       [ Check for updates ]  │
└─────────────────┴────────────────────────────────────────────────────────────┘
```

### Components & Visual Details
1. **Left Category Sub-Nav:** Clean vertical category switcher with active lavender highlight.
2. **Configuration Panels:** Text input fields, system toggles, language dropdowns, and version updater.
