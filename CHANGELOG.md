# Changelog

All notable changes to fcp-mcp are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added

- Composition tools in the `edit` and `full` profiles:
  `fcpxml_set_clip_transform`, `fcpxml_add_video_effect`,
  `fcpxml_add_connected_title`, `fcpxml_add_connected_generator`, and
  `fcpxml_add_transform_keyframes`. They set static transforms, apply
  `filter-video` effects (reusing `<effect>` resources by exact uid), connect
  titles and generators at a sequence time and lane with parent-local
  offsets, and write `keyframeAnimation` transform keyframes. Every insertion
  follows Apple's DTD child order and each commit is re-read and verified.
- WORKFLOWS.md recipe 9: vertical split-screen composition.
- `fcp_search_motion_templates` (all profiles) filters installed Motion
  templates by text, category, and source, with a result cap.
- `fcp_inspect_motion_template` (all profiles) reads a template's published
  parameters (display name, FCPXML `param` key with a confidence flag, kind,
  default, value, range, menu options), text layers, embedded FxPlug plug-in UUIDs, and scene
  size/duration, without modifying the template. Only files inside a Motion
  template folder are read.
- `fcpxml_add_transition_template` (`edit`/`full`) inserts a transition from
  any Motion transition template or effect uid as
  `<transition><filter-video ref=…/>` (the DTD form), centred on the cut,
  reusing the `<effect>` resource by uid.
- `params_json` of the composition tools accepts
  `{"<key>": {"name": "…", "value": "…"}}` so parameter keys from
  `fcp_inspect_motion_template` round-trip with their display names, and
  `fcpxml_add_connected_title` with `text=""` omits `<text>` so a template's
  published text parameter can carry the text.
- WORKFLOWS.md recipe 10: inspect a Motion template and apply it with
  parameters.

### Fixed

- `fcp_list_motion_templates` now lists titles, effects, transitions and
  generators (it previously found only `.motn` files) from the user Motion
  Templates folder, including third-party folders without `.localized`, and
  from Final Cut Pro's built-in template roots. Records keep `category`,
  `name` and `path` and add `uid` (the FCPXML effect uid: `.../…` for
  built-ins, `~/…` relative to the Motion Templates folder for user
  templates), `extension`, `source`, `root`, `group`, `collection`, and
  preview image paths.
- `fcp_doctor`, the Apple DTD release gate, and Compressor discovery find
  Final Cut Pro and Compressor by bundle id (`com.apple.FinalCut`,
  `com.apple.FinalCutApp`, `com.apple.FinalCutTrial`; `com.apple.Compressor`,
  `com.apple.CompressorApp`) instead of a fixed `/Applications/... .app`
  path, so editions such as "Final Cut Pro Creator Studio.app" are detected.
  The executed Compressor binary is only taken from `/Applications`.
- The FCPXML parser reads an asset's media URL from its
  `<media-rep kind="original-media">` child (FCPXML 1.9+) when the legacy
  `src` attribute is absent, so validation and QC no longer warn that such
  assets have no source path, and media-link checks and exports see them.
- `fcpxml_reformat` now changes the primary sequence's format instead of the
  first `<format>` in the file. When that format is shared with assets, it is
  cloned for the sequence, so source media keeps its real frame size instead
  of being re-described as the target aspect ratio.

### Changed

- Profile counts are `inspect=32`, `workflow=36`, `edit=84`, and `full=105`
  tools.

## v0.3.0 — 2026-08-09 — Transactional AI piloting

### Added

- A default `workflow` profile with 34 tools, three prompts, and three
  read-only durable evidence resources.
- A fixed prepare, review, hash-bound client approval, atomic commit, cancel,
  status, and explicit recovery flow for bounded FCPXML operations.
- Private SQLite workflow state with tamper-evident events, candidate and diff
  hashes, receipts, destination locks, idempotency, and bounded diagnostics.
- Installed-wheel evidence covering all four profiles and a real
  two-operation prepare/commit/reconcile trajectory.
- A macOS release gate that validates candidates against the matching DTD
  bundled with the locally installed Final Cut Pro.
- `fcpxml_generate_from_clip_plan`, the provenance-preserving adapter for
  `youtube-mcp.materialized-clip-plan/v1` manifests. It is available only in
  the `edit` and `full` profiles.

