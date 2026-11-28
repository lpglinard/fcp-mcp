"""Motion template discovery, inspection, and template-driven FCPXML edits."""

from __future__ import annotations

import json
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from fcp_mcp import server
from fcp_mcp.config import RuntimeConfig
from fcp_mcp.contracts import ErrorCode, FCPMCPError
from fcp_mcp.fcpxml.validator import FCPXMLValidator
from fcp_mcp.motion import library
from fcp_mcp.security.paths import PathPolicy
from fcp_mcp.utils import paths as utility_paths
from scripts.apple_dtd_gate import validate_fcpxml

FIXTURES = Path(__file__).parent / "fixtures"
MOTION_FIXTURES = FIXTURES / "motion"
BASIC_TITLE_UID = (
    ".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"
)


def _dtd_dirs() -> list[Path]:
    return sorted(
        Path("/Applications").glob(
            "Final Cut Pro*.app/Contents/Frameworks/Interchange.framework/"
            "Versions/A/Resources"
        )
    )


def _place(root: Path, relative: str, fixture: str | None = None) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if fixture:
        shutil.copy2(MOTION_FIXTURES / fixture, target)
    else:
        target.write_text("<ozml/>", encoding="utf-8")
    return target


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    user = tmp_path / "Motion Templates.localized"
    builtin_a = tmp_path / "App" / "PETemplates.localized"
    builtin_b = tmp_path / "App" / "Templates.localized"
    _place(user, "Effects.localized/Vendor Pack/Looks v2/Glow/Glow.moef", "Synthetic Glow.moef")
    (user / "Effects.localized/Vendor Pack/Looks v2/Glow/large.png").write_bytes(b"png")
    (user / "Effects.localized/Vendor Pack/Looks v2/Glow/small.png").write_bytes(b"png")
    _place(user, "Titles.localized/Vendor Pack/Headline/Headline.moti", "Synthetic Title.moti")
    _place(user, "Transitions.localized/Vendor Pack/Swipe/Swipe.motr")
    _place(user, "Generators.localized/Backdrop/Backdrop.motn")
    _place(user, "Titles.localized/Misfiled/Wrong.moef")  # extension does not match category
    _place(builtin_a, "Titles.localized/Simple.localized/Plain.localized/Plain.moti")
    _place(builtin_b, "Titles.localized/Simple.localized/Plain.localized/Plain.moti")
    _place(builtin_b, "Transitions.localized/Dissolves.localized/Fade.localized/Fade.motr")
    monkeypatch.setattr(utility_paths, "motion_templates_dir", lambda: user)
    monkeypatch.setattr(
        library,
        "builtin_template_roots",
        lambda: [
            library.TemplateRoot("built-in", builtin_a, builtin_a.name),
            library.TemplateRoot("built-in", builtin_b, builtin_b.name),
        ],
    )
    return {"user": user, "builtin_a": builtin_a, "builtin_b": builtin_b, "tmp": tmp_path}


@pytest.fixture
def policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = RuntimeConfig.from_env(
        {
            "FCP_MCP_OUTPUT_DIR": str(tmp_path),
            "FCP_MCP_ALLOWED_ROOTS": str(tmp_path),
            "FCP_MCP_PROFILE": "full",
        },
        home=tmp_path,
    )
    monkeypatch.setattr(server, "CONFIG", config)
    monkeypatch.setattr(server, "PATHS", PathPolicy(config))


# --- discovery ---------------------------------------------------------------


