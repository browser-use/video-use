# Semantic Teaching API

Copy `assets/teaching.py` and the needed `assets/domains/` modules into an
original explainer workspace. Pass one shared `VisualTheme` to every component.
These APIs manage semantic identity, linked state, attention, and continuity;
ordinary Manim remains available for custom visuals.

## Foundations

- `VisualTheme` — background, text, muted, primary, secondary, accent, warning,
  and title/body/label font roles.
- `SemanticMobject` — register and retrieve stable named parts and anchors with
  `register_part()`, `part()`, `register_anchor()`, and `anchor()`. Use
  `highlight()` and `restore_opacity()` for local semantic focus.
- `LinkedValue` — bind named mobjects to one `ValueTracker`; use `set_value()` or
  `animate_to()` and suspend, resume, or clear bindings when a representation
  deliberately stops following the value.
- `TeachingScene` — a `MovingCameraScene` with `remember()`, `recall()`,
  `begin_beat()`, `focus_on()`, `restore_context()`, `highlight()`,
  `restore_highlight()`, `transform_object()`, and `hold()`.
- `TeachingThreeDScene` — the same registry, beat, focus, highlight,
  transformation, and hold contract on `ThreeDScene`.

## Domain Components

| Family | Components | Representative actions |
|---|---|---|
| Math | `NumberLineModel`, `VectorMap`, `MatrixMap`, `LinkedPlot`, `ProbabilityMass` | move a value, apply a matrix, update a plotted point, transfer probability |
| Algorithms and AI | `ArrayModel`, `GraphModel`, `StateMachine`, `TokenFlow`, `NeuralLayer` | swap values, visit/traverse, transition, mix context, activate |
| Software systems | `RequestFlow`, `ServiceGraph`, `QueueModel`, `DataPipeline` | advance/send a request, enqueue/dequeue, propagate data |
| Physics | `BodySystem`, `ForceVector`, `WaveField`, `CircuitFlow` | move a body, apply/set force, propagate a wave, flow current |
| Biology | `CellProcess`, `SequenceProcess`, `PopulationFlow` | advance a process, transcribe/translate, transfer population |
| Finance and business | `CashFlow`, `CompoundTimeline`, `Funnel`, `FeedbackLoop`, `ResourceFlow` | transfer cash/resources, compound, convert, reinforce |

Every component is frame-fitted at construction, accepts empty or long display
labels, exposes named semantic parts and anchors, and inherits the common focus
and opacity-restoration behavior. Action methods return Manim animations so an
author controls timing and composition with `self.play()`.

## Identity and State

- Use `remember()` once for an important object and `recall()` in later beats.
- Use `transform_object()` when its representation changes but its narrative
  identity does not.
- Drive multiple representations from `LinkedValue` or one verified source
  calculation; never duplicate a changing value in unrelated literals.
- Restore focus and highlights before starting another attention context. The
  helpers preserve the previous opacity and style exactly.
- Use `begin_beat()` for inspectable `next_section()` metadata inside a chapter.
