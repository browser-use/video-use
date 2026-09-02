---
name: manim-video
description: "Production pipeline for original mathematical and technical animations using Manim Community Edition. Creates semantic explainers, algorithm visualizations, equation derivations, architecture diagrams, and data stories. Use when users request animated explanations, math animations, concept visualizations, algorithm walkthroughs, technical explainers, or programmatic geometric and mathematical animation."
version: 2.0.0
---

# Manim Video Production Pipeline

## Creative Standard

This is educational cinema. Every frame teaches. Every animation reveals structure.

**Before writing a single line of code**, articulate the narrative arc. What misconception does this correct? What is the "aha moment"? What visual story takes the viewer from confusion to understanding? The user's prompt is a starting point — interpret it with pedagogical ambition.

**Geometry before algebra.** Show the shape first, the equation second. Visual memory encodes faster than symbolic memory. When the viewer sees the geometric pattern before the formula, the equation feels earned.

**First-render excellence is non-negotiable.** The output must be visually clear and aesthetically cohesive without revision rounds. If something looks cluttered, poorly timed, or like "AI-generated slides," it is wrong.

**Continuity carries meaning.** Keep important objects alive across multiple teaching beats. Transform their state, update their linked representations together, and move the camera or dim context to direct attention. Reset an object only when that reset teaches something.

**Breathing room.** Pause after a meaningful reveal for as long as the viewer needs to comprehend it. Every hold must serve reading, comparison, prediction, or payoff rather than mechanically following every animation.

**Cohesive visual language.** All scenes share a color palette, consistent typography sizing, matching animation speeds. A technically correct video where every scene uses random different colors is an aesthetic failure.

**Narration is part of a topic-only explainer.** Unless the user explicitly asks
for silence, generate audible human or synthetic narration, synchronize the
teaching beats to it, and include matching captions. A silent audio file is a
hard failure, not a narration track.

## Prerequisites

Run `scripts/setup.sh` to verify all dependencies. Requires: Python 3.10+, Manim Community Edition v0.20+ (`pip install manim`), LaTeX (`texlive-full` on Linux, `mactex` on macOS), and ffmpeg. Reference docs tested against Manim CE v0.20.1.

## Modes

| Mode | Input | Output | Reference |
|------|-------|--------|-----------|
| **Concept explainer** | Topic/concept | Narrated explanation with geometric intuition | `references/concept-explainer.md` |
| **Equation derivation** | Math expressions | Step-by-step animated proof | `references/equations.md` |
| **Algorithm visualization** | Algorithm description | Step-by-step execution with data structures | `references/graphs-and-data.md` |
| **Data story** | Data/metrics | Animated charts, comparisons, counters | `references/graphs-and-data.md` |
| **Architecture diagram** | System description | Components building up with connections | `references/mobjects.md` |
| **Paper explainer** | Research paper | Key findings and methods animated | `references/scene-planning.md` |
| **3D visualization** | 3D concept | Rotating surfaces, parametric curves, spatial geometry | `references/camera-and-3d.md` |

For a narrated concept explainer, read `references/concept-explainer.md` completely
before planning. It is the focused production contract for narration locking,
visual-beat density, independently renderable narrative chapters, derived-value
checks, and targeted draft iteration. Use `assets/teaching.py` for semantic
continuity and the components in `assets/domains/`; retain
`assets/concept_explainer.py` for caption-safe layout, shared-frame composition,
and collision checks.

## Stack

Single Python script per project. No browser, no Node.js, no GPU required.

| Layer | Tool | Purpose |
|-------|------|---------|
| Core | Manim Community Edition | Scene rendering, animation engine |
| Math | LaTeX (texlive/MiKTeX) | Equation rendering via `MathTex` |
| Video I/O | ffmpeg | Scene stitching, format conversion, audio muxing |
| TTS | ElevenLabs / Qwen3-TTS (optional) | Narration voiceover |

## Pipeline

```
RESEARCH --> NARRATE --> VISUAL PLAN --> AUTHOR --> PREVIEW --> RENDER --> COMPOSE --> REVIEW
```

1. **RESEARCH** — Verify claims, examples, and derived values.
2. **NARRATE** — Write and generate audible narration that progresses from the misconception to a specific visual payoff; omit speech only when the user explicitly requests silence.
3. **VISUAL PLAN** — Complete the required `edit/visual_plan.md` contract.
4. **AUTHOR** — Build one independently renderable class per narrative chapter and mark its internal beats with `next_section()` or `begin_beat()`.
5. **PREVIEW** — Render only the chapters under repair with `scripts/preview_scene.py`.
6. **RENDER** — Render the complete visual base at delivery quality.
7. **COMPOSE** — Add approved footage when it is more informative than illustration, then use the existing EDL workflow for narration and captions.
8. **REVIEW** — Inspect the complete video and verify that its speech track is audible before loudness normalization; the final quality decision is never made from isolated preview clips.

