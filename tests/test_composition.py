"""Tests for the composition tools: transforms, video effects, connected
titles/generators, and transform keyframes."""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from fcp_mcp import server
from fcp_mcp.config import RuntimeConfig
from fcp_mcp.contracts import ErrorCode, FCPMCPError
from fcp_mcp.fcpxml import composition
from fcp_mcp.fcpxml.validator import FCPXMLValidator
from fcp_mcp.security.paths import PathPolicy
from scripts.apple_dtd_gate import validate_fcpxml

FIXTURE = Path(__file__).parent / "fixtures" / "vertical_composition.fcpxml"
BASIC_TITLE_UID = (
    ".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"
)
# Example uids only; the tools treat uids as opaque strings.
EXAMPLE_TEMPLATE_UID = (
    "~/Effects.localized/Example/Example.moef"
)
EXAMPLE_FXPLUG_UID = "FxPlug:00000000-0000-4000-8000-000000000000"
EXAMPLE_GENERATOR_UID = (
    ".../Generators.localized/Solids.localized/Custom.localized/Custom.motn"
)


def _installed_dtd_dirs() -> list[Path]:
    return sorted(
        Path("/Applications").glob(
            "Final Cut Pro*.app/Contents/Frameworks/Interchange.framework/"
            "Versions/A/Resources"
        )
    )


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


@pytest.fixture
def source(tmp_path: Path, policy: None) -> Path:
    path = tmp_path / "vertical.fcpxml"
    shutil.copy2(FIXTURE, path)
    return path


def _spine(path: Path) -> list[ET.Element]:
    return list(ET.parse(path).getroot().find(".//project/sequence/spine"))


def _assert_valid(path: Path) -> None:
    assert FCPXMLValidator().validate_file(path).valid
    for resources in _installed_dtd_dirs():
        validate_fcpxml(path, dtd_path=resources / "FCPXMLv1_11.dtd")


# --- fcpxml_set_clip_transform ---------------------------------------------


def test_set_transform_inserts_in_dtd_order_and_reports_values(source, tmp_path):
    out = tmp_path / "t.fcpxml"
    original = source.read_bytes()

    outcome = server.fcpxml_set_clip_transform(
        str(source), "Talk", position_y=-25, scale_x=1.580247, scale_y=1.580247,
        output_path=str(out),
    )

    assert source.read_bytes() == original
    clip = _spine(out)[0]
    assert [child.tag for child in clip] == ["adjust-transform", "adjust-volume", "marker"]
    assert clip.find("adjust-transform").attrib == {
        "position": "0 -25",
        "scale": "1.580247 1.580247",
        "rotation": "0",
        "anchor": "0 0",
    }
    record = outcome.structured.transform
    assert (record.element_type, record.occurrence, record.replaced) == ("asset-clip", 1, False)
    assert record.position == "0 -25"
    _assert_valid(out)


def test_set_transform_targets_occurrence_and_replaces(source, tmp_path):
    first = tmp_path / "a.fcpxml"
    second = tmp_path / "b.fcpxml"
    third = tmp_path / "c.fcpxml"
    server.fcpxml_set_clip_transform(
        str(source), "Talk", position_x=10, occurrence=2, output_path=str(first)
    )
    server.fcpxml_add_transform_keyframes(
        str(first), "Talk", "rotation",
        '[{"time": "30s", "value": 0}, {"time": "31s", "value": 45}]',
        occurrence=2, output_path=str(second),
    )
    outcome = server.fcpxml_set_clip_transform(
        str(second), "Talk", rotation=12.5, occurrence=2, output_path=str(third)
    )

    spine = _spine(third)
    assert spine[0].find("adjust-transform") is None
    transforms = spine[2].findall("adjust-transform")
    assert len(transforms) == 1
    assert transforms[0].get("rotation") == "12.5"
    assert transforms[0].get("position") == "0 0"
    assert transforms[0].findall("param") == []
    # adjust-transform precedes the existing filter-video
    assert [child.tag for child in spine[2]] == ["adjust-transform", "filter-video"]
    assert outcome.structured.transform.replaced is True
    assert outcome.structured.transform.discarded_animated_params == ["rotation"]
    _assert_valid(third)