def test_list_covers_all_categories_with_uids_previews_and_sources(roots):
    outcome = server.fcp_list_motion_templates()
    records = {(t.category, t.name): t for t in outcome.structured.templates}

    assert set(records) == {
        ("Effects", "Glow"),
        ("Titles", "Headline"),
        ("Transitions", "Swipe"),
        ("Generators", "Backdrop"),
        ("Titles", "Plain"),
        ("Transitions", "Fade"),
    }
    glow = records[("Effects", "Glow")]
    assert glow.uid == "~/Effects.localized/Vendor Pack/Looks v2/Glow/Glow.moef"
    assert (glow.source, glow.root, glow.extension) == ("user", "Motion Templates", ".moef")
    assert glow.group == ["Vendor Pack", "Looks v2"]
    assert glow.collection == "Vendor Pack"
    assert glow.large_preview.endswith("Glow/large.png")
    assert glow.small_preview.endswith("Glow/small.png")
    plain = records[("Titles", "Plain")]
    assert plain.uid == ".../Titles.localized/Simple.localized/Plain.localized/Plain.moti"
    assert (plain.source, plain.root, plain.collection) == (
        "built-in", "PETemplates.localized", None,
    )
    assert plain.group == ["Simple"]
    assert records[("Transitions", "Fade")].root == "Templates.localized"
    assert json.loads(outcome.text)["Titles"] == ["Headline", "Plain"]


