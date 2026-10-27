# MIMIR: Website Design System & Experience Specification
> **Document:** `DESIGN.md`  
> **Aesthetic Philosophy:** *Serene Intelligence — Calm, Light, Humane, and Weightless.*  
> **Atmosphere:** Breathable whitespace, gentle porcelain surfaces, soft natural light, and quiet elegance.

---

## 1. The Design Shift: Why Calm & Light?

MIMIR is not a dark hacker terminal or a dense developer tool.  
It is a **gentle, intelligent companion for anyone who works on a computer.**

The interface must feel like stepping into a sunlit, quiet studio:
* **No visual shouting:** No harsh neon glows, no dense black boxes, no intimidating jargon.
* **Effortless clarity:** Generous margins, soft neutral tones, breathable typography, and gentle micro-animations.
* **Warm and human:** Technology that recedes into the background so human thought and creativity can breathe.

---

## 2. Color System: Soft Porcelain, Linen & Slate

A peaceful, light-drenched palette that feels soothing on the eyes and lightweight on any device:

```css
:root {
  /* Canvas & Warm Atmosphere */
  --bg-canvas: #fcfcfd;            /* Pristine, soft warm porcelain */
  --bg-surface: #ffffff;           /* Clean white paper */
  --bg-subtle: #f5f6f8;            /* Soft mist / warm linen */
  --bg-card: rgba(255, 255, 255, 0.85);

  /* Borders & Separation (Soft Whispers) */
  --border-hairline: rgba(0, 0, 0, 0.06);
  --border-subtle: rgba(0, 0, 0, 0.10);
  --border-focus: #111827;

  /* Typography (High Legibility, Zero Harshness) */
  --text-primary: #111827;         /* Deep rich ink */
  --text-secondary: #4b5563;       /* Balanced warm slate */
  --text-muted: #9ca3af;           /* Quiet whisper grey */
  --text-link: #2563eb;

  /* Gentle Emotive Accents */
  --accent-soft-amber: #fef3c7;    /* Warm morning sunlight */
  --accent-amber-text: #b45309;
  --accent-soft-sage: #ecfdf5;     /* Quiet natural green */
  --accent-sage-text: #047857;
  --accent-soft-sky: #eff6ff;      /* Calm open sky */
  --accent-sky-text: #1d4ed8;

  /* Radii & Tactile Softness */
  --radius-sm: 8px;
  --radius-md: 14px;
  --radius-lg: 22px;
  --radius-pill: 9999px;

  /* Shadows (Natural Diffusion, Never Muddy) */
  --shadow-cloud: 0 4px 20px -2px rgba(0, 0, 0, 0.04);
  --shadow-hover: 0 12px 32px -4px rgba(0, 0, 0, 0.08);
}
```

---

## 3. Typography & Editorial Tone

* **Prose Font:** A graceful, optical sans-serif (**Inter**, **Plus Jakarta Sans**, or system `-apple-system, BlinkMacSystemFont`).
* **Tone of Voice:** Natural, compassionate, simple English. Avoid computational jargon (no "AST vectors" or "Bayesian priors" on the homepage).
* **The Guiding Narrative:**
  > *"You shouldn't have to explain yourself every time you sit down at your computer. MIMIR learns your rhythm, remembers how you like things done, and quietly lends a hand."*

---

## 4. Page Architecture: Breathable, Flowing Sections