@pytest.mark.parametrize(
    ("name", "occurrence", "code"),
    [
        ("Missing", 1, ErrorCode.TARGET_NOT_FOUND),
        ("Talk", 3, ErrorCode.TARGET_NOT_FOUND),
        ("Talk", 0, ErrorCode.INVALID_ARGUMENTS),
        ("Gap", 1, ErrorCode.UNSUPPORTED_CONTRACT),
    ],
)
def test_set_transform_failures_commit_nothing(source, tmp_path, name, occurrence, code):
    out = tmp_path / "never.fcpxml"
    with pytest.raises(FCPMCPError) as caught:
        server.fcpxml_set_clip_transform(
            str(source), name, occurrence=occurrence, output_path=str(out)
        )
    assert caught.value.code is code
    assert not out.exists()


def test_set_transform_rejects_non_finite(source, tmp_path):
    with pytest.raises(FCPMCPError) as caught:
        server.fcpxml_set_clip_transform(
            str(source), "Talk", scale_x=float("nan"), output_path=str(tmp_path / "x.fcpxml")
        )
    assert caught.value.code is ErrorCode.INVALID_ARGUMENTS


def test_mc_clip_is_not_transformable():
    root = ET.fromstring(
        '<fcpxml><library><event><project><sequence><spine>'
        '<mc-clip ref="r1" name="Multi" duration="1s"/>'
        '</spine></sequence></project></event></library></fcpxml>'
    )
    with pytest.raises(FCPMCPError) as caught:
        composition.find_named_clip(root, "Multi")
    assert caught.value.code is ErrorCode.UNSUPPORTED_CONTRACT


# --- fcpxml_add_video_effect -----------------------------------------------


def test_video_effect_dedupes_resource_by_uid_and_orders_filters(source, tmp_path):
    first = tmp_path / "e1.fcpxml"
    second = tmp_path / "e2.fcpxml"

    one = server.fcpxml_add_video_effect(
        str(source), "Talk", "Example Look", EXAMPLE_TEMPLATE_UID,
        params_json='{"9999/1/2": "0.5", "Amount": 3}', output_path=str(first),
    )
    two = server.fcpxml_add_video_effect(
        str(first), "Talk", "Example Look", EXAMPLE_TEMPLATE_UID,
        occurrence=2, output_path=str(second),
    )

    root = ET.parse(second).getroot()
    effects = root.findall("resources/effect")
    assert [effect.get("uid") for effect in effects].count(EXAMPLE_TEMPLATE_UID) == 1
    assert one.structured.effect.resource_created is True
    assert two.structured.effect.resource_created is False
    assert one.structured.effect.effect_ref == two.structured.effect.effect_ref
    ids = [element.get("id") for element in root.iter() if element.get("id")]
    assert len(ids) == len(set(ids))

    spine = _spine(second)
    first_clip = spine[0]
    assert [child.tag for child in first_clip] == ["adjust-volume", "marker", "filter-video"]
    params = first_clip.find("filter-video").findall("param")
    assert [(p.get("name"), p.get("key"), p.get("value")) for p in params] == [
        ("9999/1/2", "9999/1/2", "0.5"),
        ("Amount", "Amount", "3"),
    ]
    second_clip = spine[2]
    assert [f.get("name") for f in second_clip.findall("filter-video")] == [
        "Gaussian Blur",
        "Example Look",
    ]
    assert two.structured.effect.filter_count == 2
    _assert_valid(second)


def test_video_effect_accepts_fxplug_style_uid_verbatim(source, tmp_path):
    out = tmp_path / "fx.fcpxml"
    server.fcpxml_add_video_effect(
        str(source), "Talk", "Example FxPlug", EXAMPLE_FXPLUG_UID, output_path=str(out)
    )
    effect = next(
        e for e in ET.parse(out).getroot().findall("resources/effect")
        if e.get("name") == "Example FxPlug"
    )
    assert effect.get("uid") == EXAMPLE_FXPLUG_UID
    _assert_valid(out)


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"effect_uid": "  "}, ErrorCode.INVALID_ARGUMENTS),
        ({"params_json": "[]"}, ErrorCode.INVALID_ARGUMENTS),
        ({"params_json": '{"k": {"nested": 1}}'}, ErrorCode.INVALID_ARGUMENTS),
        ({"clip_name": "Gap"}, ErrorCode.UNSUPPORTED_CONTRACT),
        ({"clip_name": "Nope"}, ErrorCode.TARGET_NOT_FOUND),
    ],
)
def test_video_effect_failures_commit_nothing(source, tmp_path, kwargs, code):
    out = tmp_path / "never.fcpxml"
    arguments = {
        "path": str(source),
        "clip_name": "Talk",
        "effect_name": "X",
        "effect_uid": "FFGaussianBlur",
        "output_path": str(out),
        **kwargs,
    }
    with pytest.raises(FCPMCPError) as caught:
        server.fcpxml_add_video_effect(**arguments)
    assert caught.value.code is code
    assert not out.exists()


