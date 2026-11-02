# MIMIR Desktop Application: UI/UX Design Specification & Architecture

> **Document:** `desktop_app/DESIGN.md`  
> **Status:** Active Reference & Production Specification  
> **Aesthetic Philosophy:** *Serene Porcelain — Calm, Humane, Transparent & Tactile.*  
> **Visual Assets Directory:** [`desktop_app/assets/ui/`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/)

---

## 1. Executive Summary & Design Vision

The **MIMIR Desktop Application** provides a native, desktop-first home for the Digital Collaborative Robot (Digital Cobot). Rather than presenting itself as an intimidating developer terminal, an opaque black-box background service, or an intrusive chatbot, MIMIR embodies **"Calm Computing"**:

1. **Passive Dignity:** It sits peacefully on the desktop in a restful state until needed.
2. **Humane Craftsmanship:** Designed with warm porcelain tones, soft light gradients, generous whitespace, and editorial typography (combining a warm serif voice with clean sans-serif controls).
3. **Inspectable Autonomy:** Every principle learned, session observed, and task replicated is completely transparent, filterable, and editable.
4. **Local Sovereignty (Trust):** Privacy and observation boundaries are first-class, tangible controls—not buried in hidden sub-menus.

---

## 2. Visual Reference Architecture

The UI design comprises six core operational surfaces unified by a shared left-rail navigation structure:

| Screen # | Surface Name | Purpose & Primary Flow | Reference Asset |
| :--- | :--- | :--- | :--- |
| **01** | **Home** | Ambient status, exploration bar, KPI telemetry, quick navigation | [`mimir_desktop_01_home.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_01_home.jpg) |
| **02** | **Observe** | Telemetry control, session ingestion history, activity analysis | [`mimir_desktop_02_observe.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_02_observe.jpg) |
| **03** | **Memory** | Neuro-symbolic principle bank, confidence scoring, evidence review | [`mimir_desktop_03_memory.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_03_memory.jpg) |
| **04** | **Replicate** | Task specification, project context, compliant code & workflow synthesis | [`mimir_desktop_04_replicate.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_04_replicate.jpg) |
| **05** | **Trust** | Granular application boundaries, local storage policy, data purging | [`mimir_desktop_05_trust.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_05_trust.jpg) |
| **06** | **Settings** | User persona, voice engine, model provider, appearance, system tray | [`mimir_desktop_06_settings.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_06_settings.jpg) |

