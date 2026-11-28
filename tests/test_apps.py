"""Bundle-id discovery of Final Cut Pro, Motion, and Compressor."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from fcp_mcp import diagnostics
from fcp_mcp.utils import apps


def _bundle(root: Path, name: str, bundle_id: str, executable: str, version: str = "1.0") -> Path:
    bundle = root / f"{name}.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / executable).write_bytes(b"")
    with (bundle / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleIdentifier": bundle_id,
                "CFBundleExecutable": executable,
                "CFBundleShortVersionString": version,
            },
            handle,
        )
    return bundle


@pytest.mark.parametrize(
    "bundle_id", ["com.apple.FinalCut", "com.apple.FinalCutApp", "com.apple.FinalCutTrial"]
)
def test_final_cut_is_found_under_any_bundle_name(tmp_path, bundle_id):
    _bundle(tmp_path, "Some Other App", "com.example.other", "Other")
    bundle = _bundle(tmp_path, "Final Cut Pro Creator Studio", bundle_id, "Final Cut Pro", "13.0")

    app = apps.find_final_cut_pro(search_roots=(tmp_path,))

    assert app is not None
    assert app.path == bundle
    assert app.bundle_id == bundle_id
    assert app.executable == "Final Cut Pro"
    assert app.version == "13.0"
    assert apps.final_cut_interchange_resources(app).parts[-5:] == (
        "Frameworks", "Interchange.framework", "Versions", "A", "Resources",
    )


def test_preference_follows_bundle_id_order_then_root_order(tmp_path):
    system = tmp_path / "system"
    user = tmp_path / "user"
    system.mkdir()
    user.mkdir()
    _bundle(system, "Final Cut Pro Trial", "com.apple.FinalCutTrial", "Final Cut Pro")
    full = _bundle(user, "Final Cut Pro", "com.apple.FinalCut", "Final Cut Pro")

    assert apps.find_final_cut_pro(search_roots=(system, user)).path == full


def test_motion_and_compressor_editions(tmp_path):
    motion = _bundle(tmp_path, "Motion Creator Studio", "com.apple.motionappApp", "Motion")
    compressor = _bundle(
        tmp_path, "Compressor Creator Studio", "com.apple.CompressorApp", "Compressor"
    )

    assert apps.find_motion(search_roots=(tmp_path,)).path == motion
    found = apps.find_compressor(search_roots=(tmp_path,))
    assert found.executable_path == compressor / "Contents" / "MacOS" / "Compressor"


def test_unreadable_or_missing_roots_find_nothing(tmp_path):
    broken = tmp_path / "Broken.app" / "Contents"
    broken.mkdir(parents=True)
    (broken / "Info.plist").write_bytes(b"not a plist")

    assert apps.find_final_cut_pro(search_roots=(tmp_path, tmp_path / "missing")) is None


def test_doctor_reports_discovered_edition(monkeypatch, tmp_path):
    bundle = _bundle(tmp_path, "Final Cut Pro Creator Studio", "com.apple.FinalCutApp", "FCP Exec")
    monkeypatch.setattr(apps, "default_search_roots", lambda: (tmp_path,))
    seen: list[str] = []
    monkeypatch.setattr(
        diagnostics, "_process_running", lambda name: seen.append(name) or False
    )

    check, installed, running = diagnostics._fcp_check()

    assert (installed, running) == (True, False)
    assert check.status == "pass"
    assert check.details["path"] == str(bundle)
    assert check.details["bundle_id"] == "com.apple.FinalCutApp"
    assert seen == ["FCP Exec"]