# --- connected placement -----------------------------------------------------


@pytest.mark.parametrize(
    ("timeline_offset", "parent_index", "expected_offset"),
    [
        # parent offset 3600s, start 10s: 10 + (3602 - 3600)
        ("3602s", 0, "12s"),
        ("3600s", 0, "10s"),
        # gap at 3610s with start 3600s: 3600 + (3611 - 3610)
        ("3611s", 1, "3601s"),
        # second Talk, offset 3612s start 30s: 30 + (3615 - 3612)
        ("3615s", 2, "33s"),
        # boundary belongs to the item that starts there
        ("3612s", 2, "30s"),
    ],
)
def test_connected_title_offset_is_parent_local(
    source, tmp_path, timeline_offset, parent_index, expected_offset
):
    out = tmp_path / "title.fcpxml"
    original_sequence = ET.parse(source).getroot().find(".//sequence").attrib

    outcome = server.fcpxml_add_connected_title(
        str(source), "HELLO", timeline_offset, "2s", lane=2, output_path=str(out)
    )

    spine = _spine(out)
    titles = spine[parent_index].findall("title")
    assert len(titles) == 1
    title = titles[0]
    assert title.get("offset") == expected_offset
    assert title.get("lane") == "2"
    assert outcome.structured.element.offset == expected_offset
    assert outcome.structured.element.timeline_offset == timeline_offset
    assert ET.parse(out).getroot().find(".//sequence").attrib == original_sequence
    _assert_valid(out)


def test_connected_title_structure_resources_and_styles(source, tmp_path):
    first = tmp_path / "t1.fcpxml"
    second = tmp_path / "t2.fcpxml"
    one = server.fcpxml_add_connected_title(
        str(source), "Top", "3601s", "3s", lane=1, position_y=25, font_size=90,
        params_json='{"9999/10003/1/100/101": "1"}', output_path=str(first),
    )
    two = server.fcpxml_add_connected_title(
        str(first), "Second", "3601s", "3s", lane=3, anchor_clip_name="Talk",
        alignment="left", output_path=str(second),
    )

    root = ET.parse(second).getroot()
    basic = [e for e in root.findall("resources/effect") if e.get("uid") == BASIC_TITLE_UID]
    assert len(basic) == 1
    assert one.structured.element.resource_created is True
    assert two.structured.element.resource_created is False
    ids = [element.get("id") for element in root.iter() if element.get("id")]
    assert len(ids) == len(set(ids))

    clip = _spine(second)[0]
    assert [child.tag for child in clip] == [
        "adjust-volume", "title", "title", "marker",
    ]
    top = clip.findall("title")[0]
    assert [child.tag for child in top] == [
        "param", "text", "text-style-def", "adjust-transform",
    ]
    assert top.find("adjust-transform").get("position") == "0 25"
    style = top.find("text-style-def/text-style")
    assert style.get("fontSize") == "90"
    assert style.get("fontFace") == "Bold"
    assert top.find("text/text-style").get("ref") == top.find("text-style-def").get("id")
    second_title = clip.findall("title")[1]
    assert second_title.find("adjust-transform") is None
    assert second_title.find("text-style-def/text-style").get("alignment") == "left"
    assert one.structured.element.position == "0 25"
    assert two.structured.element.position is None
    _assert_valid(second)


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"timeline_offset": "3599s"}, ErrorCode.TARGET_NOT_FOUND),
        ({"timeline_offset": "3626s"}, ErrorCode.TARGET_NOT_FOUND),
        ({"timeline_offset": "3623s"}, ErrorCode.UNSUPPORTED_CONTRACT),  # timeMap parent
        ({"timeline_offset": "3615s", "anchor_clip_name": "Talk"}, ErrorCode.INVALID_ARGUMENTS),
        ({"anchor_clip_name": "Nope"}, ErrorCode.TARGET_NOT_FOUND),
        ({"lane": 0}, ErrorCode.INVALID_ARGUMENTS),
        ({"duration": "0s"}, ErrorCode.INVALID_ARGUMENTS),
        ({"timeline_offset": "1.5s"}, ErrorCode.INVALID_ARGUMENTS),
        ({"alignment": "middle"}, ErrorCode.INVALID_ARGUMENTS),
        ({"font_size": -1}, ErrorCode.INVALID_ARGUMENTS),
    ],
)
def test_connected_title_failures_commit_nothing(source, tmp_path, kwargs, code):
    out = tmp_path / "never.fcpxml"
    arguments = {
        "path": str(source),
        "text": "X",
        "timeline_offset": "3601s",
        "duration": "1s",
        "output_path": str(out),
        **kwargs,
    }
    with pytest.raises(FCPMCPError) as caught:
        server.fcpxml_add_connected_title(**arguments)
    assert caught.value.code is code
    assert not out.exists()