### Changed

- Profile counts are `inspect=30`, `workflow=34`, `edit=75`, and `full=94`
  tools; `inspect`, `edit`, and `full` remain explicit alternatives, and all
  profiles retain their existing prompt and resource contracts.
- The MCP boundary now uses the stable Python SDK v2 line and reports the
  fcp-mcp package version as its wire server version.
- Client commits must repeat the exact candidate SHA-256 returned by prepare.
  This proves candidate identity but is recorded as unverified-human approval.
- Python 3.10 compatibility is preserved through `typing_extensions.Self`.
- Commit timestamps retain subsecond precision so restart validation preserves
  event ordering.
- Audio-role assignment now emits Apple-valid `audioRole` on `asset-clip`
  elements, while legacy `role` input remains readable.
- The installed-wheel smoke source now includes the required asset
  `media-rep`, and live project durations serialize as FCPXML rational times
  instead of JavaScript object strings.
- Primary-storyline trims now synchronize the owning sequence duration,
  account for nonzero sequence timecode origins, preserve unchanged rational
  spellings, and report any duration correction in the operation receipt.

### Boundaries

- No candidate XML resource, MCP Tasks, background autonomy, or generic graph
  engine is introduced.
- Selecting `full` does not bypass the independent live-control opt-in.
- `fcpxml_apply_template` remains `unsupported_contract`.

## v0.2.1 — 2026-07-26 — Trust baseline

This patch preserves the existing public tool names and successful text
responses while making the local execution boundary explicit and testable.

### Added

- `fcp_doctor`, bringing the catalog to 89 tools across 12 categories,
  plus the existing five prompts.
- Functional `fcp-mcp --version`, `serve`, `doctor`, and
  `doctor --json` CLI modes.
- Stable coded MCP failures, structured diagnostics, tool annotations,
  path scoping, transactional FCPXML receipts, and machine-validated
  documentation examples.
- Configuration for allowed input roots, opt-in live control, and text
  or JSON transaction events.

### Changed

- Runtime SDK dependency is bounded to `mcp>=1.27,<2`.
- FCPXML writes use validated same-directory temporary files, backups,
  atomic replacement, and post-commit verification.
- AppleScript and JXA dynamic values are delivered through `run argv`
  instead of being interpolated into program source.
- Media and Compressor calls verify command status and promised output.
- Live FCP and Compressor actions are disabled until
  `FCP_MCP_ENABLE_LIVE_CONTROL` is explicitly enabled.
- All 89 tools carry public MCP safety annotations.
- The tag workflow isolates verification from publication and uses PyPI
  Trusted Publishing instead of a stored API token.

### Corrected contracts

- Known domain failures now return MCP `isError: true` with stable codes.
- `fcpxml_apply_template` returns `unsupported_contract`; v0.2.1 has no
  stable clip-substitution schema and does not pretend to apply one.
- QC reports are documented as Markdown covering validation, statistics,
  gaps, flash frames, duplicate sources, and pacing.
- Beat-driven rough cuts are documented as cadence approximations, not
  frame-exact beat placement.

### FastMCP v1 identity disclosure

FastMCP v1 has no public constructor parameter for an application
version. The initialize response therefore reports the installed MCP SDK
version in `serverInfo.version`. The CLI, server instructions, and doctor
report the `fcp-mcp` package version separately. A public wire-identity
fix is deferred to the stable MCP Python SDK v2 migration.

## v0.2.0 — 2026-04-23 — MCP Prompts

Adds five `@mcp.prompt()` decorators that wrap the most common tool
sequences so MCP clients can invoke them by name instead of chaining
tool calls by hand.

### Prompts (5)

- **`qc-check`** — runs `fcpxml_qc_report` and interprets the results as a
  triaged checklist (auto-fixable vs. manual).
- **`rough-cut`** — wraps `fcpxml_auto_rough_cut` with an optional target
  duration, then re-QCs the output.
- **`cleanup`** — chains `fcpxml_fix_flash_frames` → `fcpxml_fill_gaps` →
  `fcpxml_qc_report` and reports before/after counts.
- **`youtube-chapters`** — extracts FCPXML markers and emits a
  YouTube-ready chapter blob (`HH:MM:SS Title`).
