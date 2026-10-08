# Semantic Chapter Planning Reference

Use this reference with the required `edit/visual_plan.md` contract in
`concept-explainer.md`. The artifact guides custom authoring; it is not a rigid
scene compiler.

## Narrative Arc Structures

### Discovery Arc

1. Hook — pose the central question or expose the misconception.
2. Intuition — construct a visual model the audience can manipulate mentally.
3. Formalize — connect the model to notation, an algorithm, or a mechanism.
4. Reveal — coordinate the representations into the final aha moment.
5. Extend — show one implication without diluting the thesis.

### Problem-Solution Arc

1. Problem — make the failure visible in a persistent system.
2. Failed attempt — transform the same system and show why it still fails.
3. Key insight — introduce the missing relationship.
4. Solution — update the system causally.
5. Result — compare the original and final states.

### Comparison Arc

1. Setup — establish shared inputs and measures.
2. Approach A — transform the first copy.
3. Approach B — transform the second copy.
4. Contrast — link differences to the same underlying values.
5. Verdict — land on a frame that makes the decision legible.

### Build-Up Arc

1. Establish the first component.
2. Add the next component without moving the first arbitrarily.
3. Reveal the relationship through transfer or flow.
4. Change scale or conditions while linked representations update.
5. Move the camera only when the full system or a local mechanism is the payoff.

## Chapter Boundaries

Create one independently renderable class per narrative chapter. Within it, use
`begin_beat()` or `next_section()` for the teaching beats. A boundary is not an
instruction to clear the frame:

- **Carry forward** an important object in its current state.
- **Transform bridge** one object into the next representation while preserving
  its semantic identity.
- **Context shift** keep the system visible but dim or reframe it.
- **Deliberate departure** remove an object only when its absence communicates
  completion, replacement, loss, or a change of scope.

Hard cuts and fades are valid editorial choices when intentional. They are not
default cleanup operations.

## Cross-Chapter Consistency

- One shared `VisualTheme` defines semantic color and font roles.
- The same concept keeps its color role and approximate position.
- Repeated quantities use one `LinkedValue` or one source calculation.
- Motion vocabulary remains coherent; vary it only when the meaning changes.
- A chapter payoff should become the next chapter's context when the narrative
  depends on it.

## Chapter Checklist

- [ ] The chapter advances one stage of the teaching thesis.
- [ ] Internal beats are named with section markers.
- [ ] Persistent objects have semantic names.
- [ ] Each beat specifies initial state, visible action, and resulting state.
- [ ] Coupled representations update together.
- [ ] Camera movement or dimming has an explicit attention purpose.
- [ ] The final frame proves the planned claim.
- [ ] Carried objects are named for the next beat or chapter.
- [ ] All text and teaching content remain frame-safe.

## Duration Heuristics

| Content | Typical duration |
|---|---:|
| Establish a visual model | 5–10s |
| Construct a relationship | 8–15s |
| Transform or compare states | 6–12s |
| Dense payoff study | 3–6s |
| Extend the result | 5–10s |

These are planning ranges, not pacing presets. Narration alignment and viewer
comprehension decide the real timing.

## Planning Template

```markdown
# [Video title]

## Teaching contract
- Audience and assumed knowledge:
- Central question or misconception:
- Teaching thesis:
- Final aha moment:
- Target duration and delivery:
- Visual direction:

## Semantic theme
- Background:
- Text:
- Muted/context:
- Primary meaning:
- Secondary meaning:
- Accent meaning:
- Warning meaning:
- Title/body/label font roles:

## Chapter map
| Chapter class | Purpose | Narration span | Entry state | Payoff state |
|---|---|---|---|---|

## Beat plan
| Narration span | Named objects | Initial state | Object state change | Relationships updated together | Camera/attention | Result and proof | Carried forward |
|---|---|---|---|---|---|---|---|

## Payoff frame
- Remaining objects and states:
- Relationship the viewer should see:
- Hold purpose and duration:

## Optional footage
| Beat | Source/provenance | Crop/timing | Why footage is better than illustration |
|---|---|---|---|
```