*Full 6-Screen System Overview:* [`mimir_desktop_overview.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_overview.jpg)  
*Full-Scale Hero Screen:* [`mimir_desktop_home.jpg`](file:///d:/Projects/MIMIR/desktop_app/assets/ui/mimir_desktop_home.jpg)

---

## 3. Design Tokens & Sensory System

### 3.1. Color Palette (Porcelain & Soft Accents)

The visual system uses soft off-whites, neutral linen grays, subtle hairline strokes, and desaturated status badges:

```css
/* Canvas & Surfaces */
--bg-app:               #F5F5F7;          /* Soft ambient desk tint */
--bg-surface:           #FFFFFF;          /* Pure card/container background */
--bg-subtle:            #F9FAFB;          /* Subtle recessed sections */
--bg-nav-active:        #F0EFFF;          /* Soft lavender pill for active tab */
--bg-hover:             #F3F4F6;          /* Micro-interaction hover */

/* Typography */
--text-heading:         #0F172A;          /* Slate-900 / Deep charcoal ink */
--text-serif-hero:      #0A192F;          /* Deep navy-slate for personal greeting */
--text-body:            #334155;          /* Slate-700 / High readability body */
--text-muted:           #64748B;          /* Slate-500 / Secondary metadata */
--text-faint:           #94A3B8;          /* Slate-400 / Placeholders & overlines */

/* Borders & Separators */
--border-hairline:      #EAEAEF;          /* Delicate card outline */
--border-subtle:        #E2E8F0;          /* Input fields and list dividers */
--border-focus:         #6366F1;          /* Soft indigo focus ring */

/* Semantic Status Tints */
--status-green-bg:      #ECFDF5;          /* Confirmed / Processed / Active */
--status-green-text:    #059669;
--status-purple-bg:     #F3E8FF;          /* Learning / Auto-selected */
--status-purple-text:   #7C3AED;
--status-blue-bg:       #EFF6FF;          /* Analyzed / Info */
--status-blue-text:     #2563EB;
--status-neutral-bg:    #F1F5F9;          /* Ignored / Unconfirmed / Resting */
--status-neutral-text:  #64748B;
--status-red-bg:        #FEF2F2;          /* Destructive / Clear / Alert */
--status-red-text:      #DC2626;

/* Interactive Accents */
--accent-primary:       #6366F1;          /* Soft Indigo */
--accent-primary-hover: #4F46E5;
--accent-pill-lavender: #ECEBFA;
```

### 3.2. Typography Hierarchy

1. **Brand Wordmark:** Sans-serif, bold/medium, wide letter-spacing (`M I M I R`).
2. **Hero Greeting Title:** High-contrast warm serif font (e.g. *Canela*, *Editorial New*, *Playfair Display*, or *Georgia*), 36px–42px, regular/medium weight. Creates an intimate, human, and craft-driven arrival experience.
3. **Overlines / Section Catchers:** Sans-serif, 11px, bold, uppercase, tracked (`0.08em`): `GOOD EVENING,`, `WHAT WOULD YOU LIKE TO DO?`, `RECENT TASKS`.
4. **Page Headings:** Sans-serif, 22px–26px, semi-bold (`font-weight: 600`), text-heading.
5. **Card Titles & Principle Statements:** Sans-serif, 14px–15px, medium (`font-weight: 500`).
6. **Subtitles & Metadata:** Sans-serif, 13px–14px, regular, muted slate.

### 3.3. Elevation, Radius & Spatial Geometry

- **Window Sizing:** Minimum 1024×640 px; Default recommended 1280×800 px.
- **Corner Radii:**
  - Cards & Panels: `16px`
  - Input Containers: `20px` to `9999px` (full pill)
  - Small Buttons & Badges: `8px`
  - Status Pills & Quick Filter Chips: `9999px`
- **Shadow System:**
  - Ambient Card Shadow: `0px 2px 8px rgba(0, 0, 0, 0.03), 0px 1px 2px rgba(0, 0, 0, 0.02)`
  - Elevated Popover / Search Bar: `0px 8px 24px -4px rgba(0, 0, 0, 0.05)`

---

## 4. Shell Architecture & Layout Grid

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  M I M I R                                                                 ─  □  ✕     │
├───────────────┬────────────────────────────────────────────────────────────────────────┤
│               │                                                                        │
│  [⌂ Home]     │  [Header Area: Editorial Title + Ambient Status Card / Action]        │
│               │                                                                        │
│  [◉ Observe]  │  [Central Interactive Component: Search / Form / Principle Deck]       │
│               │                                                                        │
│  [📖 Memory]   │  [Metric Tiles / Filter Rows]                                          │
│               │                                                                        │
│  [⎘ Replicate]│  [Content Cards / Historical Feed / Granular Permission Controls]      │
│               │                                                                        │
│  [🛡 Trust]    │                                                                        │
│               │                                                                        │
│  ───────────  │                                                                        │
│  [⚙ Settings] │                                                                        │
└───────────────┴────────────────────────────────────────────────────────────────────────┘
```

- **Sidebar (Left Rail):**
  - Width: Fixed `210px` – `224px`.
  - Background: Soft off-white canvas blending into window framing.
  - Structure:
    - Top: Brand Wordmark `M I M I R` (margin 24px top/left).
    - Middle: Vertical Nav List (`Home`, `Observe`, `Memory`, `Replicate`, `Trust`) with 8px vertical spacing.
    - Active Indicator: Lavender background pill (`#F0EFFF`) with violet text (`#4338CA`) and darkened icon.
    - Bottom: Separator line followed by pinned `Settings` tab button.
- **Main Canvas (Right Viewport):**
  - Margins: 32px horizontal, 28px vertical.
  - Scrollable `QScrollArea` / smooth container with hidden or hairline scrollbars.
  - Flexible multi-column cards arranged via fluid flex / grid layouts.

---

## 5. Detailed Screen Specifications

### 5.1. Screen 01: Home (`tabs/home.py`)
*The central cockpit providing ambient reassurance and instant exploration.*

1. **Hero Greeting Section:**
   - Overline: `GOOD EVENING,`
   - Hero User Name: `Arin` (or configured user profile name) rendered in high-end serif type.
   - Tagline: *"I'm MIMIR. I observe how you work, learn your style, and apply it when you need me."*
2. **Top-Right Status & Ingestion Card:**
   - Indicator: Pulsing status dot + `Resting · Not observing` (or `Observing · Recording telemetry`).
   - Context string: *"Your workspace isn't being observed right now."*
   - Primary Action Pill: `▷ Start Observing` / `⏹ Stop Observing`.
3. **Central Universal Input Bar:**
   - Pill container with leading sparkle icon `✦`, placeholder text: *"What would you like to explore?"*, and trailing voice input trigger (microphone icon).
   - Quick Action Chips underneath:
     - `✦ Explain this code`
     - `📄 Plan a feature`
     - `💡 Brainstorm ideas`
     - `··· More`
4. **Summary Metrics Row (3-Column Layout):**
   - **Principles learned:** Counter `2`, book icon, click-through chevron `>` to Memory.
   - **Episodes observed:** Counter `0`, waveform icon, click-through chevron `>` to Observe.
   - **Average confidence:** Score `0.60`, chart icon, click-through chevron `>`.
5. **Action Directory Cards ("WHAT WOULD YOU LIKE TO DO?"):**
   - 4-card grid routing directly to secondary tabs:
     - **Observe:** *"Watch repos, learn your habits."*
     - **Memory:** *"Your standards, inspectable."*
     - **Replicate:** *"Apply your style to new tasks."*
     - **Trust:** *"Stays local, you're in control."*

---

### 5.2. Screen 02: Observe (`tabs/observe.py`)
*Telemetry ingestion manager, workspace watcher, and session audit log.*

1. **Header & Context:**
   - Overline: `OBSERVE`
   - Title: `Control what MIMIR can see.`
   - Subtitle: *"Choose your workspace, manage observation, and review captured sessions."*
2. **Active Status Banner:**
   - Full-width rounded card indicating current telemetry state with quick `Start Observing` toggle.
3. **Sub-Navigation & Scope Filters:**
   - Pill Segmented Tabs: `[ Sessions ]` and `[ Applications ]`.
   - Timeframe Selector: `All time ▾` (options: *Today*, *Last 7 days*, *Last 30 days*, *All time*).
4. **Session Activity Stream:**
   - Interactive list cards showing application icon, session title, duration, and processing status badge:
     - **VS Code Session:** `Today, 4:00 PM · 42 minutes` → Badge: `Analyzed` (Soft blue).
     - **Chrome Session:** `Today, 2:10 PM · 18 minutes` → Badge: `Processed` (Soft green).
     - **Project: AlgoFight:** `Yesterday, 8:15 PM · 1 hour 12 minutes` → Badge: `Processed` (Soft green).
     - **Terminal Session:** `Yesterday, 6:01 PM · 24 minutes` → Badge: `Ignored` (Neutral gray).

---

### 5.3. Screen 03: Memory (`tabs/memory.py`)
*The Neuro-Symbolic Behavioral Principle Bank. Completely inspectable and editable.*

1. **Header & Context:**
   - Overline: `MEMORY`
   - Title: `What MIMIR knows about you.`
   - Subtitle: *"These are the principles learned from your work, with supporting evidence."*
2. **Search & Category Filtration:**
   - Search input: `🔍 Search principles...`
   - Tag filter dropdown: `All ▾` (categories: *Architecture*, *Code Style*, *Refactoring*, *Testing*, *Workflow*).
3. **Principle Cards Stack:**
   - Each principle represents an inferred behavioral invariant:
     - **Card Content:** Category icon (e.g. `</>`, `📄`, `📁`, `🔄`, `💡`), human-readable principle text.
     - **Evidence Metadata:** `Observed in X sessions · Confidence Y.YY`.
     - **Status Badge:**
       - `Confirmed` (Forest green on soft sage, high confidence >= 0.80).
       - `Learning` (Purple on soft lavender, emerging confidence 0.50–0.79).
       - `Unconfirmed` (Neutral slate, tentative observation < 0.50).
   - **Click Action:** Expands principle detail drawer to display the supporting episodes, git diff citations, reinforce button, contradict button, or delete rule.

---

### 5.4. Screen 04: Replicate (`tabs/replicate.py`)
*Applies learned principles to synthesize new code, scaffolding, or workflows in the user's authentic style.*

1. **Header & Context:**
   - Overline: `REPLICATE`
   - Title: `Apply your style to new tasks.`
   - Subtitle: *"Describe what you want, and MIMIR will use your learned principles."*
2. **Synthesis Prompt Panel:**
   - Large multi-line card: *"What would you like MIMIR to create or change?"* with voice trigger icon.
   - Targeting Controls:
     - Project context picker: `AlgoFight ▾`
     - Principle conditioning: `Auto-selected (3) ▾` (allowing the user to inspect or override which invariants guide synthesis).
     - Primary Action: `Generate →` (Indigo button).
3. **Recent Tasks Feed:**
   - Section title: `RECENT TASKS` with `View all >` link.
   - Task items showing icon, title, repository target, timestamp, and verdict state:
     - *Implement authentication flow* (AlgoFight · 2 days ago) → `Completed`.
     - *Improve error handling* (MIMIR · 4 days ago) → `Completed`.
     - *Design system ideas* (Personal Notes · 6 days ago) → `Reviewed`.

---

### 5.5. Screen 05: Trust & Boundaries (`tabs/trust.py`)
*User sovereignty, local data guarantees, and application allow-listing.*

1. **Header & Context:**
   - Overline: `TRUST`
   - Title: `Your data, your control.`
   - Subtitle: *"Manage permissions, storage, and privacy settings."*
2. **Access Control (Application Allow-List):**
   - Sub-label: *"Choose what MIMIR can observe and interact with."*
   - Rows featuring app icon, display name, target directory or scope, and instant on/off switches:
     - **VS Code:** `D:\Projects` → `[Toggle: ON]`
     - **Google Chrome:** `Browsing activity (titles & structure)` → `[Toggle: ON]`
     - **Terminal / Command Line:** `Commands and context (no sensitive content)` → `[Toggle: OFF]`
   - Action link: `+ Add application` (opens file/process picker).
3. **Data Storage & Privacy Policy:**
   - Sub-label: *"See how your data is stored and managed."*
   - **Storage location:** `Local (This device)` — emphasizing zero cloud exfiltration by default.
   - **Retention period:** `Until you delete`.
   - **Emergency Clear:** `Clear all learned data` → Destructive button `Clear` with confirmation modal.

---

### 5.6. Screen 06: Settings (`tabs/settings.py`)
*Fine-grained system calibration, voice persona, and integration hooks.*

1. **Header & Context:**
   - Overline: `SETTINGS`
   - Title: `Customize your experience.`
   - Subtitle: *"Control how MIMIR looks, sounds, and works."*
2. **Two-Column Settings Architecture:**
   - **Left Sub-Nav Category Rail:**
     - `General` (Selected)
     - `Voice`
     - `Model`
     - `Appearance`
     - `Shortcuts`
     - `Integrations`
   - **Right Configuration Panel (for General):**
     - Section `General`: *"Basic preferences for MIMIR."*
       - Assistant Name: Text input (`MIMIR`).
       - Language: Dropdown (`English (US) ▾`).
       - Start on system launch: `[Toggle: ON]`.
       - Show in system tray: `[Toggle: ON]`.
     - Section `Updates`:
       - Version indicator: `Version 1.0.0`.
       - Action: `Check for updates` button.

---

## 6. Implementation & Component Mapping (PyQt6 / QSS)

To align the codebase with these UI specifications, the components map directly to the `desktop_app/` module structure:

| UI Specification Element | PyQt6 Component Class | Module Location | Styling Rule / Token |
| :--- | :--- | :--- | :--- |
| **Sidebar Rail** | `QFrame` + `QButtonGroup` | `desktop_app/app.py` | `theme.qt_stylesheet()` (`#sidebar`, `#nav_btn`) |
| **Serene Hero Greeting** | `QLabel` (`font-family: Georgia/Serif`) | `desktop_app/tabs/home.py` | `#hero_name`, `#hero_overline` |
| **Status Pill Card** | `QFrame` + `QHBoxLayout` | `desktop_app/tabs/home.py` | `#status_card`, `#status_dot` |
| **Metric KPI Cards** | `ClickableCard(QFrame)` | `desktop_app/tabs/home.py` | `#kpi_card`, `#kpi_number` |
| **Session Feed Item** | `QFrame` + status badge | `desktop_app/tabs/observe.py` | `#session_row`, `#badge_processed` |
| **Principle Card** | `QFrame` + confidence bar | `desktop_app/tabs/memory.py` | `#principle_card`, `#badge_confirmed` |
| **Task Replicate Form** | `QTextEdit` + `QComboBox` | `desktop_app/tabs/replicate.py` | `#task_input`, `#generate_btn` |
| **App Permission Switch**| `QCheckBox` (styled as iOS switch) | `desktop_app/tabs/trust.py` | `#toggle_switch` |
| **Settings Sub-Nav** | `QListWidget` / vertical buttons | `desktop_app/tabs/settings.py` | `#settings_nav` |

---

## 7. Verification & Visual QA Checklist

- [x] High-resolution reference assets copied to `desktop_app/assets/ui/`.
- [x] All 6 individual screen mockups extracted and indexed.
- [x] Color tokens and typography hierarchy fully documented.
- [x] Component-level mappings established between mockups and PyQt6 modules.
- [x] Alignment verified with the core principle: *Observe. Infer. Learn. Replicate.*
