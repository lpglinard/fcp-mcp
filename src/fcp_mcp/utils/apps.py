"""Locate Apple pro applications by bundle identifier.

Final Cut Pro, Motion and Compressor ship under several bundle names, for
example ``Final Cut Pro.app``, ``Final Cut Pro Trial.app`` or ``Final Cut Pro
Creator Studio.app``. Discovery therefore matches the ``CFBundleIdentifier``
in each candidate's ``Info.plist`` instead of a fixed path.
"""

from __future__ import annotations

import plistlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Ordered by preference when several editions are installed.
FINAL_CUT_PRO_BUNDLE_IDS = (
    "com.apple.FinalCut",
    "com.apple.FinalCutApp",
    "com.apple.FinalCutTrial",
)
MOTION_BUNDLE_IDS = (
    "com.apple.motionapp",
    "com.apple.motionappApp",
)
COMPRESSOR_BUNDLE_IDS = (
    "com.apple.Compressor",
    "com.apple.CompressorApp",
)

SYSTEM_APPLICATIONS = Path("/Applications")


def default_search_roots() -> tuple[Path, ...]:
    """``/Applications`` first, then the user's ``~/Applications``."""
    return SYSTEM_APPLICATIONS, Path.home() / "Applications"


@dataclass(frozen=True)
class InstalledApp:
    path: Path
    bundle_id: str
    executable: str
    version: str

    @property
    def executable_path(self) -> Path:
        return self.path / "Contents" / "MacOS" / self.executable


def _read_info(bundle: Path) -> dict[str, object] | None:
    info = bundle / "Contents" / "Info.plist"
    try:
        with info.open("rb") as handle:
            data = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _candidates(search_roots: Iterable[Path]) -> list[Path]:
    bundles: list[Path] = []
    for root in search_roots:
        try:
            if not root.is_dir():
                continue
            bundles.extend(sorted(root.glob("*.app")))
        except OSError:
            continue
    return bundles


def find_app(
    bundle_ids: Sequence[str],
    *,
    search_roots: Iterable[Path] | None = None,
) -> InstalledApp | None:
    """Return the preferred installed app whose bundle id is in ``bundle_ids``.

    Preference follows ``bundle_ids`` order, then search-root order, then
    bundle name.
    """
    roots = tuple(search_roots) if search_roots is not None else default_search_roots()
    found: list[tuple[int, int, InstalledApp]] = []
    for position, bundle in enumerate(_candidates(roots)):
        info = _read_info(bundle)
        if info is None:
            continue
        bundle_id = info.get("CFBundleIdentifier")
        if not isinstance(bundle_id, str) or bundle_id not in bundle_ids:
            continue
        executable = info.get("CFBundleExecutable")
        version = info.get("CFBundleShortVersionString")
        found.append(
            (
                bundle_ids.index(bundle_id),
                position,
                InstalledApp(
                    path=bundle,
                    bundle_id=bundle_id,
                    executable=executable if isinstance(executable, str) else bundle.stem,
                    version=version if isinstance(version, str) else "",
                ),
            )
        )
    if not found:
        return None
    return min(found, key=lambda item: (item[0], item[1]))[2]


def find_final_cut_pro(*, search_roots: Iterable[Path] | None = None) -> InstalledApp | None:
    return find_app(FINAL_CUT_PRO_BUNDLE_IDS, search_roots=search_roots)


def find_motion(*, search_roots: Iterable[Path] | None = None) -> InstalledApp | None:
    return find_app(MOTION_BUNDLE_IDS, search_roots=search_roots)


def find_compressor(*, search_roots: Iterable[Path] | None = None) -> InstalledApp | None:
    return find_app(COMPRESSOR_BUNDLE_IDS, search_roots=search_roots)


def final_cut_interchange_resources(app: InstalledApp) -> Path:
    """Directory holding the bundled ``FCPXMLv*.dtd`` files."""
    return app.path / "Contents/Frameworks/Interchange.framework/Versions/A/Resources"


def final_cut_motion_effect_resources(app: InstalledApp) -> Path:
    """Directory holding Final Cut's built-in Motion template roots."""
    return app.path / "Contents/PlugIns/MediaProviders/MotionEffect.fxp/Contents/Resources"
