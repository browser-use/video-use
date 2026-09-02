"""Style-neutral semantic authoring primitives for Manim Community explainers.

The classes in this module manage meaning, continuity, and attention.  They do
not replace ordinary Manim mobjects or animations; authors can drop to raw
Manim whenever a custom visual argument needs it.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from manim import (
    AnimationGroup,
    DefaultSectionType,
    Group,
    Mobject,
    MovingCameraScene,
    Text,
    ThreeDScene,
    Transform,
    VGroup,
    ValueTracker,
    config,
)


# one explicit set of semantic color and font roles shared by every component
@dataclass(frozen=True, slots=True)
class VisualTheme:
    """One explicit set of semantic color and typography roles.

    Components require a theme instance so color meaning remains stable across
    a whole explanation.  The defaults are deliberately neutral rather than a
    recreation of any existing studio palette.
    """

    background: str = "#111318"
    text: str = "#F4F5F7"
    muted: str = "#9298A3"
    primary: str = "#5B8DEF"
    secondary: str = "#5FC49C"
    accent: str = "#F2C14E"
    warning: str = "#E86A6A"
    title_font: str = ""
    body_font: str = ""
    label_font: str = ""

    # resolve a semantic font role and reject unknown roles
    def font_for(self, role: str) -> str:
        """Resolve a semantic font role and reject accidental one-off roles."""
        fonts = {
            "title": self.title_font,
            "body": self.body_font,
            "label": self.label_font,
        }
        if role not in fonts:
            raise KeyError(f"unknown font role {role!r}; choose title, body, or label")
        return fonts[role]


# create themed text that survives empty strings and shrinks to a max width
def theme_text(
    value: object,
    theme: VisualTheme,
    *,
    role: str = "label",
    font_size: float = 24,
    color: str | None = None,
    max_width: float | None = None,
) -> Text:
    """Create resilient themed text, including a harmless empty-label object."""
    text = str(value)
    rendered = Text(
        text if text else " ",
        font=theme.font_for(role),
        font_size=font_size,
        color=color or theme.text,
    )
    # an empty label becomes an invisible space so layout code never breaks
    if not text:
        rendered.set_opacity(0.0)
    if max_width is not None and rendered.width > max_width:
        rendered.scale_to_fit_width(max_width)
    return rendered


# scale a component down only when it would exceed the frame minus the margin
def fit_to_frame(mobject: Mobject, *, margin: float = 0.45) -> Mobject:
    """Scale a component down only when it would exceed the active frame."""
    if margin < 0:
        raise ValueError("frame margin cannot be negative")
    maximum_width = float(config.frame_width) - 2 * margin
    maximum_height = float(config.frame_height) - 2 * margin
    if maximum_width <= 0 or maximum_height <= 0:
        raise ValueError("frame margin leaves no drawable area")
    scale = min(
        1.0,
        maximum_width / max(float(mobject.width), 1e-9),
        maximum_height / max(float(mobject.height), 1e-9),
    )
    if scale < 1.0:
        mobject.scale(scale)
    return mobject


# collect every family member that can report and restore a style
def _family_with_style(mobject: Mobject) -> list[Mobject]:
    return [
        member
        for member in mobject.get_family()
        if hasattr(member, "get_style") and hasattr(member, "set_style")
    ]


# capture a deep copy of each unique styled member so it can be restored later
def _style_snapshot(mobjects: Iterable[Mobject]) -> list[tuple[Mobject, dict[str, Any]]]:
    seen: set[int] = set()
    snapshot: list[tuple[Mobject, dict[str, Any]]] = []
    for mobject in mobjects:
        for member in _family_with_style(mobject):
            if id(member) in seen:
                continue
            seen.add(id(member))
            snapshot.append((member, deepcopy(member.get_style())))
    return snapshot


# put every captured style back onto its member
def _restore_snapshot(snapshot: Iterable[tuple[Mobject, dict[str, Any]]]) -> None:
    for member, style in snapshot:
        member.set_style(**deepcopy(style))


# find the submobject index path from root to target or none when target is not inside
def _path_to(root: Mobject, target: Mobject) -> tuple[int, ...] | None:
    if root is target:
        return ()
    for index, child in enumerate(root.submobjects):
        child_path = _path_to(child, target)
        if child_path is not None:
            return (index, *child_path)
    return None


# walk a submobject index path from root and return the mobject at the end
def _at_path(root: Mobject, path: Sequence[int]) -> Mobject:
    current = root
    for index in path:
        current = current.submobjects[index]
    return current


# a vgroup whose authored parts and anchors have stable semantic names
class SemanticMobject(VGroup):
    """A ``VGroup`` whose authored parts and anchors have stable semantic names."""

    # validate the theme and set up the part and anchor registries
    def __init__(self, theme: VisualTheme, *mobjects: Mobject, **kwargs: Any) -> None:
        if not isinstance(theme, VisualTheme):
            raise TypeError("SemanticMobject requires a shared VisualTheme")
        super().__init__(*mobjects, **kwargs)
        self.theme = theme
        self._semantic_parts: dict[str, Mobject] = {}
        self._semantic_anchors: dict[
            str, Mobject | Sequence[float] | Callable[[], Sequence[float]]
        ] = {}
        self._opacity_snapshot: list[tuple[Mobject, dict[str, Any]]] | None = None

    # store a named part and add it to the group unless asked not to
    def register_part(self, name: str, mobject: Mobject, *, add: bool = True) -> Mobject:
        if not name:
            raise ValueError("part name cannot be empty")
        if name in self._semantic_parts:
            raise ValueError(f"part {name!r} is already registered")
        if not isinstance(mobject, Mobject):
            raise TypeError("registered parts must be Manim mobjects")
        self._semantic_parts[name] = mobject
        if add and not any(child is mobject for child in self.submobjects):
            self.add(mobject)
        return mobject

    # look up a registered part and list the known names on a miss
    def part(self, name: str) -> Mobject:
        try:
            return self._semantic_parts[name]
        except KeyError as exc:
            known = ", ".join(sorted(self._semantic_parts)) or "none"
            raise KeyError(f"unknown part {name!r}; registered parts: {known}") from exc

    # names of every registered part in registration order
    @property
    def part_names(self) -> tuple[str, ...]:
        return tuple(self._semantic_parts)

    # store a named anchor that may be a mobject a fixed point or a callable
    def register_anchor(
        self,
        name: str,
        anchor: Mobject | Sequence[float] | Callable[[], Sequence[float]],
    ) -> None:
        if not name:
            raise ValueError("anchor name cannot be empty")
        if name in self._semantic_anchors:
            raise ValueError(f"anchor {name!r} is already registered")
        # plain coordinates must be two or three numbers
        if not isinstance(anchor, Mobject) and not callable(anchor):
            point = np.asarray(anchor, dtype=float)
            if point.shape not in {(2,), (3,)}:
                raise ValueError("anchor coordinates must contain two or three values")
        self._semantic_anchors[name] = anchor

    # resolve a named anchor to a three dimensional point copy
    def anchor(self, name: str) -> np.ndarray:
        try:
            anchor = self._semantic_anchors[name]
        except KeyError as exc:
            known = ", ".join(sorted(self._semantic_anchors)) or "none"
            raise KeyError(f"unknown anchor {name!r}; registered anchors: {known}") from exc
        # a mobject anchor follows its center while callables and raw points are evaluated now
        if isinstance(anchor, Mobject):
            point = np.asarray(anchor.get_center(), dtype=float)
        else:
            point = np.asarray(anchor() if callable(anchor) else anchor, dtype=float)
        if point.shape == (2,):
            point = np.append(point, 0.0)
        if point.shape != (3,):
            raise ValueError(f"anchor {name!r} did not resolve to a 2D or 3D point")
        return point.copy()

    # names of every registered anchor in registration order
    @property
    def anchor_names(self) -> tuple[str, ...]:
        return tuple(self._semantic_anchors)

    # dim every named part except the requested focus and remember the original styles
    def highlight(
        self,
        *part_names: str,
        dim_opacity: float = 0.18,
    ) -> "SemanticMobject":
        """Dim every named part except the requested semantic focus."""
        if not 0 <= dim_opacity <= 1:
            raise ValueError("dim opacity must be between zero and one")
        selected = {self.part(name) for name in part_names}
        if not selected:
            raise ValueError("highlight requires at least one registered part")
        if self._opacity_snapshot is None:
            self._opacity_snapshot = _style_snapshot(self._semantic_parts.values())
        for part in self._semantic_parts.values():
            if part not in selected:
                part.set_opacity(dim_opacity)
        return self

    # undo a highlight by restoring the saved styles
    def restore_opacity(self) -> "SemanticMobject":
        if self._opacity_snapshot is not None:
            _restore_snapshot(self._opacity_snapshot)
            self._opacity_snapshot = None
        return self

    # rebind parts and anchors by structural path after this object has become target
    def _adopt_semantics_from(self, target: "SemanticMobject") -> None:
        """Rebind semantic parts after this object has become ``target``."""
        # record where each part lives inside target then find the same path inside self
        part_paths = {
            name: _path_to(target, part)
            for name, part in target._semantic_parts.items()
        }
        self._semantic_parts = {
            name: _at_path(self, path)
            for name, path in part_paths.items()
            if path is not None
        }
        # mobject anchors are remapped by path and callables are frozen to their current point
        anchors: dict[str, Mobject | Sequence[float] | Callable[[], Sequence[float]]] = {}
        for name, anchor in target._semantic_anchors.items():
            if isinstance(anchor, Mobject):
                path = _path_to(target, anchor)
                anchors[name] = _at_path(self, path) if path is not None else anchor.get_center()
            elif callable(anchor):
                anchors[name] = np.asarray(anchor(), dtype=float)
            else:
                anchors[name] = np.asarray(anchor, dtype=float)
        self._semantic_anchors = anchors


# one value tracker driving any number of named representations
class LinkedValue:
    """One ``ValueTracker`` driving any number of named representations."""

    # create the tracker and an empty binding table
    def __init__(self, value: float = 0.0) -> None:
        self.tracker = ValueTracker(float(value))
        self._bindings: dict[
            str, tuple[Mobject, Callable[[Mobject, float], Mobject | None], Callable[[Mobject], None]]
        ] = {}
        self._suspended = False

    # the current tracked value as a float
    @property
    def value(self) -> float:
        return float(self.tracker.get_value())

    # bind a dependent mobject to an update callback and run it once immediately
    def register(
        self,
        name: str | Mobject,
        mobject: Mobject | Callable[[Mobject, float], Mobject | None],
        update: Callable[[Mobject, float], Mobject | None] | None = None,
    ) -> Mobject:
        """Register a dependent by name, or pass ``(mobject, update)`` for an auto-name."""
        # support both the named form and the shorthand form where the mobject comes first
        if isinstance(name, Mobject):
            dependent = name
            callback = mobject
            binding_name = f"dependent_{len(self._bindings)}"
        else:
            binding_name = name
            dependent = mobject
            callback = update
        if not binding_name:
            raise ValueError("linked value binding name cannot be empty")
        if binding_name in self._bindings:
            raise ValueError(f"linked value binding {binding_name!r} already exists")
        if not isinstance(dependent, Mobject) or not callable(callback):
            raise TypeError("register requires a mobject and an update callback")

        # manim updater that skips while suspended and swaps in a replacement when the callback returns one
        def updater(current: Mobject, _dt: float = 0.0) -> None:
            if self._suspended:
                return
            replacement = callback(current, self.value)
            if isinstance(replacement, Mobject) and replacement is not current:
                current.become(replacement)

        dependent.add_updater(updater)
        self._bindings[binding_name] = (dependent, callback, updater)
        updater(dependent)
        return dependent

    # return the mobject bound under a name
    def dependent(self, name: str) -> Mobject:
        try:
            return self._bindings[name][0]
        except KeyError as exc:
            raise KeyError(f"unknown linked value binding {name!r}") from exc

    # set the tracker and push the new value to every dependent
    def set_value(self, value: float) -> "LinkedValue":
        self.tracker.set_value(float(value))
        self.update()
        return self

    # rerun every updater unless updates are suspended
    def update(self) -> "LinkedValue":
        if not self._suspended:
            for dependent, _callback, updater in self._bindings.values():
                updater(dependent)
        return self

    # pause updates so the value can change without redrawing
    def suspend(self) -> "LinkedValue":
        self._suspended = True
        return self

    # turn updates back on and refresh every dependent
    def resume(self) -> "LinkedValue":
        self._suspended = False
        return self.update()

    # remove one binding or all of them and detach their updaters
    def clear(self, name: str | None = None) -> "LinkedValue":
        names = list(self._bindings) if name is None else [name]
        for binding_name in names:
            try:
                dependent, _callback, updater = self._bindings.pop(binding_name)
            except KeyError as exc:
                raise KeyError(f"unknown linked value binding {binding_name!r}") from exc
            dependent.remove_updater(updater)
        return self

    # build a manim animation that tweens the tracker to a new value
    def animate_to(self, value: float, *, run_time: float | None = None) -> Any:
        builder = self.tracker.animate
        if run_time is not None:
            if run_time < 0:
                raise ValueError("animation duration cannot be negative")
            builder = builder(run_time=run_time)
        return builder.set_value(float(value))


# shared registry focus highlight and hold helpers layered onto a manim scene
class _TeachingSceneMixin:
    max_static_hold_s = 3.0

    # lazily create the registry and snapshot attributes so construction order does not matter
    def _ensure_teaching_state(self) -> None:
        if not hasattr(self, "_remembered_objects"):
            self._remembered_objects: dict[str, Mobject] = {}
            self._context_snapshot: list[tuple[Mobject, dict[str, Any]]] | None = None
            self._context_roots: list[Mobject] = []
            self._highlight_snapshot: list[tuple[Mobject, dict[str, Any]]] | None = None
            self._highlight_roots: list[Mobject] = []
            self._camera_context: Any = None

    # hook manim setup so the teaching state exists before construct runs
    def setup(self) -> None:
        super().setup()
        self._ensure_teaching_state()

    # store a named mobject and add it to the scene unless asked not to
    def remember(self, name: str, mobject: Mobject, *, add: bool = True) -> Mobject:
        self._ensure_teaching_state()
        if not name:
            raise ValueError("remembered object name cannot be empty")
        if name in self._remembered_objects:
            raise ValueError(f"object {name!r} is already remembered")
        self._remembered_objects[name] = mobject
        if add and not any(existing is mobject for existing in self.mobjects):
            self.add(mobject)
        return mobject

    # look up a remembered mobject and list the known names on a miss
    def recall(self, name: str) -> Mobject:
        self._ensure_teaching_state()
        try:
            return self._remembered_objects[name]
        except KeyError as exc:
            known = ", ".join(sorted(self._remembered_objects)) or "none"
            raise KeyError(f"unknown remembered object {name!r}; known objects: {known}") from exc

    # start a named manim section so each narrative beat is a separate clip
    def begin_beat(
        self,
        name: str,
        *,
        section_type: DefaultSectionType = DefaultSectionType.NORMAL,
        skip_animations: bool = False,
    ) -> None:
        if not name:
            raise ValueError("beat name cannot be empty")
        # Positional arguments support both Manim CE 0.20's ``type`` name and
        # 0.21's renamed ``section_type`` parameter.
        self.next_section(name, section_type, skip_animations)

    # turn names or mobjects or an iterable of them into a list of mobjects
    def _resolve_targets(
        self, target: str | Mobject | Iterable[str | Mobject]
    ) -> list[Mobject]:
        if isinstance(target, (str, Mobject)):
            values: Iterable[str | Mobject] = [target]
        else:
            values = target
        resolved = [self.recall(value) if isinstance(value, str) else value for value in values]
        if not resolved or not all(isinstance(value, Mobject) for value in resolved):
            raise ValueError("focus target must contain at least one mobject")
        return resolved

    # keep only the roots that share no family member with any target
    @staticmethod
    def _unrelated_roots(roots: Iterable[Mobject], targets: Iterable[Mobject]) -> list[Mobject]:
        target_ids = {
            id(member)
            for target in targets
            for member in target.get_family()
        }
        return [
            root
            for root in roots
            if not any(id(member) in target_ids for member in root.get_family())
        ]

    # play the dim animations and let subclasses move the camera too
    def _play_focus(
        self,
        targets: list[Mobject],
        dim_animations: list[Any],
        *,
        margin: float,
        run_time: float,
    ) -> None:
        self.play(*dim_animations, run_time=run_time)

    # no camera to restore in the base mixin
    def _restore_focus_camera(self, *, run_time: float, animate: bool) -> None:
        return None

    # dim everything unrelated to the target and optionally frame it with the camera
    def focus_on(
        self,
        target: str | Mobject | Iterable[str | Mobject],
        *,
        dim_opacity: float = 0.18,
        margin: float = 0.8,
        run_time: float = 0.6,
        animate: bool = True,
    ) -> None:
        self._ensure_teaching_state()
        if self._context_snapshot is not None:
            raise RuntimeError("restore the current focus context before starting another")
        if not 0 <= dim_opacity <= 1:
            raise ValueError("dim opacity must be between zero and one")
        targets = self._resolve_targets(target)
        # snapshot the unrelated roots so restore_context can put back exact styles
        unrelated = self._unrelated_roots(self.mobjects, targets)
        self._context_roots = unrelated
        self._context_snapshot = _style_snapshot(unrelated)
        if animate:
            animations = [root.animate.set_opacity(dim_opacity) for root in unrelated]
            self._play_focus(targets, animations, margin=margin, run_time=run_time)
        else:
            for root in unrelated:
                root.set_opacity(dim_opacity)
            self._set_focus_camera_immediately(targets, margin=margin)

    # no camera to move in the base mixin
    def _set_focus_camera_immediately(self, targets: list[Mobject], *, margin: float) -> None:
        return None

    # restore the styles saved by focus_on and return the camera to where it was
    def restore_context(self, *, run_time: float = 0.6, animate: bool = True) -> None:
        self._ensure_teaching_state()
        if self._context_snapshot is None:
            return
        snapshot = self._context_snapshot
        # animate every member back to its saved style in one group then hard set it to be exact
        if animate:
            animations = [
                member.animate.set_style(**deepcopy(style))
                for member, style in snapshot
            ]
            if animations:
                self.play(AnimationGroup(*animations, lag_ratio=0), run_time=run_time)
        _restore_snapshot(snapshot)
        self._restore_focus_camera(run_time=run_time, animate=animate)
        self._context_snapshot = None
        self._context_roots = []

    # recolor the target with the accent color and remember its styles for later
    def highlight(
        self,
        target: str | Mobject | Iterable[str | Mobject],
        *,
        color: str | None = None,
        run_time: float = 0.35,
        animate: bool = True,
    ) -> None:
        self._ensure_teaching_state()
        if self._highlight_snapshot is not None:
            raise RuntimeError("restore the current highlight before starting another")
        targets = self._resolve_targets(target)
        self._highlight_roots = targets
        self._highlight_snapshot = _style_snapshot(targets)
        highlight_color = color or getattr(getattr(self, "theme", None), "accent", "#F2C14E")
        if animate:
            self.play(*(target.animate.set_color(highlight_color) for target in targets), run_time=run_time)
        else:
            for focused in targets:
                focused.set_color(highlight_color)

    # restore the styles saved by highlight
    def restore_highlight(self, *, run_time: float = 0.35, animate: bool = True) -> None:
        self._ensure_teaching_state()
        if self._highlight_snapshot is None:
            return
        snapshot = self._highlight_snapshot
        if animate:
            animations = [
                member.animate.set_style(**deepcopy(style))
                for member, style in snapshot
            ]
            if animations:
                self.play(AnimationGroup(*animations, lag_ratio=0), run_time=run_time)
        _restore_snapshot(snapshot)
        self._highlight_snapshot = None
        self._highlight_roots = []

    # morph a remembered object into target while keeping its registry identity
    def transform_object(
        self,
        name: str,
        target: Mobject,
        *,
        run_time: float = 0.8,
        animate: bool = True,
        **animation_kwargs: Any,
    ) -> Mobject:
        """Morph a remembered object while preserving its registry identity."""
        current = self.recall(name)
        if animate:
            self.play(
                Transform(current, target, **animation_kwargs),
                run_time=run_time,
            )
        else:
            current.become(target)
        # carry the semantic parts and anchors over when both sides are semantic
        if isinstance(current, SemanticMobject) and isinstance(target, SemanticMobject):
            current._adopt_semantics_from(target)
        self._remembered_objects[name] = current
        return current

    # wait for a duration and demand a purpose for long static holds
    def hold(self, duration_s: float, *, purpose: str | None = None) -> None:
        if duration_s < 0:
            raise ValueError("hold duration cannot be negative")
        if duration_s > self.max_static_hold_s and not purpose:
            raise ValueError(
                f"static hold of {duration_s:.2f}s needs an explicit educational purpose"
            )
        # skip waits shorter than one frame
        if duration_s >= 1 / float(config.frame_rate):
            self.wait(duration_s)


# a moving camera scene with semantic continuity and attention helpers
class TeachingScene(_TeachingSceneMixin, MovingCameraScene):
    """A moving-camera scene with semantic continuity and attention helpers."""

    # dim unrelated roots while zooming the camera frame onto the targets
    def _play_focus(
        self,
        targets: list[Mobject],
        dim_animations: list[Any],
        *,
        margin: float,
        run_time: float,
    ) -> None:
        group = Group(*targets)
        frame = self.camera.frame
        self._camera_context = frame.copy()
        # pick the width that fits both dimensions with margin while keeping the frame aspect ratio
        target_width = max(group.width + margin * 2, (group.height + margin * 2) * frame.width / frame.height)
        camera_animation = frame.animate.move_to(group.get_center()).set(width=target_width)
        self.play(*dim_animations, camera_animation, run_time=run_time)

    # move the camera frame onto the targets without animating
    def _set_focus_camera_immediately(self, targets: list[Mobject], *, margin: float) -> None:
        group = Group(*targets)
        frame = self.camera.frame
        self._camera_context = frame.copy()
        # pick the width that fits both dimensions with margin while keeping the frame aspect ratio
        target_width = max(group.width + margin * 2, (group.height + margin * 2) * frame.width / frame.height)
        frame.move_to(group.get_center()).set(width=target_width)

    # bring the camera frame back to its saved copy
    def _restore_focus_camera(self, *, run_time: float, animate: bool) -> None:
        if self._camera_context is None:
            return
        if animate:
            self.play(Transform(self.camera.frame, self._camera_context), run_time=run_time)
        else:
            self.camera.frame.become(self._camera_context)
        self._camera_context = None


# the same semantic authoring contract on top of the manim three d scene
class TeachingThreeDScene(_TeachingSceneMixin, ThreeDScene):
    """The same semantic authoring contract on top of Manim's ``ThreeDScene``."""

    # capture every camera setting that focus may change
    def _camera_values(self) -> dict[str, Any]:
        return {
            "phi": float(self.camera.phi),
            "theta": float(self.camera.theta),
            "gamma": float(self.camera.gamma),
            "zoom": float(self.camera.zoom),
            "focal_distance": float(self.camera.focal_distance),
            "frame_center": np.asarray(self.camera.frame_center, dtype=float).copy(),
        }

    # dim unrelated roots while moving the three d camera onto the targets
    def _play_focus(
        self,
        targets: list[Mobject],
        dim_animations: list[Any],
        *,
        margin: float,
        run_time: float,
    ) -> None:
        group = Group(*targets)
        self._camera_context = self._camera_values()
        # zoom by the tighter of the width and height ratios and cap it so the camera never zooms absurdly far
        scale = min(
            float(config.frame_width) / max(group.width + 2 * margin, 1e-6),
            float(config.frame_height) / max(group.height + 2 * margin, 1e-6),
        )
        self.move_camera(
            frame_center=group.get_center(),
            zoom=min(float(self.camera.zoom) * scale, 8.0),
            added_anims=dim_animations,
            run_time=run_time,
        )

    # move the three d camera onto the targets without animating
    def _set_focus_camera_immediately(self, targets: list[Mobject], *, margin: float) -> None:
        group = Group(*targets)
        self._camera_context = self._camera_values()
        # zoom by the tighter of the width and height ratios and cap it so the camera never zooms absurdly far
        scale = min(
            float(config.frame_width) / max(group.width + 2 * margin, 1e-6),
            float(config.frame_height) / max(group.height + 2 * margin, 1e-6),
        )
        self.camera.frame_center = group.get_center()
        self.camera.zoom = min(float(self.camera.zoom) * scale, 8.0)

    # restore the saved three d camera settings by animation or direct assignment
    def _restore_focus_camera(self, *, run_time: float, animate: bool) -> None:
        if self._camera_context is None:
            return
        if animate:
            self.move_camera(**self._camera_context, run_time=run_time)
        else:
            for name, value in self._camera_context.items():
                setattr(self.camera, name, value)
        self._camera_context = None


__all__ = [
    "LinkedValue",
    "SemanticMobject",
    "TeachingScene",
    "TeachingThreeDScene",
    "VisualTheme",
    "fit_to_frame",
    "theme_text",
]