def test_connected_title_rejects_anchor_off_primary_storyline(source, tmp_path):
    first = tmp_path / "t1.fcpxml"
    server.fcpxml_add_connected_title(
        str(source), "Nested", "3601s", "1s", output_path=str(first)
    )
    with pytest.raises(FCPMCPError) as caught:
        server.fcpxml_add_connected_title(
            str(first), "X", "3601s", "1s", anchor_clip_name="Nested",
            output_path=str(tmp_path / "never.fcpxml"),
        )
    assert caught.value.code is ErrorCode.UNSUPPORTED_CONTRACT


def test_connected_generator_emits_video_with_params_and_transform(source, tmp_path):
    out = tmp_path / "g.fcpxml"
    outcome = server.fcpxml_add_connected_generator(
        str(source), "Backdrop", EXAMPLE_GENERATOR_UID, "3614s", "6s", lane=-1,
        params_json='{"Color": "0 0 0 1"}', position_y=25, scale_x=2, scale_y=2,
        role="video", output_path=str(out),
    )

    clip = _spine(out)[2]
    video = clip.find("video")
    assert video is not None
    assert video.get("offset") == "32s"
    assert video.get("lane") == "-1"
    assert video.get("role") == "video"
    assert [child.tag for child in video] == ["param", "adjust-transform"]
    assert video.find("adjust-transform").get("scale") == "2 2"
    # anchored items sit before the existing filter-video
    assert [child.tag for child in clip] == ["video", "filter-video"]
    effect = next(
        e for e in ET.parse(out).getroot().findall("resources/effect")
        if e.get("id") == video.get("ref")
    )
    assert effect.get("uid") == EXAMPLE_GENERATOR_UID
    record = outcome.structured.element
    assert (record.kind, record.element_type, record.parent_start) == ("generator", "video", "30s")
    assert record.parameters[0].model_dump() == {
        "name": "Color", "key": "Color", "value": "0 0 0 1",
    }
    _assert_valid(out)


def test_connected_generator_rejects_empty_uid(source, tmp_path):
    with pytest.raises(FCPMCPError) as caught:
        server.fcpxml_add_connected_generator(
            str(source), "X", "", "3601s", "1s", output_path=str(tmp_path / "n.fcpxml")
        )
    assert caught.value.code is ErrorCode.INVALID_ARGUMENTS


# --- fcpxml_add_transform_keyframes ----------------------------------------


def test_keyframes_create_transform_and_use_keyframe_animation(source, tmp_path):
    out = tmp_path / "k.fcpxml"
    outcome = server.fcpxml_add_transform_keyframes(
        str(source), "Talk", "position",
        json.dumps([
            {"time": "10s", "value": "0 -60", "interp": "easeOut"},
            {"time": "21/2s", "value": [0, -25], "interp": "linear", "curve": "linear"},
        ]),
        output_path=str(out),
    )

    clip = _spine(out)[0]
    transform = clip.find("adjust-transform")
    assert clip[0].tag == "adjust-transform"
    assert transform.attrib == {
        "position": "0 0", "scale": "1 1", "rotation": "0", "anchor": "0 0",
    }
    keyframes = transform.findall("param[@name='position']/keyframeAnimation/keyframe")
    assert [k.attrib for k in keyframes] == [
        {"time": "10s", "value": "0 -60", "interp": "easeOut"},
        {"time": "21/2s", "value": "0 -25", "interp": "linear", "curve": "linear"},
    ]
    record = outcome.structured.animation
    assert (record.transform_created, record.replaced) == (True, False)
    assert (record.clip_start, record.clip_duration) == ("10s", "10s")
    _assert_valid(out)