def test_list_fails_only_when_no_root_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(utility_paths, "motion_templates_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(library, "builtin_template_roots", list)
    with pytest.raises(FCPMCPError) as caught:
        server.fcp_list_motion_templates()
    assert caught.value.code is ErrorCode.TARGET_NOT_FOUND


def test_search_filters_and_caps(roots):
    outcome = server.fcp_search_motion_templates(query="vendor", limit=2)
    assert outcome.structured.total == 3
    assert outcome.structured.truncated is True
    assert len(outcome.structured.templates) == 2

    titles = server.fcp_search_motion_templates(category="Titles", source="built-in")
    assert [t.name for t in titles.structured.templates] == ["Plain"]
    words = server.fcp_search_motion_templates(query="LOOKS glow")
    assert [t.name for t in words.structured.templates] == ["Glow"]


@pytest.mark.parametrize(
    "kwargs",
    [{"category": "Sounds"}, {"source": "cloud"}, {"limit": 0}],
)
def test_search_rejects_bad_filters(roots, kwargs):
    with pytest.raises(FCPMCPError) as caught:
        server.fcp_search_motion_templates(**kwargs)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENTS


def test_resolve_accepts_uids_and_paths_inside_roots_only(roots, tmp_path):
    user = roots["user"]
    assert library.resolve_template("~/Effects.localized/Vendor Pack/Looks v2/Glow/Glow.moef")
    assert library.resolve_template(
        ".../Transitions.localized/Dissolves.localized/Fade.localized/Fade.motr"
    )
    inside = user / "Titles.localized/Vendor Pack/Headline/Headline.moti"
    assert library.resolve_template(str(inside))[1] == inside.resolve()

    outside = tmp_path / "elsewhere" / "Evil.moef"
    outside.parent.mkdir()
    outside.write_text("<ozml/>", encoding="utf-8")
    link = user / "Effects.localized" / "Escape.moef"
    os.symlink(outside, link)
    (user / "Effects.localized" / "notes.txt").write_text("x", encoding="utf-8")
    for reference in (
        str(outside),
        str(link),
        "~/../elsewhere/Evil.moef",
        ".../../../elsewhere/Evil.moef",
        "~/Effects.localized/notes.txt",
        "~/Effects.localized/Missing.moef",
    ):
        assert library.resolve_template(reference) is None, reference


# --- inspection --------------------------------------------------------------


def test_inspect_effect_reports_keys_types_and_plugins(roots):
    outcome = server.fcp_inspect_motion_template(
        "~/Effects.localized/Vendor Pack/Looks v2/Glow/Glow.moef"
    )
    result = outcome.structured
    by_name = {p.name: p for p in result.parameters}

    assert result.template.category == "Effects"
    assert (result.width, result.height, result.duration_frames, result.frame_rate) == (
        1920, 1080, 240, 24.0,
    )
    assert result.theme == "Synthetic"
    assert by_name["Brightness"].key == "9999/20003/20007/20119/59/63"
    assert by_name["Brightness"].key_basis == "group_member"
    assert (by_name["Brightness"].default, by_name["Brightness"].value) == ("100", "80")
    size = by_name["Size"]
    assert (size.kind, size.minimum, size.maximum, size.value) == ("curve", "0", "10", "2")
    mode = by_name["Mode"]
    assert (mode.kind, mode.options, mode.key_basis) == ("menu", ["Soft", "Hard"], "published_target")
    assert by_name["HDR Level"].key == "9999/1/103"
    # Paths through a filter or the document root are not confirmed by Final Cut.
    assert {p.key_confidence for p in result.parameters} == {"unverified"}
    assert result.unnamed_parameters == 1
    assert result.total_parameters == 4
    assert [p.plugin_uuid for p in result.plugins] == ["00000000-0000-4000-8000-0000000000AA"]
    assert "4 of 4 parameters" in outcome.text


def test_inspect_title_reports_text_layers_and_truncates(roots):
    path = roots["user"] / "Titles.localized/Vendor Pack/Headline/Headline.moti"
    result = server.fcp_inspect_motion_template(str(path), max_parameters=1).structured

    assert result.truncated is True
    assert result.total_parameters == 3
    assert [p.name for p in result.parameters] == ["Headline"]
    assert result.parameters[0].kind == "text"
    assert result.parameters[0].key == "9999/30001/30010/2/369"
    assert result.parameters[0].key_confidence == "confirmed_pattern"


def test_inspect_text_style_keys_use_the_style_channel(roots):
    result = server.fcp_inspect_motion_template(
        "~/Titles.localized/Vendor Pack/Headline/Headline.moti"
    ).structured
    size = next(p for p in result.parameters if p.name == "Size")
    # Same shape as the key Final Cut stored for Basic Title: .../<text>/5/<style>/3
    assert size.key == "9999/30001/30010/5/30020/3"
    assert (size.key_confidence, size.value) == ("confirmed_pattern", "96")
    assert [(t.name, t.object_key, t.text) for t in result.text_layers] == [
        ("Headline Text", "9999/30001/30010", "Your Headline"),
    ]


def test_inspect_never_modifies_the_template(roots):
    path = roots["user"] / "Effects.localized/Vendor Pack/Looks v2/Glow/Glow.moef"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    server.fcp_inspect_motion_template(str(path))
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize(
    ("reference", "code"),
    [
        ("~/Effects.localized/Missing/Missing.moef", ErrorCode.TARGET_NOT_FOUND),
        ("/etc/hosts", ErrorCode.TARGET_NOT_FOUND),
        ("~/Titles.localized/Misfiled/Wrong.moef", ErrorCode.UNSUPPORTED_CONTRACT),
        ("~/Transitions.localized/Vendor Pack/Swipe/Swipe.motr", ErrorCode.VALIDATION_FAILED),
    ],
)
def test_inspect_failures(roots, reference, code):
    swipe = roots["user"] / "Transitions.localized/Vendor Pack/Swipe/Swipe.motr"
    swipe.write_text("<<not xml", encoding="utf-8")
    with pytest.raises(FCPMCPError) as caught:
        server.fcp_inspect_motion_template(reference)
    assert caught.value.code is code


def test_inspect_rejects_bad_limit(roots):
    with pytest.raises(FCPMCPError) as caught:
        server.fcp_inspect_motion_template("~/Titles.localized/Vendor Pack/Headline/Headline.moti", 0)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENTS


# --- template-driven edits -------------------------------------------------


@pytest.fixture
def timeline(tmp_path: Path, policy: None) -> Path:
    path = tmp_path / "timeline.fcpxml"
    shutil.copy2(FIXTURES / "vertical_composition.fcpxml", path)
    return path


def _params_json(parameters) -> str:
    return json.dumps(
        {p.key: {"name": p.name, "value": p.value or ""} for p in parameters}
    )


def test_inspected_title_params_round_trip_into_connected_title(roots, timeline, tmp_path):
    inspection = server.fcp_inspect_motion_template(
        "~/Titles.localized/Vendor Pack/Headline/Headline.moti"
    ).structured
    out = tmp_path / "title.fcpxml"
    parameters = [p.model_copy(update={"value": "Big News"}) if p.kind == "text" else p
                  for p in inspection.parameters]

    outcome = server.fcpxml_add_connected_title(
        str(timeline), "", "3601s", "2s", lane=2,
        title_name=inspection.template.name, title_uid=inspection.template.uid,
        params_json=_params_json(parameters), output_path=str(out),
    )

    title = ET.parse(out).getroot().find(".//spine/asset-clip/title")
    assert title.get("name") == "Headline"
    assert title.find("text") is None and title.find("text-style-def") is None
    assert [p.attrib for p in title.findall("param")] == [
        {"name": "Headline", "key": "9999/30001/30010/2/369", "value": "Big News"},
        {"name": "Font", "key": "9999/30001/30010/2/83", "value": "Helvetica-Bold"},
        {"name": "Size", "key": "9999/30001/30010/5/30020/3", "value": "96"},
    ]
    record = outcome.structured.element
    assert record.has_text_element is False
    first = record.parameters[0]
    assert (first.name, first.key, first.value) == (
        "Headline", "9999/30001/30010/2/369", "Big News",
    )
    assert FCPXMLValidator().validate_file(out).valid
    for resources in _dtd_dirs():
        validate_fcpxml(out, dtd_path=resources / "FCPXMLv1_11.dtd")


def test_inspected_effect_params_round_trip_into_filter(roots, timeline, tmp_path):
    inspection = server.fcp_inspect_motion_template(
        "~/Effects.localized/Vendor Pack/Looks v2/Glow/Glow.moef"
    ).structured
    chosen = [p for p in inspection.parameters if p.name in {"Brightness", "Mode"}]
    out = tmp_path / "fx.fcpxml"

    server.fcpxml_add_video_effect(
        str(timeline), "Talk", inspection.template.name, inspection.template.uid,
        params_json=_params_json(chosen), output_path=str(out),
    )

    filters = ET.parse(out).getroot().find(".//spine/asset-clip").findall("filter-video")
    assert [p.attrib for p in filters[-1].findall("param")] == [
        {"name": "Brightness", "key": "9999/20003/20007/20119/59/63", "value": "80"},
        {"name": "Mode", "key": "9999/20003/20007/20119/105", "value": "1"},
    ]


@pytest.mark.parametrize(
    "params_json",
    ['{"k": {"name": "x"}}', '{"k": {"value": "1", "extra": 1}}', '{"k": {"value": "1", "name": ""}}'],
)
def test_param_objects_are_validated(timeline, tmp_path, params_json):
    with pytest.raises(FCPMCPError) as caught:
        server.fcpxml_add_video_effect(
            str(timeline), "Talk", "X", "FFGaussianBlur", params_json=params_json,
            output_path=str(tmp_path / "never.fcpxml"),
        )
    assert caught.value.code is ErrorCode.INVALID_ARGUMENTS


def test_transition_template_is_centred_dtd_valid_and_deduped(timeline, tmp_path):
    first = tmp_path / "t1.fcpxml"
    second = tmp_path / "t2.fcpxml"
    uid = "~/Transitions.localized/Example/Swipe/Swipe.motr"

    one = server.fcpxml_add_transition_template(
        str(timeline), "Gap", "Swipe", uid, duration="1s",
        params_json='{"9999/1/2": {"name": "Direction", "value": "1"}}', output_path=str(first),
    )
    two = server.fcpxml_add_transition_template(
        str(first), "Talk", "Swipe", uid, duration="2s", output_path=str(second),
    )

    spine = list(ET.parse(second).getroot().find(".//spine"))
    assert [child.tag for child in spine] == [
        "asset-clip", "transition", "gap", "transition", "asset-clip", "asset-clip",
    ]
    second_transition, first_transition = spine[1], spine[3]
    # Talk ends at 3610s; a 2s transition is centred on the cut.
    assert second_transition.attrib == {"name": "Swipe", "offset": "3609s", "duration": "2s"}
    # Gap ends at 3612s; 1s centred on the cut -> 7223/2s.
    assert first_transition.get("offset") == "7223/2s"
    filter_video = first_transition.find("filter-video")
    assert [p.attrib for p in filter_video.findall("param")] == [
        {"name": "Direction", "key": "9999/1/2", "value": "1"},
    ]
    effects = [
        e for e in ET.parse(second).getroot().findall("resources/effect") if e.get("uid") == uid
    ]
    assert len(effects) == 1
    assert (one.structured.transition.resource_created, two.structured.transition.resource_created) == (
        True, False,
    )
    assert two.structured.transition.cut == "3610s"
    assert two.structured.transition.next_clip_name == "Gap"
    assert FCPXMLValidator().validate_file(second).valid
    for resources in _dtd_dirs():
        validate_fcpxml(second, dtd_path=resources / "FCPXMLv1_11.dtd")


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"after_clip_name": "Retimed"}, ErrorCode.UNSUPPORTED_CONTRACT),  # last clip
        ({"after_clip_name": "Nope"}, ErrorCode.TARGET_NOT_FOUND),
        ({"duration": "5s", "after_clip_name": "Gap"}, ErrorCode.INVALID_ARGUMENTS),
        ({"duration": "0s"}, ErrorCode.INVALID_ARGUMENTS),
        ({"effect_uid": ""}, ErrorCode.INVALID_ARGUMENTS),
    ],
)
def test_transition_template_failures_commit_nothing(timeline, tmp_path, kwargs, code):
    out = tmp_path / "never.fcpxml"
    arguments = {
        "path": str(timeline),
        "after_clip_name": "Talk",
        "effect_name": "Swipe",
        "effect_uid": ".../Transitions.localized/X.localized/X.motr",
        "output_path": str(out),
        **kwargs,
    }
    with pytest.raises(FCPMCPError) as caught:
        server.fcpxml_add_transition_template(**arguments)
    assert caught.value.code is code
    assert not out.exists()