## Project Structure

```
project-name/
  edit/
    visual_plan.md       # Required semantic teaching plan for original explainers
    animations/          # Chapter source and production renders
    verify/              # Targeted chapter previews and review evidence
    edl.json             # Existing composition contract; unchanged
    final.mp4
```

## Creative Direction

### Semantic Theme

Create one `VisualTheme` for the approved direction. Its background, text,
muted, primary, secondary, accent, warning, and title/body/label font roles keep
meaning consistent without imposing a house palette. Components require the
same theme instance and never embed another creator's palette or branding.

### Animation Timing Starting Points

| Context | Typical motion | Hold purpose |
|---------|----------------|--------------|
| Establish a model | 1–2s | orient to its parts |
| Key equation reveal | 1.5–2.5s | connect notation to the model |
| Transform/morph | 1–2s | compare initial and resulting state |
| Supporting label | 0.5–1s | read only when it carries new meaning |
| "Aha moment" reveal | 2–3s | study the final relationship |

These are starting points. Narration alignment and comprehension decide the
real timing; do not add the listed hold mechanically.

### Typography Scale

| Role | Font size | Usage |
|------|-----------|-------|
| Title | 48 | Scene titles, opening text |
| Heading | 36 | Section headers within a scene |
| Body | 30 | Explanatory text |
| Label | 24 | Annotations, axis labels |
| Caption | 20 | Subtitles, fine print |

### Fonts

Typography follows the approved visual direction. Define title, body, and label
roles in `VisualTheme`, confirm the selected fonts exist on the render host, and
inspect their real output at medium quality. Monospace is appropriate for code
or an intentional aesthetic; it is not a universal requirement. Keep ordinary
text at `font_size=18` or larger and retain all width and frame-safety checks.

### Continuity

- Preserve a concept's color role and screen position unless changing either one teaches something.
- Carry important objects into later beats and transform them instead of recreating completed diagrams.
- Update coupled numbers, plots, arrows, and labels from the same underlying value.
- Use animation vocabulary for meaning: construction, transfer, comparison, causality, and state change.

## Workflow

### Step 1: Plan (`edit/visual_plan.md`)

Before any original explainer code, complete `edit/visual_plan.md`. See
`references/concept-explainer.md` and `references/scene-planning.md`. Clip-editing
workflows do not require this artifact.

When a chapter shares the frame with sourced footage, use
`assets/concept_explainer.py`. Its `composition_regions()`,
`assert_inside_region()`, and `normalized_bounds()` helpers use the same
normalized rectangles as the renderer, so the EDL can protect the teaching
visual from picture-in-picture collisions.

### Step 2: Code (script.py)

Use one class per narrative chapter. A chapter is independently renderable and
contains multiple named teaching beats; it is not a short title-and-card scene.

```python
from manim import *
from teaching import TeachingScene, VisualTheme
from domains import MatrixMap

THEME = VisualTheme(
    background="#10141C", text="#F6F7FA", muted="#7F8899",
    primary="#5E8BFF", secondary="#57C4A5", accent="#F2C14E",
    warning="#E56B6F", title_font="Inter", body_font="Inter",
    label_font="Inter",
)

class CompositionChapter(TeachingScene):
    def construct(self):
        self.theme = THEME
        self.camera.background_color = THEME.background
        transformation = self.remember("transformation", MatrixMap(THEME))
        self.begin_beat("first transformation")
        self.play(FadeIn(transformation))
        self.begin_beat("composition payoff")
        self.play(transformation.apply_to((1, 1)))
        self.hold(2, purpose="compare input and composed output")
```

Key patterns:
- **Narration mapping** for every significant visual action; captions remain composed last through the existing EDL renderer
- **One shared `VisualTheme`** for semantic color and typography roles
- **Named objects and beats** through `remember()` and `begin_beat()`
- **Portable easing imports** — import `ease_out_cubic` and
  `ease_in_out_cubic` from the copied concept-explainer asset; do not assume
  unqualified easing names are exported by `from manim import *`
- **Continuity at boundaries** — hold or transform the payoff frame; fade only objects whose departure has narrative meaning
- **Frame-safe endings** — call `assert_inside_frame()` and
  `assert_no_overlap()` on each chapter's critical teaching states, including
  the payoff frame.

### Step 3: Render

```bash
python skills/manim-video/scripts/preview_scene.py \
  edit/animations/script.py CompositionChapter
manim -qh edit/animations/script.py CompositionChapter  # production
```