```
┌────────────────────────────────────────────────────────────────────────┐
│ [Minimal Nav]   mimir.                 How it feels   Trust   [Try It] │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│                              THE HERO                                  │
│                 A little quieter. A lot more understood.               │
│                                                                        │
│            A companion on your desktop that learns how you work,       │
│                so you spend less time explaining and more              │
│                           time creating.                               │
│                                                                        │
│             ┌──────────────────────────────────────────────┐           │
│             │            THE TRANQUIL DESKTOP              │           │
│             │  [Soft Floating Face 🧐]                     │           │
│             │  "I noticed you always save summaries here.  │           │
│             │   Want me to take care of it?"               │           │
│             └──────────────────────────────────────────────┘           │
│                                                                        │
├────────────────────────────────────────────────────────────────────────┤
│  HOW IT FEELS (3 Clean Human Steps: Quietly Learns, Understands, Helps) │
├────────────────────────────────────────────────────────────────────────┤
│  A DIFFERENT APPROACH: Why you shouldn't have to prompt everything     │
├────────────────────────────────────────────────────────────────────────┤
│  THE COMPANION EXPERIENCE: Soft voice, gentle gestures, total peace    │
├────────────────────────────────────────────────────────────────────────┤
│  YOUR PRIVATE SPACE: Never leaves your laptop, pauses whenever you say │
├────────────────────────────────────────────────────────────────────────┤
│  AN OPEN INVITATION (Open Source, Friendly, Calm Footer)               │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Detailed Section Walkthrough

### 5.1. The Clean, Floating Navigation
* **Look:** Transparent glass with soft background blur (`backdrop-filter: blur(12px)`).
* **Elements:**
  * Clean, lowercase brand mark: `mimir.`
  * Simple links: `How it feels`, `The Companion`, `Privacy & Trust`.
  * Gentle action button: A clean, pill-shaped button with dark ink fill: `Get Started`.

---

### 5.2. The Hero: Welcoming & Airy
* **Headline:**  
  *“A little quieter.*  
  *A lot more understood.”*
* **Subhead:**  
  *“Most software asks you to type instructions over and over. MIMIR simply observes how you do things, learns your personal style, and steps in to help right when you need it.”*
* **The Visual Heartpiece:**
  * A light, beautifully framed desktop mockup on a soft pearl background.
  * Shows the **Friendly Companion Face**:
    * A smooth, rounded pill with soft, expressive cartoon-like eyes.
    * A gentle speech bubble:  
      *“I noticed how you organize your projects. Want me to tidy up this folder for you?”*
    * Two clean pill buttons: `[Yes, please]` & `[Not now]`.

---

### 5.3. "How It Feels": The Three Moments
Three spacious, light cards with rounded corners and generous breathing room:

1. **Quiet Observation:**  
   *It doesn't get in your way. While you write, edit, and organize, MIMIR quietly learns what you care about.*
2. **Deep Understanding:**  
   *It doesn't just memorize clicks. It understands the principles behind your decisions—your habits, your taste, and your standards.*
3. **Gentle Assistance:**  
   *When an unfamiliar task comes up, you don't have to write a giant prompt. MIMIR already knows how you'd do it.*

---

### 5.4. The Friendly Companion (Voice & Persona)
* Demonstrates how you interact with MIMIR through simple everyday language:
  * *You:* *"Hey, can you finish pushing these changes?"*
  * *MIMIR:* *"All done. Checked your tests first, just like you always do."*
* Shows the soft emotional states of the companion avatar:
  * 😴 **Resting:** Peaceful and quiet.
  * 🧐 **Noticing:** Thoughtful and attentive.
  * 🎙️ **Listening:** Attentive when you speak.
  * ✋ **Yielding:** Immediately pauses the moment your hand touches the mouse.

---

### 5.5. Total Privacy & Peace of Mind (Non-Negotiable)
People need to feel safe with an observing companion. This section uses clean, reassuring design:
* **Stays on your computer:** Your personal habits and work stay private on your laptop.
* **Never records passwords:** Smart filters strip out personal keys, passwords, and private notes.
* **Instant pause:** A single tap on the keyboard or mouse immediately pauses MIMIR. You are always in control.

---

## 6. Engineering Principles for the Webpage

1. **Lightweight (<20 KB):**
   * Single HTML file + single CSS file.
   * Zero JavaScript bloat, zero heavy tracking libraries.
   * Loads instantaneously, even on mobile or slower connections.
2. **Airy Whitespace:**
   * Generous vertical padding (`120px` between sections) giving the content room to breathe.
3. **Accessible & Responsive:**
   * Soft, high-contrast text meeting WCAG AAA legibility.
   * Beautiful, fluid layout across phones, laptops, and wide screens.
