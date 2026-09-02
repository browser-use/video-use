# Original Concept Explainer Contract

Use this contract when creating an original technical explainer from a topic and
narration. It does not apply when the task is only clipping or treating supplied
footage. The objective is a correct, continuous visual argument rather than a
narrated sequence of cards.

A topic-only request implies an audible narrated explainer unless the user
explicitly requests silence. Do not create a silent placeholder track: generate
speech, use its measured duration for timing, and verify the assembled audio has
finite loudness and audible samples before normalization.

## Scope and legibility contract

These limits come from measured runs and are not taste. Treat them as hard.

1. **One mechanism per explainer under a minute.** A 45 second piece teaches one idea
   completely. If the narration needs a second mechanism to make sense, cut the first
   one down or ask for a longer runtime. At most three chapters for 45 seconds.
2. **Narration pace 150 to 185 words per minute.** Land the runtime by editing words or by
   setting the voice speed before locking the take. Never time stretch a recorded
   narration to fit; a take more than five percent long is a script problem.
3. **Original explainers are Manim with the teaching assets.** PIL is for overlay cards
   only. A whole explainer rendered as raster frames is a failed handoff even when the
   layout manifest passes.
4. **Chapters share geometry.** Build persistent objects through one base scene or shared
   builders so every carried object has identical position and size at a chapter boundary.
   Compare the exit frame of each chapter with the entry frame of the next before rendering.
5. **Legibility floor at 1920x1080.** Labels use font_size 24 or larger, titles 36 or larger,
   raster text at least 28 px cap height. At most four foreground groups on screen at any
   critical frame. The bottom 16 percent of the frame is the caption rail and stays empty.
6. **Declare color roles once.** The plan names the role of each color (key, hash path,
   success, warning) and those roles never change during the video.
7. **Plan depth.** The beat table has about one row per four seconds of runtime, each row
   naming its objects, the state change, and what the beat proves.
8. **Hold the payoff.** Narration ends at least two seconds before the video does and the
   final frame holds still.

## Efficient Production Order

1. Research the topic and verify every factual claim and worked value.
2. Write narration around one misconception or central question, a progression,
   and a concrete final visual payoff.
3. Estimate narration duration locally before making a paid TTS request:
   count the words and divide by the voice's words-per-minute rate, then keep a
   three-second safety margin below the maximum. After synthesis, read the real
   duration from the alignment JSON and re-plan if it misses the window.

   Use the fixed voice's measured rate when known. Otherwise begin near 145 WPM
   and treat the result as an estimate. Reserve three seconds below a 55–65
   second ceiling because punctuation and phrasing affect real speech duration.
4. Generate audible narration once, save its alignment sidecar when available,
   measure the actual duration, and lock the narration before final animation
   timing. Reject or repair a silent source before rendering the EDL.
5. Complete the required `edit/visual_plan.md` below.
6. Choose Manim, Penrose, CeTZ, sourced footage, or another engine beat by beat.
   Illustration is the default for original explainers. Footage is optional and
   needs an editorial reason plus the existing provenance and rights checks.
7. Copy `assets/teaching.py`, `assets/concept_explainer.py`, and any needed
   `assets/domains/` modules into the animation workspace. Build one independently
   renderable Manim class per narrative chapter.
8. Mark internal teaching beats with `TeachingScene.begin_beat()` or
   `next_section()`. Preview and repair only the affected chapter classes.
9. Render the complete visual base, add approved footage through the existing
   overlay contract, and apply narration and captions through the existing EDL
   renderer.
10. Review the complete video at 1x and persist run evidence. Isolated previews
    speed up authoring but are not the quality gate.

## Required `edit/visual_plan.md`

This artifact is advisory rather than a scene compiler: write it, follow it, and
still use custom Manim whenever an abstraction would constrain the teaching.
The plan must contain:

### Teaching contract

- **Audience and assumed knowledge**
- **Central question or misconception**
- **Teaching thesis**
- **Final aha moment**: the exact relationship the payoff frame makes visible
- **Approved visual direction**: semantic theme and typography roles

### Chapter map

Identify one independently renderable class per narrative chapter. Each chapter
contains multiple teaching beats and should build, transform, compare, or move a
persistent set of objects instead of resetting into a new card.

### Beat table

Add one row per meaningful visual beat:

| Narration span | Named objects | Initial state | Object state change | Relationships updated together | Camera or attention action | Result / what this proves | Carried into next beat |
|---|---|---|---|---|---|---|---|
| 8.2–10.5 “tasks run together” | `queue`, `workers`, `elapsed_time` | tasks wait in one queue | tasks fan into workers | worker status and elapsed-time counter advance from one value | focus on active workers; queue dims | concurrency shortens elapsed time | workers and counter |