- **`beat-sync`** — calls `media_detect_beats` then constrains rough-cut
  clip length using the detected cadence. Exact beat cut points are not
  part of the v0.2 tool schema.

No breaking changes. All 88 tools from v0.1.0 are unchanged.

## v0.1.0 — 2026-04-22 — Initial public release

First public release. 88 tools across 12 categories covering FCPXML
editing, live FCP control, parametric puppets, media analysis, and
Compressor dispatch — the first MCP server to cover all four layers.

### Tools (88)

- **inspect (8)** — `fcpxml_parse`, `fcpxml_list_clips`, `fcpxml_list_markers`,
  `fcpxml_list_effects`, `fcpxml_list_roles`, `fcpxml_analyze_pacing`,
  `fcpxml_timeline_stats`, `fcpxml_diff`
- **qc (10)** — `fcpxml_detect_gaps`, `fcpxml_detect_flash_frames`,
  `fcpxml_detect_duplicates`, `fcpxml_validate`, `fcpxml_qc_report`,
  `fcpxml_check_media_links`, `fcpxml_check_frame_rates`,
  `fcpxml_check_audio_levels`, `fcpxml_check_safe_zones`,
  `fcpxml_check_duration`
- **edit (12)** — `fcpxml_add_marker`, `fcpxml_add_keyword`,
  `fcpxml_add_title`, `fcpxml_add_audio`, `fcpxml_add_transition`,
  `fcpxml_trim_clip`, `fcpxml_split_clip`, `fcpxml_delete_clips`,
  `fcpxml_reorder_clips`, `fcpxml_change_speed`, `fcpxml_assign_role`,
  `fcpxml_reformat`
- **heal (3)** — `fcpxml_fix_flash_frames`, `fcpxml_fill_gaps`,
  `fcpxml_remove_silence`
- **batch (4)** — `fcpxml_batch_add_markers`, `fcpxml_batch_rename_clips`,
  `fcpxml_batch_assign_roles`, `fcpxml_batch_apply_transition`
- **generate (4)** — `fcpxml_create_project`, `fcpxml_create_timeline`,
  `fcpxml_auto_rough_cut`, `fcpxml_generate_montage`
- **templates (3)** — `fcpxml_list_templates`, `fcpxml_apply_template`,
  `fcpxml_save_template`
- **io (5)** — `fcpxml_import_srt`, `fcpxml_import_edl`,
  `fcpxml_export_edl`, `fcpxml_export_resolve`, `fcpxml_export_fcp7`
- **live (20)** — `fcp_is_running`, `fcp_get_app_state`, `fcp_get_libraries`,
  `fcp_get_events`, `fcp_get_projects`, `fcp_get_timeline_info`,
  `fcp_open_library`, `fcp_import_xml`, `fcp_export_xml`,
  `fcp_playback`, `fcp_navigate`, `fcp_select_tool`, `fcp_undo`, `fcp_redo`,
  `fcp_menu_command`, `fcp_keyboard_shortcut`, `fcp_share`,
  `fcp_discover_effects`, `fcp_list_motion_templates`,
  `fcp_list_share_destinations`
- **puppet (7)** — `puppet_create_rig`, `puppet_create_humanoid_rig`,
  `puppet_build_scene`, `puppet_animate`, `puppet_preset_motion`,
  `puppet_multi_scene`, `puppet_list_presets`
- **media (10)** — `media_info`, `media_list_streams`, `media_loudness`,
  `media_detect_silence`, `media_detect_beats`, `media_scene_detect`,
  `media_extract_thumbnail`, `media_extract_thumbnails`,
  `media_extract_audio`, `media_audio_to_midi`
- **compressor (2)** — `compressor_list_settings`, `compressor_encode`

### Foundations

- Rational-arithmetic timecode (`time_utils.py`) — frame-accurate across
  23.976 / 24 / 29.97 / 59.94 / drop-frame without rounding drift
- Hardened XML parsing (`utils/safe_xml.py`) — `defusedxml`-backed,
  XXE/billion-laughs/external-entity blocked
- Structural validator (`fcpxml/validator.py`) — checks the implemented
  FCPXML invariants; it is not a complete Apple schema validator
- Tree-preserving FCPXML mutation retains supported effects, roles,
  audio lanes, keyframes, markers, and keywords
- 107 unit tests covering parser, writer, time utilities, analysis,
  generator, puppet system
