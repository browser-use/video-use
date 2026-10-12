---
name: video-use
description: Agent-native video editing — dark harness UI, orange signal accent, mono timeline craft
colors:
  accent: "#ff6b35"
  accent-soft: "rgba(255,107,53,0.15)"
  accent-mute: "rgba(255,107,53,0.08)"
  signal-blue: "#6b9fff"
  signal-blue-soft: "rgba(140,180,255,0.12)"
  ink: "#0f0f16"
  surface-deep: "#13131a"
  surface-panel: "#1a1a20"
  surface-frame: "#252530"
  stone-900: "#1c1917"
  stone-800: "#292524"
  stone-700: "#44403c"
  stone-600: "#57534e"
  stone-500: "#78716c"
  stone-400: "#a8a29e"
  stone-300: "#d6d3d1"
  stone-200: "#e7e5e4"
  stone-50: "#fafaf9"
  white: "#ffffff"
typography:
  display:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "32px"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 700
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 700
    letterSpacing: "0.2em"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
  label:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "10px"
    fontWeight: 600
    letterSpacing: "0.05em"
rounded:
  sm: "4px"
  md: "6px"
  lg: "12px"
  full: "9999px"
spacing:
  xs: "6px"
  sm: "10px"
  md: "20px"
  lg: "28px"
  xl: "40px"
  panel-x: "40px"
components:
  chip-accent:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
    rounded: "{rounded.sm}"
    padding: "4px 8px"
  panel-dark:
    backgroundColor: "{colors.stone-900}"
    textColor: "{colors.white}"
    rounded: "{rounded.lg}"
    padding: "{spacing.lg} {spacing.panel-x}"
  canvas-page:
    backgroundColor: "{colors.stone-200}"
    textColor: "{colors.stone-800}"
  sheet:
    backgroundColor: "{colors.stone-50}"
    textColor: "{colors.stone-800}"
    rounded: "0px"
---

# Design System: video-use

## Overview

**Creative North Star: "The Harness Console"**

video-use’s visual language is the *editing harness the agent and human share*: a dark instrument panel where transcript, waveform, and filmstrip are first-class—not a consumer NLE skin and not a marketing landing-page gradient. The poster (`poster.html`) and banner establish a craft-tool aesthetic: stone neutrals, one hot orange signal (`#ff6b35`), cool blue for audio/word glyphs, and JetBrains Mono for anything machine-readable.

Personality is precise, technical, and calm under density. Surfaces stack as tonal layers (stone-50 sheet on stone-200 desk; stone-900 chrome for “editor chrome”). Accent is scarce and semantic: section kicker, speaker tags, “ON-DEMAND COMPOSITE” badges—not decorative blobs.

**Key Characteristics:**
- Dark console chrome + light paper desk (dual-surface)
- Single orange accent; blue reserved for audio/text-of-sound
- Inter for human UI; JetBrains Mono for paths, timecodes, words
- Wide max canvas (~1400px) with generous horizontal padding (40px)
- Soft sheet shadow; panels use borders more than heavy drop shadows

## Colors

Palette is warm stone neutrals with a single flame accent and a cool signal blue for waveform literacy.

### Primary
- **Signal Orange** (`#ff6b35`): Brand accent — kickers (“The Harness”), emphasis spans, speaker labels, live badges. Keep rare.

### Secondary
- **Waveform Blue** (`#6b9fff`): Audio energy and word labels on dark timeline composites. Not a second brand; a data channel.

### Neutral
- **Void Ink** (`#0f0f16`): Deepest timeline well / waveform trough.
- **Console Deep** (`#13131a` / `#1a1a20`): Filmstrip wells and panel guts.
- **Stone 900–700** (`#1c1917` → `#44403c`): Editor chrome, bars, borders on dark UI.
- **Stone 500–400**: Secondary mono captions and section labels.
- **Stone 200 / 50** (`#e7e5e4` / `#fafaf9`): Page desk and content sheet.
- **White** (`#ffffff`): Primary type on dark chrome.

### Named Rules
**The One Flame Rule.** Orange is the only warm accent. If everything is orange, nothing is a signal.

**The Channel Split Rule.** Blue = sound/time data. Orange = human/agent attention. Don’t swap roles.

## Typography

**Display Font:** Inter (system-ui, sans-serif)  
**Body Font:** Inter  
**Label/Mono Font:** JetBrains Mono (ui-monospace, monospace)

