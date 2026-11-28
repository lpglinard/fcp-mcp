"""Read-only inspection of Motion template documents (``ozml`` XML).

Motion templates (``.moti``, ``.moef``, ``.motr``, ``.motn``) are XML
documents with a ``<scene>`` holding ``layer`` / ``scenenode`` / ``filter``
objects (each with a numeric ``id``) and a ``<publishSettings>`` list of
``<target object=… channel=… name=…>`` entries: the parameters Final Cut
shows in its inspector.

FCPXML ``<param key=…>`` encoding. Final Cut stores Motion parameter keys as
``9999/<object id path>/<channel path>``: ``9999`` stands for the template
document, followed by the ids of the layer and nested objects that own the
parameter, then the parameter's channel ids. This is confirmed by keys Final
Cut itself stored for Basic Title (``9999/999166631/999166633/5/999166635/3``:
layer ``999166631`` → text object ``999166633`` → channel ``5/999166635/3``).
Text styles appear in that path as ``5/<style id>``. For published targets
this module builds the key from the target's object ancestry plus its
``channel`` attribute; members of a published group extend the channel path
with their own parameter ids. Keys whose object path runs only through
layers, scene nodes, and styles are reported as ``confirmed_pattern``. Keys
through filters (for example FxPlug plug-ins wrapped in a template),
behaviors, footage, or the document root are ``unverified``: Final Cut may
insert a folder channel there, as it does for styles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict
from xml.etree import ElementTree

from ..utils.safe_xml import parse_file

MAX_TEMPLATE_BYTES = 64 * 1024 * 1024
KEY_ROOT = "9999"
_OBJECT_TAGS = frozenset(
    {"layer", "scenenode", "filter", "behavior", "footage", "clip", "style"}
)
# Text styles are reached through channel 5 of their text object; this is the
# form Final Cut stored for Basic Title (".../999166633/5/999166635/3").
_STYLE_CHANNEL = "5"
# Objects whose path segment is confirmed by Final Cut-stored keys.
_CONFIRMED_TAGS = frozenset({"layer", "scenenode", "style"})


@dataclass(frozen=True)
class TemplateParameter:
    name: str
    key: str
    published_name: str
    object_id: str
    object_name: str
    channel: str
    kind: str
    default: str | None
    value: str | None
    minimum: str | None
    maximum: str | None
    options: tuple[str, ...]
    key_basis: str  # "published_target" or "group_member"
    key_confidence: str  # "confirmed_pattern" or "unverified"


@dataclass(frozen=True)
class TextLayer:
    name: str
    object_id: str
    object_key: str
    text: str | None


@dataclass(frozen=True)
class EmbeddedPlugin:
    object_name: str
    plugin_name: str
    plugin_uuid: str
    plugin_version: str | None


@dataclass
class TemplateInspection:
    ozml_version: str
    width: int | None
    height: int | None
    duration_frames: int | None
    frame_rate: float | None
    theme: str | None
    parameters: list[TemplateParameter] = field(default_factory=list)
    text_layers: list[TextLayer] = field(default_factory=list)
    plugins: list[EmbeddedPlugin] = field(default_factory=list)
    total_parameters: int = 0
    truncated: bool = False
    unnamed_parameters: int = 0


class TemplateParseError(ValueError):
    """The template is not a readable ozml document."""


def _int(text: str | None) -> int | None:
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def _float(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def _object_paths(scene: ElementTree.Element) -> dict[str, tuple[list[str], ElementTree.Element, bool]]:
    """Map object id → (key path below the document root, element, confirmed).

    ``confirmed`` is False when the path runs through an object kind whose key
    segment has not been confirmed against Final Cut (filters, behaviors,
    footage, the document root itself).
    """
    result: dict[str, tuple[list[str], ElementTree.Element, bool]] = {}

    def walk(element: ElementTree.Element, path: list[str], top_level: bool, confirmed: bool) -> None:
        for child in element:
            if child.tag not in _OBJECT_TAGS or not child.get("id"):
                continue
            object_id = child.get("id", "")
            # The top-level Project scenenode is the document itself (9999).
            is_root = top_level and child.tag == "scenenode" and child.get("name") == "Project"
            if is_root:
                child_path = path
            elif child.tag == "style":
                child_path = [*path, _STYLE_CHANNEL, object_id]
            else:
                child_path = [*path, object_id]
            child_confirmed = confirmed and not is_root and child.tag in _CONFIRMED_TAGS
            result.setdefault(object_id, (child_path, child, child_confirmed))
            walk(child, child_path, False, child_confirmed)

    walk(scene, [], True, True)
    return result


def _find_channel(owner: ElementTree.Element, channel: str) -> ElementTree.Element | None:
    current: ElementTree.Element | None = owner
    for part in [segment for segment in channel.split("/") if segment not in ("", ".")]:
        if current is None:
            return None
        current = next(
            (child for child in current if child.tag == "parameter" and child.get("id") == part),
            None,
        )
    return current if current is not owner else None


class _Description(TypedDict):
    kind: str
    default: str | None
    value: str | None
    minimum: str | None
    maximum: str | None
    options: tuple[str, ...]


def _describe(parameter: ElementTree.Element) -> _Description:
    curve = parameter.find("curve")
    entries = [entry.get("name", "") for entry in parameter.findall("entry")]
    if parameter.find("parameter") is not None:
        kind = "group"
    elif entries:
        kind = "menu"
    elif parameter.find("font") is not None:
        kind = "font"
    elif parameter.find("text") is not None:
        kind = "text"
    elif curve is not None:
        kind = "curve"
    elif parameter.find("dataValue") is not None:
        kind = "data"
    elif "value" in parameter.attrib or "default" in parameter.attrib:
        kind = "scalar"
    else:
        kind = "unknown"
    default = parameter.get("default")
    value = parameter.get("value")
    minimum = maximum = None
    if curve is not None:
        default = curve.get("default", default)
        value = curve.get("value", value)
        minimum = curve.findtext("min")
        maximum = curve.findtext("max")
    if kind == "text":
        value = parameter.findtext("text")
    if kind == "font":
        value = parameter.findtext("font")
        default = parameter.findtext("defaultFont")
    return {
        "kind": kind,
        "default": default,
        "value": value,
        "minimum": minimum,
        "maximum": maximum,
        "options": tuple(entries),
    }


def _leaves(parameter: ElementTree.Element, channel: list[str]) -> list[tuple[ElementTree.Element, list[str]]]:
    children = [child for child in parameter if child.tag == "parameter" and child.get("id")]
    if not children:
        return [(parameter, channel)]
    leaves: list[tuple[ElementTree.Element, list[str]]] = []
    for child in children:
        leaves.extend(_leaves(child, [*channel, child.get("id", "")]))
    return leaves


def inspect_template(path: Path, *, max_parameters: int = 200) -> TemplateInspection:
    """Parse a Motion template and list its published parameters."""
    try:
        size = path.stat().st_size
    except OSError as error:
        raise TemplateParseError(f"cannot read template: {error}") from error
    if size > MAX_TEMPLATE_BYTES:
        raise TemplateParseError(f"template is larger than {MAX_TEMPLATE_BYTES} bytes")
    try:
        root = parse_file(path).getroot()
    except Exception as error:  # defusedxml raises several error types
        raise TemplateParseError(f"template is not valid XML: {error}") from error
    if root.tag != "ozml":
        raise TemplateParseError(f"not a Motion document (root <{root.tag}>)")
    scene = root.find("scene")
    if scene is None:
        raise TemplateParseError("Motion document has no <scene>")
    settings = scene.find("sceneSettings")
    inspection = TemplateInspection(
        ozml_version=root.get("version", ""),
        width=_int(settings.findtext("width")) if settings is not None else None,
        height=_int(settings.findtext("height")) if settings is not None else None,
        duration_frames=_int(settings.findtext("duration")) if settings is not None else None,
        frame_rate=_float(settings.findtext("frameRate")) if settings is not None else None,
        theme=root.findtext("template/theme"),
    )
    objects = _object_paths(scene)
    factories = {
        factory.get("id", ""): factory.findtext("description") or ""
        for factory in root.findall("factory")
    }

    parameters: list[TemplateParameter] = []
    for target in scene.findall("publishSettings/target"):
        object_id = target.get("object", "")
        channel = target.get("channel", "")
        if object_id not in objects:
            continue
        object_path, owner, confirmed = objects[object_id]
        confidence = "confirmed_pattern" if confirmed else "unverified"
        channel_ids = [segment for segment in channel.split("/") if segment not in ("", ".")]
        base_key = "/".join([KEY_ROOT, *object_path, *channel_ids])
        element = _find_channel(owner, channel)
        published_name = target.get("name", "")
        if element is None:
            if not published_name:
                inspection.unnamed_parameters += 1
                continue
            parameters.append(
                TemplateParameter(
                    name=published_name, key=base_key, published_name=published_name,
                    object_id=object_id, object_name=owner.get("name", ""),
                    channel=channel, kind="unknown", default=None, value=None,
                    minimum=None, maximum=None, options=(), key_basis="published_target",
                    key_confidence=confidence,
                )
            )
            continue
        for leaf, leaf_channel in _leaves(element, channel_ids):
            is_target = leaf is element
            display = (published_name or leaf.get("name", "")) if is_target else leaf.get(
                "name", ""
            )
            if not display:
                # Unnamed entries are internal plug-in data FCP does not show.
                inspection.unnamed_parameters += 1
                continue
            described = _describe(leaf)
            parameters.append(
                TemplateParameter(
                    name=display,
                    key="/".join([KEY_ROOT, *object_path, *leaf_channel]),
                    published_name=published_name,
                    object_id=object_id,
                    object_name=owner.get("name", ""),
                    channel="./" + "/".join(leaf_channel),
                    kind=described["kind"],
                    default=described["default"],
                    value=described["value"],
                    minimum=described["minimum"],
                    maximum=described["maximum"],
                    options=described["options"],
                    key_basis="published_target" if is_target else "group_member",
                    key_confidence=confidence,
                )
            )
    inspection.total_parameters = len(parameters)
    inspection.truncated = len(parameters) > max_parameters
    inspection.parameters = parameters[:max_parameters]

    for object_id, (object_path, element, _confirmed) in objects.items():
        if element.tag == "scenenode" and factories.get(element.get("factoryID", "")) == "Text":
            text_parameter = next(
                (p for p in element.iter("parameter") if p.get("name") == "Text"
                 and p.find("text") is not None),
                None,
            )
            inspection.text_layers.append(
                TextLayer(
                    name=element.get("name", ""),
                    object_id=object_id,
                    object_key="/".join([KEY_ROOT, *object_path]),
                    text=text_parameter.findtext("text") if text_parameter is not None else None,
                )
            )
        if element.get("pluginUUID"):
            inspection.plugins.append(
                EmbeddedPlugin(
                    object_name=element.get("name", ""),
                    plugin_name=element.get("pluginName", ""),
                    plugin_uuid=element.get("pluginUUID", ""),
                    plugin_version=element.get("pluginVersion"),
                )
            )
    return inspection