If the action and resulting state are the same, the beat is probably a static
slide. Redesign it as a construction, comparison, transformation, transfer, or
flow. Every spoken claim needs visible evidence, but not every sentence needs a
new object.

### Payoff frame

Name every object that remains, its final state and position, the relationship
the viewer should notice, and how long the frame is held through the final
spoken words.

### Optional footage

For each footage beat, record the source, intended crop and timing, rights or
license evidence, and why footage communicates that claim better than an
illustration. Omit footage when no beat benefits from it.

## Semantic Authoring Rules

- Important objects persist across multiple beats. Preserve their semantic color
  role and position unless a deliberate transformation teaches something.
- Related representations share one underlying value. A changed probability,
  balance, vector, or request state must update every visible representation.
- Motion reveals causality or structure; it does not exist to vary animation
  types mechanically.
- Camera movement and opacity have an attention purpose recorded in the plan.
- Do not reveal a completed diagram first and spend the chapter pointing at its
  labels. Build the relevant relationship as the narration earns it.
- A 10–15 second chapter normally contains 3–5 meaningful state changes. Static
  holds longer than three seconds need a reading, comparison, prediction, or
  payoff purpose.
- The main teaching object must remain readable at phone size. Keep titles,
  labels, equations, and payoff copy inside the safe frame.
- Compute derived numbers in code. Matrix products, percentages, counters, chart
  values, and equation results must come from a calculation or assertion.
- Use timestamp alignment to land important reveals on their payoff words. Let
  the visual lead the word slightly rather than appear after it.
- Hold the last meaningful frame through the final spoken words. Fade only when
  departure itself is meaningful; do not create unexplained blank padding.

## Reusable Assets

`assets/teaching.py` provides:

- `VisualTheme` for explicit semantic color and typography roles
- `SemanticMobject` for stable named parts and anchors
- `LinkedValue` for synchronized representations driven by one `ValueTracker`
- `TeachingScene` and `TeachingThreeDScene` for persistent object registries,
  beat markers, camera/opacity focus, highlights, transformations, and holds

`assets/domains/` provides style-neutral math, algorithms/AI, systems, physics,
biology, finance, and business components. They expose concept-level actions and
remain ordinary Manim objects. Raw Manim is always available.

`assets/concept_explainer.py` provides caption-safe content frames, fitted text
and panels, composition-region guides, final-state overlap checks, and
EDL-ready normalized bounds. Use `assert_inside_frame()` and
`assert_no_overlap()` on every critical state. When footage shares the frame,
use `composition_regions()` and export the teaching object's
`normalized_bounds()` to the EDL `protected_regions` contract.

Penrose and CeTZ remain available for precise static structures. Import those
assets into Manim when temporal reveal or semantic transformation is needed.

## Targeted Preview

```bash
python skills/manim-video/scripts/preview_scene.py \
  /path/to/project/edit/animations/script.py ChapterOne ChapterThree
```

The command uses Manim Community low quality and its cache, writes only beneath
`edit/verify/manim_previews/`, and produces each selected chapter's video,
initial/final frames, five-frame contact sheet, duration, dimensions, frame rate,
render status, and available section metadata. Selection is scene-class level;
`next_section()` metadata helps inspect internal beats but does not imply
unsupported arbitrary section-only rendering.

## Review Before Production

- Read the narration while watching the complete low-quality assembly at 1x.
- Confirm every beat-table action is visibly demonstrated.
- Confirm named objects persist for the spans promised by the plan.
- Confirm linked representations agree at initial, intermediate, and final state.
- Search for static holds longer than three seconds and justify or replace them.
- Confirm every derived value is calculated or asserted.
- Confirm every narrative chapter renders independently and section markers match
  its internal beats.
- Preview text-heavy frames at medium quality and pass safe-frame validation.
- Inspect the complete video for factual errors, clipped teaching content,
  caption collisions, broken rendering, missing audio, and delivery properties.

## Shared-Frame Footage Rules

Reserve the bottom caption rail defined by the existing explainer asset. Choose
a relationship rather than merely adding a rectangle: `cutaway`, `split_left`,
`split_right`, or `picture_in_picture`. A split frame pairs footage with an
illustration only when the two views teach the same narration sentence. Place
picture-in-picture footage in genuine negative space and declare the teaching
object's occupied bounds so overlay preflight can reject collisions at footage
entry, middle, and exit frames.

Inspect only a few candidate moments from each source. If a source cannot be
cleared or does not teach the beat better than illustration, omit it rather than
lowering relevance. Keep provenance in a restrained source footer instead of
adding decorative title hierarchy.