**Character:** Inter carries bold editorial titles on dark chrome; Mono owns filesystem paths, timecodes, and ASR words so the eye never confuses prose with machine truth.

### Hierarchy
- **Display** (800, 32px, leading-none, tight tracking): Poster/page titles on stone-900 headers.
- **Headline** (700, ~13px): Insight-bar emphasis and in-panel titles.
- **Title / Kicker** (700–800, 10–11px, uppercase, tracking ~3px): Section labels (“What the LLM actually sees”), brand kicker (“The Harness”).
- **Body** (400, 13px): Insight copy and explanatory prose on mid chrome.
- **Label / Mono** (500–700, 10–11px): `video-use / SKILL.md`, composite filenames, word ticks, SPEAKER tags.

### Named Rules
**The Mono Means Machine Rule.** Paths, timestamps, packed transcript glyphs, and waveform word labels stay in JetBrains Mono.

## Layout

- **Desk:** Full-bleed `stone-200` with vertical padding (`py-10`), content centered.
- **Sheet:** `max-w-[1400px]`, `stone-50`, 1px `stone-300` border, light `shadow-sm`.
- **Horizontal rhythm:** Primary content padding `px-10` (40px).
- **Stack:** Header chrome → insight bar → labeled sections → dark nested panels (timeline mock).
- **Density:** Information-dense inside dark panels; airy section labels above them.
- **Responsive:** Poster is a fixed explanatory artifact; prefer horizontal scroll or scale-down of SVG composites over reflowing the filmstrip metaphor into cards.

## Elevation & Depth

Hybrid: **tonal layering first**, thin borders second, one soft sheet shadow for the paper stage. Dark UI does not rely on large drop shadows; depth is stone-900 / 800 / 700 steps plus 1px borders (`border-stone-700`).

### Shadow Vocabulary
- **Sheet lift** (`box-shadow: shadow-sm` / subtle): The light content card over the desk only.
- **No ambient float on console chrome:** Dark panels sit flush via border + fill contrast.

### Named Rules
**The Flat Console Rule.** Inside the editor metaphor, prefer border + tone over theatrical shadows.

## Shapes

- **Sheet:** Square outer stage (no large page radius).
- **Console panels:** `rounded-xl` (~12px) with clipped overflow for timeline mocks.
- **Film cells / chips:** `rounded` 4–6px; speaker pills ~4px.
- **Traffic lights:** Full circles (`rounded-full`) at 10px, muted stone-600.
- **Borders:** 1px stone-300 on light; stone-700 on dark. Accent used as stroke only at low alpha on speaker lanes.

## Components

### Kickers & badges
- Uppercase, wide tracking, accent or stone-400.
- Mono badges (e.g. `ON-DEMAND COMPOSITE PNG`) use accent + bold mono at 10px.

### Dark panel (timeline chrome)
- **Shape:** rounded-xl, border stone-700, bg stone-900.
- **Title bar:** stone-800, bottom border, mono path string left, accent status right.
- **Internal pad:** ~20px around SVG stage.

### Insight bar
- Full-width stone-800 strip; centered 13px stone-300 copy; bold white + accent spans for key terms.

### Speaker chip
- Soft accent fill (`rgba(255,107,53,0.08–0.15)`), accent text, mono 10px, slight border at low accent alpha.

### Waveform stage
- Void ink well; blue stroke polyline; soft blue fill; silence gaps as orange wash at ~6% alpha.

### Traffic-light dots
- Decorative window chrome only; never semantic status (avoid green/yellow/red meaning).

## Do's and Don'ts

### Do:
- **Do** keep orange scarce and semantic (kickers, attention, speaker identity).
- **Do** put machine strings in JetBrains Mono.
- **Do** stage work on a light sheet over stone-200 when explaining; use dark console for “what the model sees.”
- **Do** preserve the dual metaphor: paper brief + instrument panel.
- **Do** treat timeline composites as first-class visuals (filmstrip + waveform + words).

### Don't:
- **Don't** introduce a second warm brand color or purple AI gradients.
- **Don't** set long prose in mono.
- **Don't** dump full-frame video UI chrome; the product shows *composites*, not a fake Premiere skin.
- **Don't** use accent as large fill backgrounds.
- **Don't** invent light-mode-only components that break the console metaphor without a PRODUCT.md reason.
