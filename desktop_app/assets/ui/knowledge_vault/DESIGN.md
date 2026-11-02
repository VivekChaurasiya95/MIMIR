---
name: Institutional Assurance
colors:
  surface: '#faf8ff'
  surface-dim: '#d2d9f4'
  surface-bright: '#faf8ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f2f3ff'
  surface-container: '#eaedff'
  surface-container-high: '#e2e7ff'
  surface-container-highest: '#dae2fd'
  on-surface: '#131b2e'
  on-surface-variant: '#464554'
  inverse-surface: '#283044'
  inverse-on-surface: '#eef0ff'
  outline: '#777586'
  outline-variant: '#c7c4d7'
  surface-tint: '#5148d7'
  primary: '#2a14b4'
  on-primary: '#ffffff'
  primary-container: '#4338ca'
  on-primary-container: '#c1beff'
  inverse-primary: '#c3c0ff'
  secondary: '#006a63'
  on-secondary: '#ffffff'
  secondary-container: '#99efe5'
  on-secondary-container: '#006f67'
  tertiary: '#5c2f00'
  on-tertiary: '#ffffff'
  tertiary-container: '#7d4200'
  on-tertiary-container: '#ffb477'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e3dfff'
  primary-fixed-dim: '#c3c0ff'
  on-primary-fixed: '#100069'
  on-primary-fixed-variant: '#372abf'
  secondary-fixed: '#9cf2e8'
  secondary-fixed-dim: '#80d5cb'
  on-secondary-fixed: '#00201d'
  on-secondary-fixed-variant: '#00504a'
  tertiary-fixed: '#ffdcc3'
  tertiary-fixed-dim: '#ffb77d'
  on-tertiary-fixed: '#2f1500'
  on-tertiary-fixed-variant: '#6e3900'
  background: '#faf8ff'
  on-background: '#131b2e'
  surface-variant: '#dae2fd'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 36px
    fontWeight: '600'
    lineHeight: 44px
    letterSpacing: -0.02em
  display-lg-mobile:
    fontFamily: Inter
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
    letterSpacing: -0.01em
  headline-lg:
    fontFamily: Inter
    fontSize: 30px
    fontWeight: '600'
    lineHeight: 38px
    letterSpacing: -0.015em
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.005em
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: 0em
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0em
  body-sm:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: 0em
  label-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.02em
  caption:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.03em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 1.5rem
  gutter-sm: 1rem
  gutter-lg: 2rem
  margin: 2rem
  margin-mobile: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2rem
  space-2xl: 3rem
---

## Brand & Style

This design system establishes an authentic, institutional-grade visual foundation tailored for enterprise governance, risk mitigation, and compliance orchestration. The design rejects juvenile dark-mode tropes, sci-fi glowing borders, and developer-terminal aesthetics in favor of authoritative, human-centered clarity. 

The aesthetic delivers three core qualities:
1. **Unquestionable Credibility:** Calm, stable light surfaces engineered to put risk officers, operations leads, and auditors at ease during mission-critical decisions.
2. **Accessible Sophistication:** Precision layouts balancing high-density data tables with generous, structured whitespace that reduces cognitive fatigue.
3. **Actionable Confidence:** Clear state distinctions and intentional focal points that make complex multi-step workflows feel deterministic and safe.

## Colors

The palette pairs deep royal indigo with balanced slate neutrals and purposeful semantic signals. The dominant background is a light, clean canvas that optimizes legibility across prolonged working sessions.

### Functional Roles
- **Primary (`#4338CA` - Royal Indigo):** Anchors primary actions, active navigational landmarks, and brand authority. Expresses deliberate intent without aggressive contrast.
- **Secondary (`#0F766E` - Deep Pine Teal):** Designates verified states, structural policy badges, and secondary analytical focal points.
- **Tertiary (`#D97706` - Warm Ochre):** Flags pending reviews, manual interventions, and soft notices requiring user triage.
- **Neutral Base (`#0F172A` - Slate Charcoal):** Drives typography and critical interface borders. Paired with sub-tones `#334155` (Slate Secondary) and `#64748B` (Slate Muted) for layered typographic contrast.
- **Structural Borders (`#E2E8F0` and `#EAECF0`):** Define component perimeters with precision, avoiding visual heaviness.
- **Semantic Signals:** Success is represented by balanced emerald (`#059669`), warning by warm amber (`#D97706`), and destructive critical errors by deep crimson (`#DC2626`).

## Typography

Typography relies entirely on the universal clarity, tabular precision, and neutral tone of Inter. Type hierarchies are strict and utilitarian, designed to handle multi-line compliance clauses, policy rules, and financial figures without awkward breaks.

### Rules of Usage
- **Numerics & Records:** Always activate tabular lining figures (`font-variant-numeric: tabular-nums`) for currency, audit timestamps, and percentage metrics to guarantee vertical alignment in lists and summary cards.
- **Weights:** Restricted to 400 (Regular), 500 (Medium), and 600 (Semi-bold). Avoid weights above 600 to preserve a calm, sophisticated enterprise aesthetic.
- **Readability Rules:** Display scales use slightly negative letter-spacing for structural tightness. Smaller UI labels and micro-captions use expanded letter-spacing to preserve optical legibility at high density.

## Layout & Spacing

The structural layout uses a disciplined 12-column responsive grid underpinned by an 8pt base unit (with a 4pt sub-grid for fine internal component alignments).