### Step 4: Stitch

```bash
cat > concat.txt << 'EOF'
file 'media/videos/script/480p15/CompositionChapter.mp4'
file 'media/videos/script/480p15/SystemsChapter.mp4'
EOF
ffmpeg -y -f concat -safe 0 -i concat.txt -c copy final.mp4
```

### Step 5: Review

```bash
python skills/manim-video/scripts/preview_scene.py \
  edit/animations/script.py CompositionChapter SystemsChapter
```

The preview command writes only below `edit/verify/` and produces a low-quality
chapter video, first and final frames, a five-frame contact sheet, media
metadata, render status, and saved `next_section()` metadata. It selects whole
scene classes; Manim does not support arbitrary section-only rendering here.

## Critical Implementation Notes

### Raw Strings for LaTeX
```python
# WRONG: MathTex("\frac{1}{2}")
# RIGHT:
MathTex(r"\frac{1}{2}")
```

### buff >= 0.5 for Edge Text
```python
label.to_edge(DOWN, buff=0.5)  # never < 0.5
```

### FadeOut Before Replacing Text
```python
self.play(ReplacementTransform(note1, note2))  # not Write(note2) on top
```

### Never Animate Non-Added Mobjects
```python
self.play(Create(circle))  # must add first
self.play(circle.animate.set_color(RED))  # then animate
```

## Performance Targets

| Quality | Resolution | FPS | Speed |
|---------|-----------|-----|-------|
| `-ql` (draft) | 854x480 | 15 | 5-15s/scene |
| `-qm` (medium) | 1280x720 | 30 | 15-60s/scene |
| `-qh` (production) | 1920x1080 | 60 | 30-120s/scene |

Always iterate at `-ql`. Only render `-qh` for final output.

## References

| File | Contents |
|------|----------|
| `references/concept-explainer.md` | Required narrated-explainer contract: audio lock, visual beats, timing, correctness, and efficient iteration |
| `references/teaching-api.md` | Semantic scene API, linked values, continuity helpers, and domain component index |
| `references/animations.md` | Core animations, rate functions, composition, `.animate` syntax, timing patterns |
| `references/mobjects.md` | Text, shapes, VGroup/Group, positioning, styling, custom mobjects |
| `references/visual-design.md` | Semantic design principles, attention, layout patterns, themes, and typography |
| `references/equations.md` | LaTeX in Manim, TransformMatchingTex, derivation patterns |
| `references/graphs-and-data.md` | Axes, plotting, BarChart, animated data, algorithm visualization |
| `references/camera-and-3d.md` | MovingCameraScene, ThreeDScene, 3D surfaces, camera control |
| `references/scene-planning.md` | Narrative arcs, chapter continuity, beat planning, and visual-plan template |
| `references/rendering.md` | CLI reference, quality presets, ffmpeg, voiceover workflow, GIF export |
| `references/troubleshooting.md` | LaTeX errors, animation errors, common mistakes, debugging |
| `references/animation-design-thinking.md` | When to animate vs show static, decomposition, pacing, narration sync |
| `references/updaters-and-trackers.md` | ValueTracker, add_updater, always_redraw, time-based updaters, patterns |
| `references/paper-explainer.md` | Turning research papers into animations — workflow, templates, domain patterns |
| `references/decorations.md` | SurroundingRectangle, Brace, arrows, DashedLine, Angle, annotation lifecycle |
| `references/production-quality.md` | Pre-code, pre-render, post-render checklists, spatial layout, color, tempo |

---

## Creative Divergence (use only when user requests experimental/creative/unique output)

If the user asks for creative, experimental, or unconventional explanatory approaches, select a strategy and reason through it BEFORE designing the animation.

- **SCAMPER** — when the user wants a fresh take on a standard explanation
- **Assumption Reversal** — when the user wants to challenge how something is typically taught

### SCAMPER Transformation
Take a standard mathematical/technical visualization and transform it:
- **Substitute**: replace the standard visual metaphor (number line → winding path, matrix → city grid)
- **Combine**: merge two explanation approaches (algebraic + geometric simultaneously)
- **Reverse**: derive backward — start from the result and deconstruct to axioms
- **Modify**: exaggerate a parameter to show why it matters (10x the learning rate, 1000x the sample size)
- **Eliminate**: remove all notation — explain purely through animation and spatial relationships

### Assumption Reversal
1. List what's "standard" about how this topic is visualized (left-to-right, 2D, discrete steps, formal notation)
2. Pick the most fundamental assumption
3. Reverse it (right-to-left derivation, 3D embedding of a 2D concept, continuous morphing instead of steps, zero notation)
4. Explore what the reversal reveals that the standard approach hides
