"""Wire-level result contracts for the composition tools."""

from __future__ import annotations

import hashlib
import importlib
import shutil
import uuid
from pathlib import Path

import pytest

from fcp_mcp import server
from fcp_mcp.config import RuntimeConfig
from fcp_mcp.security.paths import PathPolicy

FIXTURE = Path(__file__).parents[1] / "fixtures" / "vertical_composition.fcpxml"

CASES = (
    (
        "fcpxml_set_clip_transform",
        "ClipTransformMutationResult",
        {"clip_name": "Talk", "position_y": -25.0, "scale_x": 1.5, "scale_y": 1.5},
        "Transform set on 'Talk'.",
        "transform",
    ),
    (
        "fcpxml_add_video_effect",
        "VideoEffectMutationResult",
        {
            "clip_name": "Talk",
            "effect_name": "Gaussian Blur",
            "effect_uid": "FFGaussianBlur",
            "params_json": '{"Amount": "12"}',
        },
        "Effect 'Gaussian Blur' added to 'Talk'.",
        "effect",
    ),
    (
        "fcpxml_add_connected_title",
        "ConnectedElementMutationResult",
        {"text": "HOOK", "timeline_offset": "3602s", "duration": "3s", "lane": 2},
        "Title 'HOOK' connected to 'Talk' on lane 2.",
        "element",
    ),
    (
        "fcpxml_add_connected_generator",
        "ConnectedElementMutationResult",
        {
            "generator_name": "Backdrop",
            "generator_uid": (
                ".../Generators.localized/Solids.localized/Custom.localized/Custom.motn"
            ),
            "timeline_offset": "3615s",
            "duration": "5s",
        },
        "Generator 'Backdrop' connected to 'Talk' on lane 1.",
        "element",
    ),
    (
        "fcpxml_add_transform_keyframes",
        "TransformKeyframeMutationResult",
        {
            "clip_name": "Talk",
            "param": "position",
            "keyframes_json": (
                '[{"time": "10s", "value": "0 -60"}, '
                '{"time": "21/2s", "value": "0 -25", "interp": "easeOut"}]'
            ),
        },
        "2 position keyframes set on 'Talk'.",
        "animation",
    ),
)

RESULT_MODEL_NAMES = {
    "ClipTransformRecord",
    "ClipTransformMutationResult",
    "VideoEffectRecord",
    "VideoEffectMutationResult",
    "ConnectedElementRecord",
    "ConnectedElementMutationResult",
    "TransformKeyframeRecord",
    "TransformAnimationRecord",
    "TransformKeyframeMutationResult",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def composition_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "model_name", "arguments", "text_prefix", "domain_key"),
    CASES,
    ids=[case[0] for case in CASES],
)
async def test_composition_tools_publish_typed_results_and_real_receipts(
    tool_name: str,
    model_name: str,
    arguments: dict[str, object],
    text_prefix: str,
    domain_key: str,
    tmp_path: Path,
    composition_policy: None,
) -> None:
    source = tmp_path / f"{tool_name}-source.fcpxml"
    shutil.copy2(FIXTURE, source)
    input_sha256 = _sha256(source)
    destination = tmp_path / f"{tool_name}-output.fcpxml"

    result = await server.mcp.call_tool(
        tool_name,
        {"path": str(source), **arguments, "output_path": str(destination)},
    )

    assert result.is_error is False
    assert result.content[0].text == (
        f"{text_prefix} Saved to: {destination.resolve()}"
    )
    definitions = {tool.name: tool for tool in await server.mcp.list_tools()}
    definition = definitions[tool_name]
    assert definition.output_schema["title"] == model_name
    assert set(definition.output_schema["properties"]) == {
        "schema_version", "destination", "receipt", domain_key,
    }

    models = importlib.import_module("fcp_mcp.result_models.fcpxml")
    model = getattr(models, model_name).model_validate(result.structured_content)
    payload = model.model_dump(mode="json")
    assert payload["schema_version"] == "1"
    assert payload["destination"] == {
        "path": str(destination.resolve()),
        "media_type": "application/vnd.apple.fcpxml+xml",
        "sha256": _sha256(destination),
        "size_bytes": destination.stat().st_size,
    }
    receipt = payload["receipt"]
    uuid.UUID(receipt["transaction_id"])
    assert receipt["source"] == str(source.resolve())
    assert receipt["input_sha256"] == input_sha256
    assert receipt["output_sha256"] == _sha256(destination)
    assert receipt["disposition"] == "committed"
    assert source.read_bytes() == FIXTURE.read_bytes()


def test_composition_result_models_are_frozen_and_closed() -> None:
    fcpxml = importlib.import_module("fcp_mcp.result_models.fcpxml")
    for name in RESULT_MODEL_NAMES:
        model = getattr(fcpxml, name)
        assert model.model_config["frozen"] is True, name
        assert model.model_config["extra"] == "forbid", name
        pending: list[object] = [model.model_json_schema()]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                if "additionalProperties" in node:
                    assert node["additionalProperties"] is False, name
                pending.extend(node.values())
            elif isinstance(node, list):
                pending.extend(node)