### Breakpoints & Fluidity
- **Desktop (1280px+):** 12-column layout with 24px gutters, fixed-width side navigation (256px), and max container bounds capped at 1440px for single-pane dashboards. Split-view panes (policy rule vs. preview output) divide into 6/6 or 5/7 columns.
- **Tablet (768px - 1279px):** 8-column layout with 16px gutters and 24px outer margins. Multi-column audit sidecars collapse into sliding overlay panels.
- **Mobile (Below 768px):** 4-column layout with 16px gutters and 16px outer margins. Split comparisons convert from horizontal side-by-side to stacked vertical before-and-after cards with sticky segment toggles.

## Elevation & Depth

Visual hierarchy relies primarily on clean surface segmentation, subtle 1px border definitions, and restrained, ambient drop shadows. Heavy blurs and floating neomorphic layers are strictly avoided.

### Surface Tiers & Shadows
- **Base Canvas (`Level 0`):** `#F8FAFC` (Slate Tint). Serves as the backdrop for all content groupings.
- **Flat Surface (`Level 1`):** `#FFFFFF` (Pure White). Applied to cards, table bodies, and toolbars. Paired with a subtle perimeter line (`1px solid #EAECF0`) and an ambient micro-shadow: `0 1px 2px 0 rgba(15, 23, 42, 0.04)`.
- **Raised Interactive (`Level 2`):** Dropdowns, popovers, active search filters, and standard flyouts use `#FFFFFF` backed by `0 4px 6px -1px rgba(15, 23, 42, 0.06), 0 2px 4px -2px rgba(15, 23, 42, 0.04)` with a `#E2E8F0` border.
- **Modal Overlay (`Level 3`):** Critical review dialogs and confirmation modals sit atop a dimmed background (`rgba(15, 23, 42, 0.4)`) with a deep ambient shadow: `0 20px 25px -5px rgba(15, 23, 42, 0.1), 0 8px 10px -6px rgba(15, 23, 42, 0.04)`.

## Shapes

The design system employs a soft, controlled geometry (Level 1 roundedness). This creates a dependable, engineered feel while eliminating the harshness of raw 0px corners and avoiding the playful consumer quality of fully rounded pills.

### Geometry Specifications
- **Standard UI Elements (`0.25rem` / `4px`):** Buttons, inputs, inline select fields, segmented switch items, and table highlight indicators.
- **Container Elements (`0.5rem` / `8px`):** Content cards, audit timeline rows, comparison viewports, notification banners, and modal dialogs.
- **Outer Shells (`0.75rem` / `12px`):** Application canvases, large drawer trays, and hero overview containers.
- **Full Radius (`9999px`):** Reserved exclusively for compact status badges, avatar icons, and numeric indicator counters.

## Components

### Buttons
- **Primary:** Solid `#4338CA` background, `#FFFFFF` text, `4px` radius, `0.5rem` vertical by `1rem` horizontal padding. Hover shifts to `#3730A3` with subtle focus-ring (`0 0 0 3px rgba(67, 56, 202, 0.15)`).
- **Secondary / Outline:** `#FFFFFF` background, `1px solid #D1D5DB`, `#334155` text. On hover, background shifts to `#F8FAFC` and border deepens to `#94A3B8`.
- **Destructive:** `#FFFFFF` surface with `1px solid #FCA5A5` and `#DC2626` text, shifting to `#FEF2F2` on hover.

### Status Pills
- Human-readable labels (e.g., "Active Policy", "Under Review", "Automated Exemption") built with full-pill radii (`9999px`), `0.25rem 0.625rem` padding, and `12px` semi-bold text.
- **Positive:** `#ECFDF5` background, `#047857` text, subtle `1px solid #A7F3D0`.
- **Pending/Notice:** `#FFFBEB` background, `#B45309` text, subtle `1px solid #FDE68A`.
- **Critical:** `#FEF2F2` background, `#B91C1C` text, subtle `1px solid #FECACA`.

### Segmented Controls & Tabs
- Mounted in an enclosed `#F1F5F9` track with `4px` padding. Active items feature a `#FFFFFF` raised pill, `1px solid rgba(0, 0, 0, 0.04)`, `0 1px 2px rgba(0, 0, 0, 0.05)` shadow, and dark `#0F172A` text. Inactive items use `#64748B` text with an invisible border.

### Toggle Cards
- Dual-state interactive containers with `8px` border radius, `1px solid #E2E8F0`, and white surface. Left-aligned title with descriptive human-friendly body copy, right-aligned robust mechanical switch. When checked, the border highlights to `#4338CA` with an ambient `#EEF2FF` wash.

### Comparison Previews (Side-by-Side Safeguards)
- Two symmetric panels enclosed within a shared master card. The left panel shows the "Original Rule / Input" on a `#F8FAFC` slate canvas with muted slate framing; the right panel shows the "Safeguarded Output / Automated Action" on a crisp `#FFFFFF` canvas bordered by `#0F766E`. Clear status indicators sit atop both viewports.

### Form Inputs & Checkboxes
- **Inputs:** `1px solid #CBD5E1`, white surface, `4px` radius, `14px` text with `20px` height. Focus state activates a `#4338CA` border with a 3px soft indigo halo.
- **Checkboxes:** `16px x 16px` square with `3px` corner radius. Off state uses `1px solid #CBD5E1`; checked state fills with `#4338CA` and displays a crisp white checkmark icon.