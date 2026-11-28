"""Tests for FCPXML parser."""

from pathlib import Path

import pytest

from fcp_mcp.fcpxml.models import (
    ClipType,
    FCPXMLDocument,
    MarkerType,
    TimecodeFormat,
)
from fcp_mcp.fcpxml.parser import FCPXMLParser
from fcp_mcp.fcpxml.time_utils import RationalTime


class TestParserBasic:
    def test_parse_version(self, sample_doc: FCPXMLDocument):
        assert sample_doc.version == "1.11"

    def test_parse_formats(self, sample_doc: FCPXMLDocument):
        assert "r1" in sample_doc.formats
        fmt = sample_doc.formats["r1"]
        assert fmt.name == "FFVideoFormat1080p2997"
        assert fmt.width == 1920
        assert fmt.height == 1080
        assert fmt.frame_duration == RationalTime(1001, 30000)

    def test_parse_assets(self, sample_doc: FCPXMLDocument):
        assert len(sample_doc.assets) == 4
        interview = sample_doc.assets["r2"]
        assert interview.name == "Interview_A"
        assert interview.has_video is True
        assert interview.has_audio is True

        music = sample_doc.assets["r5"]
        assert music.name == "Music_Track"
        assert music.has_video is False
        assert music.has_audio is True

    def test_parse_effects(self, sample_doc: FCPXMLDocument):
        assert "r6" in sample_doc.effects
        assert sample_doc.effects["r6"].name == "Basic Title"
        assert "r7" in sample_doc.effects
        assert sample_doc.effects["r7"].name == "Cross Dissolve"

    def test_parse_transform_preserves_xy_scale(self, tmp_path: Path):
        path = tmp_path / "xy-scale.fcpxml"
        path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<fcpxml version="1.11">
  <resources>
    <format id="r1" frameDuration="1/30s" width="1920" height="1080"/>
    <asset id="r2" name="Clip" duration="1s"/>
  </resources>
  <event name="Event">
    <project name="Project">
      <sequence format="r1" duration="1s">
        <spine>
          <asset-clip ref="r2" name="Clip" duration="1s">
            <adjust-transform scale="1.25 0.75"/>
          </asset-clip>
        </spine>
      </sequence>
    </project>
  </event>