def test_keyframes_replace_same_param_and_keep_others(source, tmp_path):
    a, b, c = (tmp_path / f"{n}.fcpxml" for n in "abc")
    server.fcpxml_set_clip_transform(str(source), "Talk", position_y=-25, output_path=str(a))
    server.fcpxml_add_transform_keyframes(
        str(a), "Talk", "scale", '[{"time": "10s", "value": "1 1"}]', output_path=str(b)
    )
    outcome = server.fcpxml_add_transform_keyframes(
        str(b), "Talk", "scale",
        '[{"time": "10s", "value": "0.5 0.5"}, {"time": "20s", "value": "1 1", "interp": "ease"}]',
        output_path=str(c),
    )
    transform = _spine(c)[0].find("adjust-transform")
    assert transform.get("position") == "0 -25"
    params = transform.findall("param")
    assert [p.get("name") for p in params] == ["scale"]
    assert len(params[0].findall("keyframeAnimation/keyframe")) == 2
    assert outcome.structured.animation.replaced is True
    assert outcome.structured.animation.transform_created is False
    _assert_valid(c)


@pytest.mark.parametrize(
    ("param", "keyframes", "code"),
    [
        ("opacity", '[{"time": "10s", "value": 1}]', ErrorCode.INVALID_ARGUMENTS),
        ("position", "[]", ErrorCode.INVALID_ARGUMENTS),
        ("position", '{"time": "10s"}', ErrorCode.INVALID_ARGUMENTS),
        # clip-local range is 10s..20s; 0s is timeline-style and rejected
        ("position", '[{"time": "0s", "value": "0 0"}]', ErrorCode.INVALID_ARGUMENTS),
        ("position", '[{"time": "11s", "value": "0 0"}, {"time": "11s", "value": "1 1"}]',
         ErrorCode.INVALID_ARGUMENTS),
        ("position", '[{"time": "11s", "value": "0"}]', ErrorCode.INVALID_ARGUMENTS),
        ("rotation", '[{"time": "11s", "value": "a"}]', ErrorCode.INVALID_ARGUMENTS),
        ("position", '[{"time": "11s", "value": "0 0", "interp": "smooth2"}]',
         ErrorCode.INVALID_ARGUMENTS),
        ("position", '[{"time": "11s", "value": "0 0", "extra": 1}]', ErrorCode.INVALID_ARGUMENTS),
    ],
)
def test_keyframe_failures_commit_nothing(source, tmp_path, param, keyframes, code):
    out = tmp_path / "never.fcpxml"
    with pytest.raises(FCPMCPError) as caught:
        server.fcpxml_add_transform_keyframes(
            str(source), "Talk", param, keyframes, output_path=str(out)
        )
    assert caught.value.code is code
    assert not out.exists()


def test_full_vertical_split_recipe_is_dtd_valid(source, tmp_path):
    steps = [tmp_path / f"step{n}.fcpxml" for n in range(6)]
    server.fcpxml_set_clip_transform(
        str(source), "Talk", position_y=-25, scale_x=1.580247, scale_y=1.580247,
        output_path=str(steps[0]),
    )
    server.fcpxml_add_connected_generator(
        str(steps[0]), "Backdrop", EXAMPLE_GENERATOR_UID, "3600s", "10s", lane=1,
        position_y=25, output_path=str(steps[1]),
    )
    server.fcpxml_add_connected_title(
        str(steps[1]), "HOOK", "3600s", "3s", lane=2, position_y=25, output_path=str(steps[2]),
    )
    server.fcpxml_add_video_effect(
        str(steps[2]), "Talk", "Example Look", EXAMPLE_TEMPLATE_UID, output_path=str(steps[3]),
    )
    server.fcpxml_add_transform_keyframes(
        str(steps[3]), "HOOK", "scale",
        '[{"time": "0s", "value": "0 0"}, {"time": "1/2s", "value": "1 1", "interp": "ease"}]',
        output_path=str(steps[4]),
    )
    server.fcpxml_add_transform_keyframes(
        str(steps[4]), "Talk", "position",
        '[{"time": "10s", "value": "0 -60"}, {"time": "21/2s", "value": "0 -25"}]',
        output_path=str(steps[5]),
    )
    final = steps[5]
    clip = _spine(final)[0]
    assert [child.tag for child in clip] == [
        "adjust-transform", "adjust-volume", "video", "title", "marker", "filter-video",
    ]
    _assert_valid(final)


