"""Discover Motion templates and the FCPXML ``uid`` Final Cut uses for them.

Two kinds of roots are scanned:

* the user's Motion Templates folder,
  ``~/Movies/Motion Templates.localized/<Category>.localized/...``, where
  third-party packs often use folders without a ``.localized`` suffix; and
* Final Cut Pro's built-in template roots inside the application bundle,
  ``<Final Cut>.app/Contents/PlugIns/MediaProviders/MotionEffect.fxp/
  Contents/Resources/*Templates.localized``.

uid convention:

* built-in templates: ``.../`` followed by the path relative to the
  built-in root, e.g.
  ``.../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti``
  (seen in a Final Cut FCPXML export; the template lives in the
  ``PETemplates.localized`` root);
* user templates: ``~/`` followed by the path relative to the Motion
  Templates folder, e.g. ``~/Effects.localized/Pack/Look/Look.moef`` (the
  form Final Cut stores in its library databases for user templates).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..utils import paths as utility_paths
from ..utils.apps import final_cut_motion_effect_resources, find_final_cut_pro

CATEGORY_EXTENSIONS = {
    "Titles": ".moti",
    "Effects": ".moef",
    "Transitions": ".motr",
    "Generators": ".motn",
}
TEMPLATE_EXTENSIONS = frozenset(CATEGORY_EXTENSIONS.values())
# Built-in roots in lookup order. Only PETemplates.localized is confirmed by a
# Final Cut export (Basic Title); the others share the ".../" namespace.
_BUILTIN_ROOT_ORDER = (
    "PETemplates.localized",
    "Templates.localized",
    "METemplates.localized",
    "iOSTemplates.localized",
)
USER_UID_PREFIX = "~/"
BUILTIN_UID_PREFIX = ".../"


@dataclass(frozen=True)
class TemplateRoot:
    source: str  # "user" or "built-in"
    path: Path
    label: str

    @property
    def uid_prefix(self) -> str:
        return USER_UID_PREFIX if self.source == "user" else BUILTIN_UID_PREFIX


@dataclass(frozen=True)
class MotionTemplate:
    category: str
    name: str
    group: tuple[str, ...]
    path: Path
    extension: str
    uid: str
    source: str
    root: str
    collection: str | None
    large_preview: Path | None
    small_preview: Path | None


def _strip_localized(name: str) -> str:
    return name.removesuffix(".localized")


def user_template_root() -> TemplateRoot:
    return TemplateRoot(
        source="user",
        path=utility_paths.motion_templates_dir(),
        label="Motion Templates",
    )


def builtin_template_roots() -> list[TemplateRoot]:
    """Built-in template roots of the installed Final Cut Pro (any edition)."""
    app = find_final_cut_pro()
    if app is None:
        return []
    resources = final_cut_motion_effect_resources(app)
    try:
        found = [entry for entry in resources.glob("*Templates.localized") if entry.is_dir()]
    except OSError:
        return []

    def order(entry: Path) -> tuple[int, str]:
        name = entry.name
        index = _BUILTIN_ROOT_ORDER.index(name) if name in _BUILTIN_ROOT_ORDER else 99
        return index, name

    return [
        TemplateRoot(source="built-in", path=entry, label=entry.name)
        for entry in sorted(found, key=order)
    ]


def template_roots() -> list[TemplateRoot]:
    """User root first, then built-in roots."""
    return [user_template_root(), *builtin_template_roots()]


def _preview(folder: Path, name: str) -> Path | None:
    candidate = folder / name
    return candidate if candidate.is_file() else None


def _template_for(root: TemplateRoot, file: Path) -> MotionTemplate | None:
    try:
        relative = file.relative_to(root.path)
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) < 2:
        return None
    category = _strip_localized(parts[0])
    if CATEGORY_EXTENSIONS.get(category) != file.suffix:
        return None
    folders = [_strip_localized(part) for part in parts[1:-1]]
    # The innermost folder normally carries the template's own name.
    if folders and folders[-1] == file.stem:
        folders = folders[:-1]
    collection = folders[0] if folders and root.source == "user" else None
    return MotionTemplate(
        category=category,
        name=file.stem,
        group=tuple(folders),
        path=file,
        extension=file.suffix,
        uid=root.uid_prefix + relative.as_posix(),
        source=root.source,
        root=root.label,
        collection=collection,
        large_preview=_preview(file.parent, "large.png"),
        small_preview=_preview(file.parent, "small.png"),
    )


def scan_root(root: TemplateRoot) -> list[MotionTemplate]:
    if not root.path.is_dir():
        return []
    templates: list[MotionTemplate] = []
    for category_dir in sorted(root.path.iterdir()):
        if not category_dir.is_dir():
            continue
        if _strip_localized(category_dir.name) not in CATEGORY_EXTENSIONS:
            continue
        for file in sorted(category_dir.rglob("*")):
            if file.suffix in TEMPLATE_EXTENSIONS and file.is_file():
                template = _template_for(root, file)
                if template is not None:
                    templates.append(template)
    return templates


def list_templates(roots: Iterable[TemplateRoot] | None = None) -> list[MotionTemplate]:
    """All templates; a uid already seen in an earlier root is skipped."""
    seen: set[str] = set()
    templates: list[MotionTemplate] = []
    for root in roots if roots is not None else template_roots():
        for template in scan_root(root):
            if template.uid in seen:
                continue
            seen.add(template.uid)
            templates.append(template)
    return templates


def resolve_template(
    reference: str,
    roots: Iterable[TemplateRoot] | None = None,
) -> tuple[TemplateRoot, Path] | None:
    """Resolve an FCPXML uid or a filesystem path to a template inside a root.

    Returns ``None`` when the reference does not name a template file inside
    one of the roots (after resolving symlinks).
    """
    root_list = list(roots) if roots is not None else template_roots()
    candidates: list[tuple[TemplateRoot, Path]] = []
    if reference.startswith(USER_UID_PREFIX):
        relative = reference[len(USER_UID_PREFIX):]
        candidates = [(root, root.path / relative) for root in root_list if root.source == "user"]
    elif reference.startswith(BUILTIN_UID_PREFIX):
        relative = reference[len(BUILTIN_UID_PREFIX):]
        candidates = [
            (root, root.path / relative) for root in root_list if root.source == "built-in"
        ]
    else:
        path = Path(reference).expanduser()
        candidates = [(root, path) for root in root_list]
    for root, candidate in candidates:
        try:
            resolved = candidate.resolve()
            base = root.path.resolve()
        except OSError:
            continue
        if (
            resolved.is_relative_to(base)
            and resolved.suffix in TEMPLATE_EXTENSIONS
            and resolved.is_file()
        ):
            return root, resolved
    return None