</fcpxml>
""",
            encoding="utf-8",
        )

        transform = (
            FCPXMLParser()
            .parse(path)
            .all_projects[0]
            .sequence.spine.clips[0]
            .transform
        )

        assert transform is not None
        assert transform.scale == 1.25
        assert transform.scale_y == 0.75


class TestParserLibrary:
    def test_library_exists(self, sample_doc: FCPXMLDocument):
        assert sample_doc.library is not None
        assert "MyLibrary" in sample_doc.library.location

    def test_event(self, sample_doc: FCPXMLDocument):
        events = sample_doc.library.events
        assert len(events) == 1
        assert events[0].name == "Travel Video"

    def test_project(self, sample_doc: FCPXMLDocument):
        projects = sample_doc.all_projects
        assert len(projects) == 1
        assert projects[0].name == "Travel Vlog v1"

    def test_sequence(self, sample_doc: FCPXMLDocument):
        seq = sample_doc.all_projects[0].sequence
        assert seq is not None
        assert seq.tc_format == TimecodeFormat.NON_DROP_FRAME
        assert seq.format_ref == "r1"


class TestParserClips:
    def test_clip_count(self, sample_doc: FCPXMLDocument):
        spine = sample_doc.all_projects[0].sequence.spine
        # 6 clips in spine: interview, beach, gap, city, flash, outro
        # (transition is not counted as a clip currently)
        assert spine.clip_count == 6

    def test_first_clip(self, sample_doc: FCPXMLDocument):
        clip = sample_doc.all_projects[0].sequence.spine.clips[0]
        assert clip.clip_type == ClipType.ASSET_CLIP
        assert clip.name == "Interview_A"
        assert clip.ref == "r2"
        assert clip.role == "Dialogue"
        assert clip.offset == RationalTime(0, 1)
        assert clip.duration == RationalTime(150150, 30000)

    def test_gap_clip(self, sample_doc: FCPXMLDocument):
        gap = sample_doc.all_projects[0].sequence.spine.clips[2]
        assert gap.is_gap is True
        assert gap.clip_type == ClipType.GAP
        assert gap.duration == RationalTime(3003, 30000)

    def test_non_gap_clips(self, sample_doc: FCPXMLDocument):
        spine = sample_doc.all_projects[0].sequence.spine
        non_gaps = spine.non_gap_clips
        assert len(non_gaps) == 5  # all except the gap

    def test_clip_roles(self, sample_doc: FCPXMLDocument):
        clips = sample_doc.all_projects[0].sequence.spine.clips
        assert clips[0].role == "Dialogue"  # Interview
        assert clips[1].role == "Video"     # Beach
        assert clips[3].role == "Video"     # City


class TestParserMarkers:
    def test_markers_on_clip(self, sample_doc: FCPXMLDocument):
        clip = sample_doc.all_projects[0].sequence.spine.clips[0]  # Interview
        assert len(clip.markers) == 1
        marker = clip.markers[0]
        assert marker.value == "Good take"
        assert marker.note == "Use this section"
        assert marker.marker_type == MarkerType.STANDARD

    def test_chapter_marker(self, sample_doc: FCPXMLDocument):
        clip = sample_doc.all_projects[0].sequence.spine.clips[3]  # City
        assert len(clip.markers) == 1
        assert clip.markers[0].marker_type == MarkerType.CHAPTER
        assert clip.markers[0].value == "City Section"


class TestParserKeywords:
    def test_keywords(self, sample_doc: FCPXMLDocument):
        clip = sample_doc.all_projects[0].sequence.spine.clips[0]  # Interview
        assert len(clip.keywords) == 1
        assert clip.keywords[0].value == "intro"

    def test_outro_keyword(self, sample_doc: FCPXMLDocument):
        clip = sample_doc.all_projects[0].sequence.spine.clips[5]  # Outro
        assert len(clip.keywords) == 1
        assert clip.keywords[0].value == "outro"


class TestParserConnectedClips:
    def test_connected_title(self, sample_doc: FCPXMLDocument):
        beach = sample_doc.all_projects[0].sequence.spine.clips[1]  # Beach
        assert len(beach.connected_clips) == 1
        title = beach.connected_clips[0]
        assert title.clip_type == ClipType.TITLE
        assert title.name == "Beach Scene"
        assert title.ref == "r6"


class TestParserEdgeCases:
    def test_flash_frame_detected(self, sample_doc: FCPXMLDocument):
        """The sample has a 2-frame clip that should be parseable."""
        flash = sample_doc.all_projects[0].sequence.spine.clips[4]
        assert flash.name == "Broll_Beach_Flash"
        # 2002/30000s = ~0.067s = 2 frames at 29.97
        assert flash.duration == RationalTime(2002, 30000)
        assert flash.duration_seconds < 0.1

    def test_file_not_found(self, parser: FCPXMLParser):
        with pytest.raises(FileNotFoundError):
            parser.parse("/nonexistent/file.fcpxml")


class TestAssetMediaRepresentation:
    """FCPXML 1.9+ stores asset URLs on media-rep children."""

    @staticmethod
    def _parse(tmp_path: Path, asset_body: str, asset_attrs: str = "") -> FCPXMLDocument:
        path = tmp_path / "media-rep.fcpxml"
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
            '<fcpxml version="1.11"><resources>'
            '<format id="r1" frameDuration="1/30s" width="1920" height="1080"/>'
            f'<asset id="r2" name="Talk" start="0s" duration="5s" format="r1"{asset_attrs}>'
            f"{asset_body}</asset></resources></fcpxml>",
            encoding="utf-8",
        )
        return FCPXMLParser().parse(path)

    def test_original_media_rep_supplies_source(self, tmp_path: Path):
        doc = self._parse(
            tmp_path,
            '<media-rep kind="proxy-media" src="file:///proxy.mov"/>'
            '<media-rep kind="original-media" src="file:///talk.mov"/>',
        )
        assert doc.assets["r2"].src == "file:///talk.mov"

    def test_falls_back_to_first_media_rep(self, tmp_path: Path):
        doc = self._parse(tmp_path, '<media-rep kind="proxy-media" src="file:///proxy.mov"/>')
        assert doc.assets["r2"].src == "file:///proxy.mov"

    def test_src_attribute_wins_when_present(self, tmp_path: Path):
        doc = self._parse(
            tmp_path,
            '<media-rep kind="original-media" src="file:///rep.mov"/>',
            ' src="file:///legacy.mov"',
        )
        assert doc.assets["r2"].src == "file:///legacy.mov"

    def test_validator_no_longer_warns_for_media_rep_assets(self, tmp_path: Path):
        from fcp_mcp.fcpxml.validator import FCPXMLValidator

        doc = self._parse(tmp_path, '<media-rep kind="original-media" src="file:///talk.mov"/>')
        messages = [issue.message for issue in FCPXMLValidator().validate_document(doc).issues]
        assert not any("no source path" in message for message in messages)


class TestMediaRepThroughTools:
    """Tool-level effects of reading media-rep sources."""

    @staticmethod
    def _write(path: Path, media: str) -> None:
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
            '<fcpxml version="1.11"><resources>'
            '<format id="r1" frameDuration="1/30s" width="1920" height="1080"/>'
            '<asset id="r2" name="Fala" start="0s" duration="5s" hasVideo="1" format="r1">'
            f'<media-rep kind="original-media" src="{media}"/></asset></resources>'
            '<library><event name="E"><project name="P">'
            '<sequence format="r1" duration="5s" tcStart="0s"><spine>'
            '<asset-clip ref="r2" name="Fala" offset="0s" duration="5s"/>'
            "</spine></sequence></project></event></library></fcpxml>",
            encoding="utf-8",
        )

    @pytest.fixture
    def policy(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from fcp_mcp import server
        from fcp_mcp.config import RuntimeConfig
        from fcp_mcp.security.paths import PathPolicy

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

    def test_qc_report_does_not_warn_about_missing_source(self, tmp_path: Path, policy):
        from fcp_mcp import server

        media = tmp_path / "fala.mov"
        media.write_bytes(b"x")
        project = tmp_path / "p.fcpxml"
        self._write(project, media.as_uri())

        report = server.fcpxml_qc_report(str(project))

        assert "no source path" not in report.text

    def test_media_link_check_sees_media_rep_paths(self, tmp_path: Path, policy):
        from fcp_mcp import server

        project = tmp_path / "p.fcpxml"
        self._write(project, (tmp_path / "missing.mov").as_uri())

        result = server.fcpxml_check_media_links(str(project))

        assert result.structured.missing_count == 1

    def test_media_rep_outside_allowed_roots_is_refused(self, tmp_path: Path, policy):
        from fcp_mcp import server
        from fcp_mcp.contracts import ErrorCode, FCPMCPError

        project = tmp_path / "p.fcpxml"
        self._write(project, "file:///private/var/elsewhere/fala.mov")

        with pytest.raises(FCPMCPError) as caught:
            server.fcpxml_check_media_links(str(project))
        assert caught.value.code is ErrorCode.PATH_OUTSIDE_SCOPE