@pytest.mark.skipif(not _installed_dtd_dirs(), reason="Final Cut Pro DTDs not installed")
@pytest.mark.parametrize("version", ["1.11", "1.13", "1.14", "1.15"])
def test_outputs_validate_against_installed_apple_dtds(tmp_path, policy, version):
    source = tmp_path / f"v{version}.fcpxml"
    source.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(
            'fcpxml version="1.11"', f'fcpxml version="{version}"'
        ),
        encoding="utf-8",
    )
    out = tmp_path / f"v{version}-out.fcpxml"
    mid = tmp_path / f"v{version}-mid.fcpxml"
    server.fcpxml_add_connected_title(
        str(source), "T", "3601s", "2s", position_y=10, output_path=str(mid)
    )
    server.fcpxml_add_transform_keyframes(
        str(mid), "T", "position",
        '[{"time": "1s", "value": "0 10"}, {"time": "2s", "value": "0 20"}]',
        output_path=str(out),
    )
    dtd_name = f"FCPXMLv{version.replace('.', '_')}.dtd"
    for resources in _installed_dtd_dirs():
        if (resources / dtd_name).is_file():
            validate_fcpxml(out, dtd_path=resources / dtd_name)


# --- helpers -----------------------------------------------------------------


def test_format_number_is_compact_and_precise():
    assert composition.format_number(-25.0) == "-25"
    assert composition.format_number(1.851852) == "1.851852"
    assert composition.format_number(-0.0) == "0"
    assert composition.format_number(1e-9) == "0"


def test_insertions_respect_later_ordered_siblings(tmp_path, policy):
    source = tmp_path / "ordered.fcpxml"
    source.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(
            '<adjust-volume amount="-3dB"/>',
            '<adjust-blend amount="0.8"/><adjust-volume amount="-3dB"/>',
        ).replace(
            '<marker start="12s" duration="1/30s" value="Hook"/>',
            '<marker start="12s" duration="1/30s" value="Hook"/>'
            '<filter-audio ref="r5" name="Channel EQ"/>'
            '<metadata><md key="com.apple.proapps.studio.reel" value="A"/></metadata>',
        ).replace(
            '<effect id="r4" name="Gaussian Blur" uid="FFGaussianBlur"/>',
            '<effect id="r4" name="Gaussian Blur" uid="FFGaussianBlur"/>'
            '<effect id="r5" name="Channel EQ" uid="FFAudioUnitEffect"/>',
        ),
        encoding="utf-8",
    )
    _assert_valid(source)
    steps = [tmp_path / f"o{n}.fcpxml" for n in range(3)]
    server.fcpxml_set_clip_transform(str(source), "Talk", position_y=-25, output_path=str(steps[0]))
    server.fcpxml_add_connected_title(str(steps[0]), "T", "3601s", "1s", output_path=str(steps[1]))
    server.fcpxml_add_video_effect(
        str(steps[1]), "Talk", "Blur", "FFGaussianBlur", output_path=str(steps[2])
    )
    clip = _spine(steps[2])[0]
    assert [child.tag for child in clip] == [
        "adjust-transform", "adjust-blend", "adjust-volume", "title", "marker",
        "filter-video", "filter-audio", "metadata",
    ]
    _assert_valid(steps[2])


def test_parent_of_returns_direct_parent_or_none() -> None:
    from xml.etree import ElementTree

    from fcp_mcp.fcpxml.composition import parent_of

    root = ElementTree.fromstring("<a><b><c/></b><d/></a>")
    b = root.find("b")
    c = root.find("b/c")
    assert b is not None and c is not None
    assert parent_of(root, c) is b
    assert parent_of(root, b) is root
    assert parent_of(root, root) is None
    assert parent_of(root, ElementTree.Element("x")) is None
