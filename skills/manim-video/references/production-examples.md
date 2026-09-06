# Production examples

These are optional starting points. Follow the requested design and verified render over example constants or absolute stylistic wording below. Resource paths beginning with `references/` are relative to the parent skill folder.

## Creative Direction

### Color Palettes

| Palette | Background | Primary | Secondary | Accent | Use case |
|---------|-----------|---------|-----------|--------|----------|
| **Classic 3B1B** | `#1C1C1C` | `#58C4DD` (BLUE) | `#83C167` (GREEN) | `#FFFF00` (YELLOW) | General math/CS |
| **Warm academic** | `#2D2B55` | `#FF6B6B` | `#FFD93D` | `#6BCB77` | Approachable |
| **Neon tech** | `#0A0A0A` | `#00F5FF` | `#FF00FF` | `#39FF14` | Systems, architecture |
| **Monochrome** | `#1A1A2E` | `#EAEAEA` | `#888888` | `#FFFFFF` | Minimalist |

### Animation Speed

| Context | run_time | self.wait() after |
|---------|----------|-------------------|
| Title/intro appear | 1.5s | 1.0s |
| Key equation reveal | 2.0s | 2.0s |
| Transform/morph | 1.5s | 1.5s |
| Supporting label | 0.8s | 0.5s |
| FadeOut cleanup | 0.5s | 0.3s |
| "Aha moment" reveal | 2.5s | 3.0s |

### Typography Scale

| Role | Font size | Usage |
|------|-----------|-------|
| Title | 48 | Scene titles, opening text |
| Heading | 36 | Section headers within a scene |
| Body | 30 | Explanatory text |
| Label | 24 | Annotations, axis labels |
| Caption | 20 | Subtitles, fine print |

### Fonts

**Use monospace fonts for all text.** Manim's Pango renderer produces broken kerning with proportional fonts at all sizes. See `references/visual-design.md` for full recommendations.

```python
MONO = "Menlo"  # define once at top of file

Text("Fourier Series", font_size=48, font=MONO, weight=BOLD)  # titles
Text("n=1: sin(x)", font_size=20, font=MONO)                  # labels
MathTex(r"\nabla L")                                            # math (uses LaTeX)
```

Minimum `font_size=18` for readability.

### Per-Scene Variation

Never use identical config for all scenes. For each scene:
- **Different dominant color** from the palette
- **Different layout** — don't always center everything
- **Different animation entry** — vary between Write, FadeIn, GrowFromCenter, Create
- **Different visual weight** — some scenes dense, others sparse


## Workflow

### Step 1: Plan (plan.md)

For a new video or substantial narrative redesign, write a short `plan.md`; use `scene-planning.md` when a detailed template helps. For a narrow change, reuse the existing plan and edit only the affected scene.

### Step 2: Code (script.py)

One class per scene. Every scene is independently renderable.

```python
from manim import *

BG = "#1C1C1C"
PRIMARY = "#58C4DD"
SECONDARY = "#83C167"
ACCENT = "#FFFF00"
MONO = "Menlo"

class Scene1_Introduction(Scene):
    def construct(self):
        self.camera.background_color = BG
        title = Text("Why Does This Work?", font_size=48, color=PRIMARY, weight=BOLD, font=MONO)
        self.add_subcaption("Why does this work?", duration=2)
        self.play(Write(title), run_time=1.5)
        self.wait(1.0)
        self.play(FadeOut(title), run_time=0.5)
```

Key patterns:
- **Subtitles** on every animation: `self.add_subcaption("text", duration=N)` or `subcaption="text"` on `self.play()`
- **Shared color constants** at file top for cross-scene consistency
- **`self.camera.background_color`** set in every scene
- **Clean exits** — FadeOut all mobjects at scene end: `self.play(FadeOut(Group(*self.mobjects)))`

### Step 3: Render

```bash
manim -ql script.py Scene1_Introduction Scene2_CoreConcept  # draft
manim -qh script.py Scene1_Introduction Scene2_CoreConcept  # production
```

### Step 4: Stitch

```bash
cat > concat.txt << 'EOF'
file 'media/videos/script/480p15/Scene1_Introduction.mp4'
file 'media/videos/script/480p15/Scene2_CoreConcept.mp4'
EOF
ffmpeg -y -f concat -safe 0 -i concat.txt -c copy final.mp4
```

### Step 5: Review

```bash
manim -ql --format=png -s script.py Scene2_CoreConcept  # preview still
```

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
