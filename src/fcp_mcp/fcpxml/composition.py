"""Composition helpers — transforms, video filters, connected clips, keyframes.

These helpers mutate an already-parsed FCPXML tree in place. They are used by
the ``fcpxml_set_clip_transform``, ``fcpxml_add_video_effect``,
``fcpxml_add_connected_title``, ``fcpxml_add_connected_generator`` and
``fcpxml_add_transform_keyframes`` tools.

Every insertion respects the child order of Apple's FCPXML DTDs (checked
against the 1.11, 1.13, 1.14 and 1.15 DTDs bundled with Final Cut Pro): for
example ``adjust-transform`` sits inside the intrinsic video parameters,
anchored (connected) items follow the intrinsic parameters and precede
markers, and ``filter-video`` follows markers but precedes ``filter-audio``
and ``metadata``.

Units:

* ``position`` is an ``"x y"`` offset from the frame centre expressed as a
  *percentage of the project frame height*; positive ``x`` moves right and
  positive ``y`` moves up (Apple FCPXML reference, "Animation": ``"-5 10"``
  is "left 5 percent and up 10 percent of the project's height"; the FCPXML
  1.1 DTD documents position the same way). In a 1080x1920 project
  ``"0 -25"`` moves the clip centre down by 480 px.
* ``anchor`` is assumed to use the same coordinate space as ``position``.
* ``scale`` is an ``"x y"`` multiplier (``"1 1"`` is unchanged).
* ``rotation`` is in degrees (counter-clockwise positive, as in the Final
  Cut Pro inspector; not verified against an export).
* Keyframe ``time`` values are treated as clip-local, starting at the
  clip's ``start`` attribute. This follows the DTD comment that ``start``
  "defines a local timeline to schedule contained and anchored items"; it
  has not been verified against a Final Cut export of a head-trimmed clip.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from xml.etree import ElementTree

from ..contracts import ErrorCode, FCPMCPError
from .time_utils import RationalTime

# Story elements whose DTD content model accepts adjust-transform and filter-video.
VISUAL_CLIP_TAGS = frozenset(
    {"asset-clip", "clip", "ref-clip", "sync-clip", "title", "video"}
)
# Named story elements a caller may address; the extra tags are reported as
# unsupported rather than "not found" so the caller learns why.
NAMED_CLIP_TAGS = VISUAL_CLIP_TAGS | {"mc-clip", "gap", "audio", "audition"}
# Primary-storyline elements that may carry anchored (connected) items.
ANCHOR_PARENT_TAGS = frozenset(
    {"asset-clip", "clip", "ref-clip", "sync-clip", "mc-clip", "title", "video", "gap"}
)
ANCHORED_TAGS = frozenset(
    {
        "audio", "video", "clip", "title", "caption", "mc-clip", "ref-clip",
        "sync-clip", "asset-clip", "audition", "spine", "live-drawing", "gap",
    }
)

TRANSFORM_PARAMS = ("position", "scale", "rotation", "anchor")
KEYFRAME_INTERPOLATIONS = ("linear", "ease", "easeIn", "easeOut")
KEYFRAME_CURVES = ("linear", "smooth")
TEXT_ALIGNMENTS = ("left", "center", "right", "justified")

_INTRINSIC_VIDEO = (
    "object-tracker", "adjust-crop", "adjust-corners", "adjust-conform",
    "adjust-transform", "adjust-blend", "adjust-stabilization",
    "adjust-rollingShutter", "adjust-360-transform", "adjust-reorient",
    "adjust-orientation", "adjust-cinematic", "adjust-colorConform",
    "adjust-stereo-3D",  # FCPXML 1.13+
)
_INTRINSIC_AUDIO = ("adjust-volume", "adjust-panner")
_TIMING = ("conform-rate", "timeMap")
_MARKERS = frozenset(
    {
        "marker", "chapter-marker", "rating", "keyword", "analysis-marker",
        "hidden-clip-marker",  # FCPXML 1.13+
    }
)
_ANCHORS = "@anchors"
_MARKER_GROUP = "@markers"
_VIDEO_FILTERS = "@video-filters"

# DTD child order per element, as a sequence of slots. A slot is either a tag
# name or one of the group sentinels above.
_CHILD_ORDER: dict[str, tuple[str, ...]] = {
    "asset-clip": (
        "note", *_TIMING, *_INTRINSIC_VIDEO, *_INTRINSIC_AUDIO, _ANCHORS,
        _MARKER_GROUP, "audio-channel-source", _VIDEO_FILTERS, "filter-audio",
        "metadata",
    ),
    "clip": (
        "note", *_TIMING, *_INTRINSIC_VIDEO, *_INTRINSIC_AUDIO, _ANCHORS,
        _MARKER_GROUP, "audio-channel-source", _VIDEO_FILTERS, "filter-audio",
        "metadata",
    ),
    "ref-clip": (
        "note", *_TIMING, *_INTRINSIC_VIDEO, *_INTRINSIC_AUDIO, _ANCHORS,
        _MARKER_GROUP, "audio-role-source", _VIDEO_FILTERS, "filter-audio",
        "metadata",
    ),
    "sync-clip": (
        "note", *_TIMING, *_INTRINSIC_VIDEO, *_INTRINSIC_AUDIO, _ANCHORS,
        _MARKER_GROUP, "sync-source", _VIDEO_FILTERS, "filter-audio",
        "metadata",
    ),
    "mc-clip": (
        "note", *_TIMING, *_INTRINSIC_AUDIO, "mc-source", _ANCHORS,
        _MARKER_GROUP, "filter-audio", "metadata",
    ),
    "title": (
        "param", "text", "text-style-def", "note", *_INTRINSIC_VIDEO,
        _ANCHORS, _MARKER_GROUP, _VIDEO_FILTERS, "metadata",
    ),
    "video": (
        "param", "note", *_TIMING, *_INTRINSIC_VIDEO,
        "adjust-natural-motion-blur",  # FCPXML 1.15+
        _ANCHORS, _MARKER_GROUP, _VIDEO_FILTERS, "reserved",
    ),
    "gap": ("note", _ANCHORS, _MARKER_GROUP, "metadata"),
}


def _slot(tag: str) -> str:
    if tag in ANCHORED_TAGS:
        return _ANCHORS
    if tag in _MARKERS:
        return _MARKER_GROUP
    if tag in {"filter-video", "filter-video-mask"}:
        return _VIDEO_FILTERS
    return tag


def parent_of(root: ElementTree.Element, element: ElementTree.Element) -> ElementTree.Element | None:
    """Return the parent of ``element`` inside ``root``, or ``None`` when it is the root or absent."""
    for candidate in root.iter():
        for child in candidate:
            if child is element:
                return candidate
    return None


def insert_in_dtd_order(parent: ElementTree.Element, child: ElementTree.Element) -> None:
    """Insert ``child`` after every sibling the DTD orders at or before it."""
    order = _CHILD_ORDER.get(parent.tag)
    if order is None:
        raise FCPMCPError(
            ErrorCode.UNSUPPORTED_CONTRACT,
            f"<{parent.tag}> cannot contain <{child.tag}>",
        )
    rank = {slot: index for index, slot in enumerate(order)}
    child_slot = _slot(child.tag)
    if child_slot not in rank:
        raise FCPMCPError(
            ErrorCode.UNSUPPORTED_CONTRACT,
            f"<{parent.tag}> cannot contain <{child.tag}>",
        )
    child_rank = rank[child_slot]
    insert_at = 0
    for index, sibling in enumerate(parent):
        # Siblings outside the content model keep their place and do not
        # influence where the new child lands.
        sibling_rank = rank.get(_slot(sibling.tag))
        if sibling_rank is not None and sibling_rank <= child_rank:
            insert_at = index + 1
    parent.insert(insert_at, child)


# --- Lookup -----------------------------------------------------------------


def find_named_clip(
    root: ElementTree.Element,
    name: str,
    *,
    occurrence: int = 1,
    supported: Iterable[str] = VISUAL_CLIP_TAGS,
) -> ElementTree.Element:
    """Return the ``occurrence``-th (1-based, document order) story element named ``name``."""
    if occurrence < 1:
        raise FCPMCPError(
            ErrorCode.INVALID_ARGUMENTS,
            f"occurrence must be >= 1: {occurrence}",
        )
    matches = [
        element
        for element in root.iter()
        if element.tag in NAMED_CLIP_TAGS and element.get("name") == name
    ]
    if len(matches) < occurrence:
        found = f" ({len(matches)} found)" if matches else ""
        raise FCPMCPError(
            ErrorCode.TARGET_NOT_FOUND,
            f"Clip '{name}' occurrence {occurrence} not found{found}",
        )
    element = matches[occurrence - 1]
    supported_tags = frozenset(supported)
    if element.tag not in supported_tags:
        raise FCPMCPError(
            ErrorCode.UNSUPPORTED_CONTRACT,
            f"Clip '{name}' is a <{element.tag}>; this operation supports "
            + ", ".join(f"<{tag}>" for tag in sorted(supported_tags)),
        )
    return element


def primary_spine(root: ElementTree.Element) -> ElementTree.Element:
    """Return the primary storyline of the first project in the document."""
    spine = root.find(".//project/sequence/spine")
    if spine is None:
        spine = root.find(".//sequence/spine")
    if spine is None:
        raise FCPMCPError(
            ErrorCode.TARGET_NOT_FOUND,
            "Timeline spine not found",
        )
    return spine


def _time_attr(element: ElementTree.Element, attribute: str) -> RationalTime:
    return RationalTime.from_fcpxml(element.get(attribute) or "0s")


@dataclass(frozen=True)
class ConnectionPoint:
    parent: ElementTree.Element
    parent_offset: RationalTime
    parent_start: RationalTime
    local_offset: RationalTime


def resolve_connection_point(
    root: ElementTree.Element,
    timeline_offset: RationalTime,
    *,
    anchor_clip_name: str = "",
    anchor_occurrence: int = 1,
) -> ConnectionPoint:
    """Find the primary-storyline parent covering ``timeline_offset``.

    ``timeline_offset`` uses the same time space as the ``offset`` attributes
    of primary-storyline items (sequence time, which includes ``tcStart``).
    A connected child's ``offset`` is expressed in its parent's *local* time:
    ``child.offset = parent.start + (timeline_offset - parent.offset)``.
    """
    spine = primary_spine(root)
    storyline = [
        child
        for child in spine
        if child.tag != "transition" and child.get("lane", "0") == "0"
    ]
    if anchor_clip_name:
        parent = find_named_clip(
            root,
            anchor_clip_name,
            occurrence=anchor_occurrence,
            supported=ANCHOR_PARENT_TAGS,
        )
        if not any(parent is item for item in storyline):
            raise FCPMCPError(
                ErrorCode.UNSUPPORTED_CONTRACT,
                f"Anchor clip '{anchor_clip_name}' is not on the primary storyline",
            )
    else:
        parent = None
        for item in storyline:
            offset = _time_attr(item, "offset")
            duration = _time_attr(item, "duration")
            if offset <= timeline_offset < offset + duration:
                parent = item
                break
        if parent is None:
            raise FCPMCPError(
                ErrorCode.TARGET_NOT_FOUND,
                "No primary-storyline item covers timeline offset "
                f"{timeline_offset.to_fcpxml()}",
            )
        if parent.tag not in ANCHOR_PARENT_TAGS:
            raise FCPMCPError(
                ErrorCode.UNSUPPORTED_CONTRACT,
                f"Primary-storyline <{parent.tag}> at "
                f"{timeline_offset.to_fcpxml()} cannot carry connected clips",
            )
    parent_offset = _time_attr(parent, "offset")
    parent_duration = _time_attr(parent, "duration")
    if not parent_offset <= timeline_offset < parent_offset + parent_duration:
        raise FCPMCPError(
            ErrorCode.INVALID_ARGUMENTS,
            f"timeline_offset {timeline_offset.to_fcpxml()} is outside anchor clip "
            f"'{parent.get('name', '')}' ({parent_offset.to_fcpxml()} + "
            f"{parent_duration.to_fcpxml()})",
        )
    if parent.find("timeMap") is not None:
        raise FCPMCPError(
            ErrorCode.UNSUPPORTED_CONTRACT,
            f"Anchor clip '{parent.get('name', '')}' is retimed (timeMap); "
            "its local time is not linear",
        )
    parent_start = _time_attr(parent, "start")
    local_offset = parent_start + (timeline_offset - parent_offset)
    return ConnectionPoint(
        parent=parent,
        parent_offset=parent_offset,
        parent_start=parent_start,
        local_offset=local_offset,
    )


# --- Resources --------------------------------------------------------------


def _used_ids(root: ElementTree.Element) -> set[str]:
    return {value for element in root.iter() if (value := element.get("id"))}


def unique_id(root: ElementTree.Element, prefix: str) -> str:
    """Return ``<prefix><n>`` unused anywhere in the document."""
    used = _used_ids(root)
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    highest = max(
        (int(match.group(1)) for value in used if (match := pattern.match(value))),
        default=0,
    )
    candidate = highest + 1
    while f"{prefix}{candidate}" in used:
        candidate += 1
    return f"{prefix}{candidate}"


def ensure_effect_resource(
    root: ElementTree.Element, name: str, uid: str
) -> tuple[ElementTree.Element, bool]:
    """Reuse the ``<effect>`` resource with exactly ``uid`` or add one.

    Returns ``(effect, created)``.
    """
    if not uid.strip():
        raise FCPMCPError(
            ErrorCode.INVALID_ARGUMENTS,
            "effect uid must not be empty",
        )
    resources = root.find("resources")
    if resources is None:
        raise FCPMCPError(
            ErrorCode.TARGET_NOT_FOUND,
            "FCPXML resources element not found",
        )
    for effect in resources.findall("effect"):
        if effect.get("uid") == uid:
            return effect, False
    effect = ElementTree.SubElement(resources, "effect")
    effect.set("id", unique_id(root, "r"))
    if name:
        effect.set("name", name)
    effect.set("uid", uid)
    return effect, True


# --- Values -----------------------------------------------------------------


def format_number(value: float) -> str:
    """Format a finite float compactly without losing practical precision."""
    if not math.isfinite(value):
        raise FCPMCPError(
            ErrorCode.INVALID_ARGUMENTS,
            f"numeric value must be finite: {value}",
        )
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def format_pair(x: float, y: float) -> str:
    return f"{format_number(x)} {format_number(y)}"


@dataclass(frozen=True)
class ParamSpec:
    key: str
    name: str
    value: str

    def attributes(self) -> dict[str, str]:
        return {"name": self.name, "key": self.key, "value": self.value}


def _param_value(value: object, label: str, key: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise FCPMCPError(
            ErrorCode.INVALID_ARGUMENTS,
            f"{label} value for '{key}' must be a string or number",
        )
    return value if isinstance(value, str) else format_number(float(value))


def parse_params(raw: Mapping[str, object], label: str) -> list[ParamSpec]:
    """Validate a parameter mapping.

    Each entry is ``"<key>": value`` (the key doubles as the display name) or
    ``"<key>": {"value": value, "name": "<display name>"}`` as returned by
    ``fcp_inspect_motion_template``.
    """
    params: list[ParamSpec] = []
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise FCPMCPError(
                ErrorCode.INVALID_ARGUMENTS,
                f"{label} keys must be non-empty strings",
            )
        name = key
        if isinstance(value, dict):
            unknown = set(value) - {"value", "name"}
            if "value" not in value or unknown:
                raise FCPMCPError(
                    ErrorCode.INVALID_ARGUMENTS,
                    f"{label} entry '{key}' must be {{\"value\": ..., \"name\"?: ...}}",
                )
            raw_name = value.get("name", key)
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise FCPMCPError(
                    ErrorCode.INVALID_ARGUMENTS,
                    f"{label} name for '{key}' must be a non-empty string",
                )
            name = raw_name
            value = value["value"]
        params.append(ParamSpec(key=key, name=name, value=_param_value(value, label, key)))
    return params


def append_params(element: ElementTree.Element, params: list[ParamSpec]) -> None:
    """Add ``<param name key value>`` children in DTD order."""
    for spec in params:
        insert_in_dtd_order(element, ElementTree.Element("param", spec.attributes()))


def param_specs_of(element: ElementTree.Element) -> list[ParamSpec]:
    """Read back direct ``<param>`` children as specs (for verification)."""
    return [
        ParamSpec(
            key=param.get("key", ""),
            name=param.get("name", ""),
            value=param.get("value", ""),
        )
        for param in element.findall("param")
    ]


def _show(value: object) -> str:
    """Render a user-supplied JSON value for an error message."""
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return f"<{type(value).__name__}>"


def parse_keyframe_value(param: str, raw: object) -> str:
    """Normalize a keyframe value for ``param`` to its FCPXML string."""
    if param == "rotation":
        if isinstance(raw, bool):
            raise FCPMCPError(ErrorCode.INVALID_ARGUMENTS, "rotation value must be a number")
        try:
            return format_number(float(raw))  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise FCPMCPError(
                ErrorCode.INVALID_ARGUMENTS,
                f"rotation keyframe value must be a number: {_show(raw)}",
            ) from error
    parts: list[object]
    if isinstance(raw, str):
        parts = list(raw.split())
    elif isinstance(raw, list):
        parts = list(raw)
    else:
        parts = []
    if len(parts) != 2 or any(isinstance(part, bool) for part in parts):
        raise FCPMCPError(
            ErrorCode.INVALID_ARGUMENTS,
            f"{param} keyframe value must be \"x y\" or [x, y]: {_show(raw)}",
        )
    try:
        x, y = (float(part) for part in parts)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise FCPMCPError(
            ErrorCode.INVALID_ARGUMENTS,
            f"{param} keyframe value must be numeric: {_show(raw)}",
        ) from error
    return format_pair(x, y)


# --- Mutations --------------------------------------------------------------


def set_transform(
    clip: ElementTree.Element,
    *,
    position: tuple[float, float],
    scale: tuple[float, float],
    rotation: float,
    anchor: tuple[float, float],
) -> tuple[ElementTree.Element, ElementTree.Element | None]:
    """Add or replace the clip's static ``adjust-transform``.

    Returns ``(new_transform, replaced_transform_or_None)``. A replaced
    transform's keyframed ``param`` children are discarded.
    """
    transform = ElementTree.Element(
        "adjust-transform",
        {
            "position": format_pair(*position),
            "scale": format_pair(*scale),
            "rotation": format_number(rotation),
            "anchor": format_pair(*anchor),
        },
    )
    previous = clip.find("adjust-transform")
    if previous is not None:
        for attribute in ("enabled", "tracking"):
            if attribute in previous.attrib:
                transform.set(attribute, previous.attrib[attribute])
        index = list(clip).index(previous)
        clip.remove(previous)
        clip.insert(index, transform)
    else:
        insert_in_dtd_order(clip, transform)
    return transform, previous


def ensure_transform(clip: ElementTree.Element) -> tuple[ElementTree.Element, bool]:
    """Return the clip's ``adjust-transform``, creating an identity one if absent."""
    transform = clip.find("adjust-transform")
    if transform is not None:
        return transform, False
    transform, _ = set_transform(
        clip, position=(0.0, 0.0), scale=(1.0, 1.0), rotation=0.0, anchor=(0.0, 0.0)
    )
    return transform, True


