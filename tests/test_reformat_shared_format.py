"""fcpxml_reformat must not re-describe source media when the sequence format is shared."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from fcp_mcp import server
from fcp_mcp.config import RuntimeConfig
from fcp_mcp.security.paths import PathPolicy

SHARED_FORMAT = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.11">
    <resources>
        <format id="r1" name="FFVideoFormat1080p30" width="1920" height="1080" frameDuration="100/3000s"/>
        <asset id="r2" name="Talk" start="0s" duration="10s" hasVideo="1" hasAudio="1" format="r1">
            <media-rep kind="original-media" src="file:///tmp/talk.mov"/>
        </asset>
    </resources>
    <event name="E">
        <project name="P">
            <sequence format="r1" duration="10s" tcStart="0s" tcFormat="NDF">
                <spine>
                    <asset-clip ref="r2" name="Talk" offset="0s" start="0s" duration="10s"/>
                </spine>
            </sequence>
        </project>
    </event>
</fcpxml>
"""


@pytest.fixture
def scoped_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
@pytest.mark.usefixtures("scoped_policy")
async def test_reformat_clones_format_shared_with_assets(tmp_path: Path) -> None:
    source = tmp_path / "shared.fcpxml"
    source.write_text(SHARED_FORMAT, encoding="utf-8")
    destination = tmp_path / "vertical.fcpxml"

    result = await server.mcp.call_tool(
        "fcpxml_reformat",
        {
            "path": str(source),
            "target_width": 1080,
            "target_height": 1920,
            "target_format_name": "FFVideoFormat1080x1920p30",
            "output_path": str(destination),
        },
    )

    assert result.structured_content["destination"]["path"] == str(destination)
    root = ET.parse(destination).getroot()
    formats = {fmt.get("id"): fmt for fmt in root.iter("format")}
    asset = root.find(".//asset")
    sequence = root.find(".//sequence")
    assert asset is not None and sequence is not None
    # the source asset keeps its real 16:9 format
    assert asset.get("format") == "r1"
    assert formats["r1"].get("width") == "1920"
    assert formats["r1"].get("height") == "1080"
    # the sequence gets its own vertical format
    sequence_format = formats[sequence.get("format")]
    assert sequence.get("format") != "r1"
    assert sequence_format.get("width") == "1080"
    assert sequence_format.get("height") == "1920"
    assert sequence_format.get("name") == "FFVideoFormat1080x1920p30"
    assert sequence_format.get("frameDuration") == "100/3000s"
