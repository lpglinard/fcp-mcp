"""Wire-level result contracts for Motion template and transition-template tools."""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import pytest

from fcp_mcp import server
from fcp_mcp.config import RuntimeConfig
from fcp_mcp.motion import library
from fcp_mcp.security.paths import PathPolicy
from fcp_mcp.utils import paths as utility_paths

FIXTURES = Path(__file__).parents[1] / "fixtures"
RESULT_MODEL_NAMES = {
    "MotionTemplateRecord",
    "MotionTemplateListResult",
    "MotionTemplateSearchResult",
    "MotionTemplateParameterRecord",
    "MotionTemplateTextLayerRecord",
    "MotionTemplatePluginRecord",
    "MotionTemplateInspectionResult",
    "TemplateParamRecord",
    "TransitionTemplateRecord",
    "TransitionTemplateMutationResult",
}


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    user = tmp_path / "Motion Templates.localized"
    target = user / "Effects.localized" / "Pack" / "Glow" / "Glow.moef"
    target.parent.mkdir(parents=True)
    shutil.copy2(FIXTURES / "motion" / "Synthetic Glow.moef", target)
    monkeypatch.setattr(utility_paths, "motion_templates_dir", lambda: user)
    monkeypatch.setattr(library, "builtin_template_roots", list)
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
    timeline = tmp_path / "timeline.fcpxml"
    shutil.copy2(FIXTURES / "vertical_composition.fcpxml", timeline)
    return tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "model_name", "arguments"),
    [
        ("fcp_list_motion_templates", "MotionTemplateListResult", {}),
        ("fcp_search_motion_templates", "MotionTemplateSearchResult", {"query": "glow"}),
        (
            "fcp_inspect_motion_template",
            "MotionTemplateInspectionResult",
            {"template": "~/Effects.localized/Pack/Glow/Glow.moef"},
        ),
        (
            "fcpxml_add_transition_template",
            "TransitionTemplateMutationResult",
            {
                "path": "timeline.fcpxml",
                "after_clip_name": "Talk",
                "effect_name": "Glow Wipe",
                "effect_uid": "~/Transitions.localized/Pack/Glow Wipe/Glow Wipe.motr",
                "output_path": "out.fcpxml",
            },
        ),
    ],
)
async def test_tools_publish_their_typed_models(
    environment: Path, tool_name: str, model_name: str, arguments: dict[str, object]
) -> None:
    resolved = {
        key: str(environment / value) if key in {"path", "output_path"} else value
        for key, value in arguments.items()
    }
    result = await server.mcp.call_tool(tool_name, resolved)

    assert result.is_error is False
    definitions = {tool.name: tool for tool in await server.mcp.list_tools()}
    assert definitions[tool_name].output_schema["title"] == model_name
    models = importlib.import_module("fcp_mcp.result_models.fcpxml")
    model = getattr(models, model_name).model_validate(result.structured_content)
    assert model.schema_version == "1"


def test_models_are_frozen_and_closed() -> None:
    fcpxml = importlib.import_module("fcp_mcp.result_models.fcpxml")
    for name in RESULT_MODEL_NAMES:
        model = getattr(fcpxml, name)
        assert model.model_config["frozen"] is True, name
        assert model.model_config["extra"] == "forbid", name