def test_transition_template_refuses_double_transition(timeline, tmp_path):
    first = tmp_path / "t1.fcpxml"
    server.fcpxml_add_transition_template(
        str(timeline), "Talk", "X", ".../Transitions.localized/X.localized/X.motr",
        output_path=str(first),
    )
    with pytest.raises(FCPMCPError) as caught:
        server.fcpxml_add_transition_template(
            str(first), "Talk", "X", ".../Transitions.localized/X.localized/X.motr",
            output_path=str(tmp_path / "never.fcpxml"),
        )
    assert caught.value.code is ErrorCode.UNSUPPORTED_CONTRACT


def test_connected_title_with_text_reports_text_element(timeline, tmp_path):
    out = tmp_path / "plain.fcpxml"
    outcome = server.fcpxml_add_connected_title(
        str(timeline), "Plain", "3601s", "1s", output_path=str(out)
    )
    assert outcome.structured.element.has_text_element is True


# --- installed Final Cut Pro (skipped when absent) ---------------------------


@pytest.mark.skipif(
    library.builtin_template_roots() == [], reason="Final Cut Pro is not installed"
)
def test_installed_basic_title_matches_exported_uid_and_known_keys():
    exported = ET.parse(FIXTURES / "basic_title_export.fcpxml").getroot()
    assert exported.find("resources/effect").get("uid") == BASIC_TITLE_UID
    resolved = library.resolve_template(BASIC_TITLE_UID, library.builtin_template_roots())
    assert resolved is not None
    result = server.fcp_inspect_motion_template(BASIC_TITLE_UID).structured
    assert result.template.uid == BASIC_TITLE_UID
    # Final Cut stores Basic Title parameter keys under this object path.
    assert [layer.object_key for layer in result.text_layers] == ["9999/999166631/999166633"]