@dataclass(frozen=True)
class KeyframeSpec:
    time: str
    value: str
    interp: str
    curve: str | None


def set_keyframed_param(
    transform: ElementTree.Element, param_name: str, keyframes: list[KeyframeSpec]
) -> tuple[ElementTree.Element, bool]:
    """Add or replace ``<param name=param_name><keyframeAnimation>…`` in a transform.

    Returns ``(param, replaced)``.
    """
    param = ElementTree.Element("param", {"name": param_name})
    animation = ElementTree.SubElement(param, "keyframeAnimation")
    for keyframe in keyframes:
        attributes = {
            "time": keyframe.time,
            "value": keyframe.value,
            "interp": keyframe.interp,
        }
        if keyframe.curve is not None:
            attributes["curve"] = keyframe.curve
        ElementTree.SubElement(animation, "keyframe", attributes)
    existing = next(
        (child for child in transform.findall("param") if child.get("name") == param_name),
        None,
    )
    if existing is not None:
        index = list(transform).index(existing)
        transform.remove(existing)
        transform.insert(index, param)
        return param, True
    transform.append(param)
    return param, False


def add_video_filter(
    clip: ElementTree.Element,
    effect: ElementTree.Element,
    *,
    name: str,
    params: list[ParamSpec],
) -> ElementTree.Element:
    """Append a ``filter-video`` after the clip's existing video filters."""
    filter_video = build_video_filter(effect, name=name, params=params)
    insert_in_dtd_order(clip, filter_video)
    return filter_video


def build_video_filter(
    effect: ElementTree.Element, *, name: str, params: list[ParamSpec]
) -> ElementTree.Element:
    attributes = {"ref": effect.get("id", "")}
    if name:
        attributes["name"] = name
    filter_video = ElementTree.Element("filter-video", attributes)
    for spec in params:
        ElementTree.SubElement(filter_video, "param", spec.attributes())
    return filter_video


# --- Transitions ------------------------------------------------------------


@dataclass(frozen=True)
class TransitionPlacement:
    spine: ElementTree.Element
    before: ElementTree.Element
    after: ElementTree.Element
    cut: RationalTime
    offset: RationalTime


def place_transition(
    clip: ElementTree.Element, spine: ElementTree.Element, duration: RationalTime
) -> TransitionPlacement:
    """Validate a transition between ``clip`` and the next storyline item.

    The transition is centred on the cut (``offset = cut - duration / 2``)
    and must fit inside half of each neighbour. Neighbouring clips are not
    adjusted.
    """
    children = list(spine)
    if not any(clip is child for child in children) or clip.get("lane", "0") != "0":
        raise FCPMCPError(
            ErrorCode.UNSUPPORTED_CONTRACT,
            f"Clip '{clip.get('name', '')}' is not on a storyline spine",
        )
    index = next(i for i, child in enumerate(children) if child is clip)
    following = children[index + 1] if index + 1 < len(children) else None
    if following is None:
        raise FCPMCPError(
            ErrorCode.UNSUPPORTED_CONTRACT,
            f"Clip '{clip.get('name', '')}' has no following clip to transition into",
        )
    if following.tag == "transition":
        raise FCPMCPError(
            ErrorCode.UNSUPPORTED_CONTRACT,
            f"A transition already follows clip '{clip.get('name', '')}'",
        )
    half = RationalTime(duration.numerator, duration.denominator * 2)
    for neighbour in (clip, following):
        if half > _time_attr(neighbour, "duration"):
            raise FCPMCPError(
                ErrorCode.INVALID_ARGUMENTS,
                f"Transition duration {duration.to_fcpxml()} is longer than twice "
                f"'{neighbour.get('name', neighbour.tag)}'",
            )
    cut = _time_attr(clip, "offset") + _time_attr(clip, "duration")
    return TransitionPlacement(
        spine=spine,
        before=clip,
        after=following,
        cut=cut,
        offset=cut - half,
    )


def build_transition(
    placement: TransitionPlacement,
    effect: ElementTree.Element,
    *,
    name: str,
    duration: str,
    params: list[ParamSpec],
) -> ElementTree.Element:
    """Insert ``<transition><filter-video ref=effect>`` after ``placement.before``."""
    transition = ElementTree.Element(
        "transition",
        {"name": name, "offset": placement.offset.to_fcpxml(), "duration": duration},
    )
    transition.append(build_video_filter(effect, name=name, params=params))
    index = next(i for i, child in enumerate(placement.spine) if child is placement.before)
    placement.spine.insert(index + 1, transition)
    return transition
