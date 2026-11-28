# fcp-mcp Workflows

Production recipes using fcp-mcp tools. Each workflow lists the agent
prompt, the tool sequence, and what makes the workflow non-trivial
without fcp-mcp.

For a one-shot verification that your install works, see
[`examples/quickstart.py`](examples/quickstart.py). For a visual gallery
with renders and narratives, see [`examples/GALLERY.md`](examples/GALLERY.md).

The default `workflow` profile supports inspection plus durable
prepare/review/commit editing. Recipes below that name direct mutators require
the `edit` or `full` profile. Prefer the transactional form whenever the
requested change can be expressed by a workflow operation.

---

## 1. Structural QC pass on a locked cut

**When to use:** right before you bounce a master. Catches flash frames,
gaps, media drift, level problems, and safe-zone breaches in one pass.

```
You: "Run the structural QC report on hero.fcpxml and give me the summary."
```

**Tools (one call):**

```tool-call
{"name":"fcpxml_qc_report","arguments":{"path":"hero.fcpxml"}}
```

**What you get back:** a Markdown report containing structural validation,
timeline statistics, gaps, flash frames, duplicate sources, and pacing.
This aggregate tool does not include the separate media-link,
frame-rate, audio-level, or safe-zone checks.

**Why fcp-mcp:** one call produces a deterministic structural summary
directly from the FCPXML document.

---

## 2. Heal pass after QC

**When to use:** follow-up to recipe 1 for explicit timeline mutations
that address flash frames and gap elements.

```
You: "Fix the flash frames and close the gaps, then re-run QC."
```

**Tools:**

1. Prepare both changes without touching `hero_heal.fcpxml`:

```tool-call
{"name":"fcpxml_workflow_prepare","arguments":{"source_path":"hero.fcpxml","destination_path":"hero_heal.fcpxml","operations":[{"kind":"fix_flash_frames","min_frames":3,"frame_duration":"1001/30000s"},{"kind":"fill_gaps","fill_ref":"r2","fill_name":"Fill"}]}}
```

2. Review the returned summary, warnings, hashes, and `diff_uri`. After the
   user explicitly approves that exact candidate, commit it:

```tool-call
{"name":"fcpxml_workflow_commit","arguments":{"run_id":"123e4567-e89b-42d3-a456-426614174000","expected_candidate_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}
```

Replace the example run ID and hash with the exact values returned by prepare.
The selected `r2` resource must already exist in the source FCPXML.

3. Re-run the structural report after commit:

```tool-call
{"name":"fcpxml_qc_report","arguments":{"path":"hero_heal.fcpxml"}}
```

**What you get back:** the modified FCPXML plus a post-change Markdown
QC report. A zero-item result is valid; do not describe the file as
clean unless the final report supports that conclusion.

**Why fcp-mcp:** both changes are explicit FCPXML mutations. Flash-frame
repair extends short clips; gap filling replaces gaps with the asset you
name, so review the timing and chosen media before importing the result.

---

## 3. Captions from .srt

**When to use:** podcast episode, documentary, or long-form with
separately-transcribed captions.

```
You: "Import captions.srt onto V2 with the role 'captions'."
```

**Tools:**

1. Import the SRT. The importer adds title clips on lane 1 with the
   `Titles.Subtitle` role:

```tool-call
{"name":"fcpxml_import_srt","arguments":{"path":"hero.fcpxml","srt_path":"captions.srt","output_path":"hero_captioned.fcpxml"}}
```

2. Check the transformed document:

```tool-call
{"name":"fcpxml_check_safe_zones","arguments":{"path":"hero_captioned.fcpxml"}}
```

3. If pre-existing clips named `Caption ...` need a different role,
   use JSON matching rules:

```tool-call
{"name":"fcpxml_batch_assign_roles","arguments":{"path":"hero_captioned.fcpxml","rules_json":"[{\"match\":\"Caption\",\"role\":\"Titles.Caption\"}]","output_path":"hero_captioned.fcpxml"}}
```

**Why fcp-mcp:** `.srt` → FCPXML title clips has nasty edge cases
(sub-frame drift, overlapping captions, non-ASCII). `import_srt` handles
them deterministically.

---

## 4. Beat-informed rough cut

**When to use:** music video, promo, or any edit where shot cadence
should approximate musical beat spacing.

```
You: "Build a 60-second rough cut from shots_01–12, synced to song.mp3."
```

**Tools:**

1. Detect beat timestamps:

```tool-call
{"name":"media_detect_beats","arguments":{"path":"song.mp3"}}
```

2. Measure loudness separately:

```tool-call
{"name":"media_loudness","arguments":{"path":"song.mp3"}}
```

3. Calculate a representative inter-beat interval, then use it as the
   maximum clip duration:

```tool-call
{"name":"fcpxml_auto_rough_cut","arguments":{"clips_json":"[{\"src\":\"shots_01.mov\",\"name\":\"Shot 01\",\"duration\":\"5s\"},{\"src\":\"shots_02.mov\",\"name\":\"Shot 02\",\"duration\":\"5s\"}]","target_duration":"60s","max_clip_duration":"2s","project_name":"Promo","output_path":"promo.fcpxml"}}
```

4. Add the audio asset:

```tool-call
{"name":"fcpxml_add_audio","arguments":{"path":"promo.fcpxml","audio_src":"song.mp3","position":"end","output_path":"promo_with_audio.fcpxml"}}
```

5. Confirm the resulting duration:

```tool-call
{"name":"fcpxml_timeline_stats","arguments":{"path":"promo_with_audio.fcpxml"}}
```

**Why fcp-mcp:** The current tools can derive a beat cadence and use it to constrain
shot length. It does not yet accept individual beat cut points, so this
is an approximation rather than frame-exact beat placement.

---

## 5. Long-form interview diagnostics and gap cleanup

**When to use:** inspect source silence, remove long FCPXML gap elements,
and add known review markers. This workflow does not transcribe or cut
source-media silence intervals.

```
You: "Clean up interview_raw.fcpxml — silences, pauses, mark every 'um'."
```

**Tools:**

1. Identify the interview clip and source path:

```tool-call
{"name":"fcpxml_list_clips","arguments":{"path":"interview_raw.fcpxml"}}
```

2. Detect silence in that source:

```tool-call
{"name":"media_detect_silence","arguments":{"path":"interview.mov","noise_threshold":"-40dB","min_duration":0.8}}
```

3. Independently remove FCPXML gap elements at least 0.8 seconds long:

```tool-call
{"name":"fcpxml_remove_silence","arguments":{"path":"interview_raw.fcpxml","silence_threshold_seconds":0.8,"output_path":"interview_clean.fcpxml"}}
```

4. Optionally add review markers from known timecodes:

```tool-call
{"name":"fcpxml_batch_add_markers","arguments":{"path":"interview_clean.fcpxml","markers_json":"[{\"clip_name\":\"Interview A\",\"start\":\"30s\",\"value\":\"Review filler word\"}]","output_path":"interview_clean.fcpxml"}}
```

5. Compare duration:

```tool-call
{"name":"fcpxml_timeline_stats","arguments":{"path":"interview_clean.fcpxml"}}
```

**Why fcp-mcp:** FFmpeg-backed silence analysis and deterministic
timeline-gap removal are available in the same server, while their
distinct semantics remain explicit.

---

## 6. YouTube chapter markers from FCP markers

**When to use:** documentary, podcast, or tutorial with marker-driven
chapter navigation on YouTube.

```
You: "Emit YouTube chapter timestamps from the chapter markers on hero.fcpxml."
```

**Tools:**

```tool-call
{"name":"fcpxml_list_markers","arguments":{"path":"hero.fcpxml"}}
```

The agent reformats the returned markers into YouTube's `MM:SS Label`
format, one per line.

**Why fcp-mcp:** no FCP share destination produces the YouTube chapter
format. This is a one-call workflow — the agent does the formatting,
fcp-mcp does the data.

---

## 7. Multicam sync + proxy round-trip

**When to use:** event coverage with 2–6 cameras + proxy edit on a
laptop, online on a workstation.

```
You: "Check that all proxy clips have originals, then export for online."
```

**Tools:**

1. Identify media references:

```tool-call
{"name":"fcpxml_list_clips","arguments":{"path":"event_proxy.fcpxml"}}
```

2. Flag missing referenced files:

```tool-call
{"name":"fcpxml_check_media_links","arguments":{"path":"event_proxy.fcpxml"}}
```

3. Export for Resolve after links are valid:

```tool-call
{"name":"fcpxml_export_resolve","arguments":{"path":"event_proxy.fcpxml","output_path":"event_online.xml"}}
```

**Why fcp-mcp:** link checking happens before the cross-NLE export.
There is no relink tool; repair missing paths in Final Cut Pro or the
source FCPXML before exporting.

---

## 8. Compressor batch encode matrix

**When to use:** one master → N deliverable formats (ProRes master, H.264
web, HEVC mobile, AAC-only audio, etc.).

```
You: "Bounce hero.mov to the four deliverable presets I have configured."
```

**Tools:**

1. List installed presets and Compressor CLI information:

```tool-call
{"name":"compressor_list_settings","arguments":{}}
```

2. Submit each selected `.cmprstng` file:

```tool-call
{"name":"compressor_encode","arguments":{"input_path":"hero.mov","setting_path":"Presets/Apple ProRes 422 HQ.cmprstng","output_dir":"deliverables","batch_name":"ProRes master"}}
```

```tool-call
{"name":"compressor_encode","arguments":{"input_path":"hero.mov","setting_path":"Presets/YouTube 4K.cmprstng","output_dir":"deliverables","batch_name":"YouTube 4K"}}
```

```tool-call
{"name":"compressor_encode","arguments":{"input_path":"hero.mov","setting_path":"Presets/HEVC Mobile 1080p.cmprstng","output_dir":"deliverables","batch_name":"HEVC mobile"}}
```

```tool-call
{"name":"compressor_encode","arguments":{"input_path":"hero.mov","setting_path":"Presets/Audio AAC 320k.cmprstng","output_dir":"deliverables","batch_name":"AAC audio"}}
```

**Why fcp-mcp:** each call invokes the checked Compressor CLI and
reports its captured submission output. The current tool does not track encode
completion or normalize the output into a portable job-ID schema.

---

## 9. Vertical split-screen composition (9:16)

**When to use:** turning a 16:9 talking-head clip into a vertical short
where the speaker fills the lower half and titles, generators, and looks
sit above it. Requires the `edit` or `full` profile.

```
You: "In vertical.fcpxml, put Talk in the lower half, add a backdrop and a
hook title in the top half, apply my look, and animate the entrance."
```

Start from an FCPXML whose project is already 1080x1920 and whose primary
storyline holds the 16:9 clip (for example, a vertical project exported
from Final Cut Pro with File > Export XML). The numbers below assume that
project, a clip named `Talk` with `start="0s"`, and a sequence `tcStart` of
`0s`; read the real values with `fcpxml_list_clips` first.

FCPXML positions are percentages of the frame *height* from the centre
(up is positive). In a 1920-pixel-tall frame the lower-half centre is 480 px
down, so `position_y` is `-25`. Under the default "Fit" conform a 1920x1080
clip is 607.5 px tall in a 1080-wide frame, so `scale` 1.580247 fills the
960 px half. These values are derived from that arithmetic; check them in
the viewer.

**Tools:**

1. Move and scale the speaker into the lower half:

```tool-call
{"name":"fcpxml_set_clip_transform","arguments":{"path":"vertical.fcpxml","clip_name":"Talk","position_y":-25,"scale_x":1.580247,"scale_y":1.580247,"output_path":"vertical_1.fcpxml"}}
```

2. Connect a generator over the top half. A full-frame generator scaled to
   half height (`scale_y` 0.5) and moved up 25% covers exactly the top half.
   Copy the `uid` from an FCPXML that Final Cut exported with that generator;
   the one below is an example:

```tool-call
{"name":"fcpxml_add_connected_generator","arguments":{"path":"vertical_1.fcpxml","generator_name":"Backdrop","generator_uid":".../Generators.localized/Solids.localized/Custom.localized/Custom.motn","timeline_offset":"0s","duration":"10s","lane":1,"position_y":25,"scale_y":0.5,"output_path":"vertical_2.fcpxml"}}
```

3. Add the hook title on a higher lane in the top half:

```tool-call
{"name":"fcpxml_add_connected_title","arguments":{"path":"vertical_2.fcpxml","text":"THE HOOK","timeline_offset":"0s","duration":"3s","lane":2,"font_size":96,"position_y":25,"output_path":"vertical_3.fcpxml"}}
```

4. Apply a video effect to the speaker. Built-in effects and Motion-template
   effects (including third-party FxPlug plug-ins that ship as `.moef`
   templates) are identified by their FCPXML `uid`; look it up with
   `fcp_search_motion_templates` (recipe 10). The uid below is an example:

```tool-call
{"name":"fcpxml_add_video_effect","arguments":{"path":"vertical_3.fcpxml","clip_name":"Talk","effect_name":"My Look","effect_uid":"~/Effects.localized/My Look/My Look.moef","output_path":"vertical_4.fcpxml"}}
```

5. Animate the entrances. Keyframe times are clip-local: a clip's first
   frame is its `start` value (`0s` here), not its timeline offset:

```tool-call
{"name":"fcpxml_add_transform_keyframes","arguments":{"path":"vertical_4.fcpxml","clip_name":"Talk","param":"position","keyframes_json":"[{\"time\":\"0s\",\"value\":\"0 -60\",\"interp\":\"easeOut\"},{\"time\":\"1/2s\",\"value\":\"0 -25\"}]","output_path":"vertical_5.fcpxml"}}
```

```tool-call
{"name":"fcpxml_add_transform_keyframes","arguments":{"path":"vertical_5.fcpxml","clip_name":"THE HOOK","param":"scale","keyframes_json":"[{\"time\":\"0s\",\"value\":\"0 0\"},{\"time\":\"1/2s\",\"value\":\"1 1\",\"interp\":\"ease\"}]","output_path":"vertical_6.fcpxml"}}
```

**What you get back:** one committed FCPXML per step, each with a
transaction receipt and the written transform, filter, connected-clip, or
keyframe values. Connected clips report both the requested
`timeline_offset` and the parent-local `offset` that was written.

**Limits:** the import bridge (`fcpxml_prepare_import`) accepts only native
effect uids and factory (`.../`) Motion templates ending in `.moti`, `.moef`,
`.motr`, or `.mopn`. Files that use generators (`.motn`) or templates from
`~/Movies/Motion Templates` must be imported with File > Import > XML in
Final Cut Pro. `mc-clip` and `gap` elements cannot take transforms or video
filters in FCPXML. Keyframe times are treated as clip-local (from the clip's
`start`), following the DTD's definition of `start`.

**Why fcp-mcp:** connected-clip offsets live in the parent clip's local
time and every element has a DTD-mandated position among its siblings; the
tools do that arithmetic and ordering for you and verify the committed file.

---

## 10. Apply a Motion template with its published parameters

**When to use:** any installed title, effect, transition, or generator
template, built-in or third-party, where you want to set the parameters the
template publishes to Final Cut's inspector.

```
You: "Find my glow look, show me its parameters, and put it on Talk at 80%
brightness. Then use a swipe transition into the next clip."
```

**Tools:**

1. Find the template and its FCPXML `uid`. User templates use
   `~/<path below Motion Templates.localized>`; built-ins use `.../<path>`:

```tool-call
{"name":"fcp_search_motion_templates","arguments":{"query":"glow","category":"Effects","limit":10}}
```

2. Inspect its published parameters. Each record has the display `name`,
   the FCPXML `key` (`9999/<object ids>/<channel ids>`), `kind`, `default`,
   current `value`, and range or menu `options` when the template defines
   them. Titles also list their text layers; FxPlug-based templates list the
   plug-in UUID:

```tool-call
{"name":"fcp_inspect_motion_template","arguments":{"template":"~/Effects.localized/Example Pack/Glow/Glow.moef"}}
```

3. Apply it, passing the chosen keys with their names and new values:

```tool-call
{"name":"fcpxml_add_video_effect","arguments":{"path":"edit.fcpxml","clip_name":"Talk","effect_name":"Glow","effect_uid":"~/Effects.localized/Example Pack/Glow/Glow.moef","params_json":"{\"9999/20003/20007/20119/59/63\": {\"name\": \"Brightness\", \"value\": \"80\"}}","output_path":"edit_1.fcpxml"}}
```

4. Transition templates work the same way. This tool centres the transition
   on the cut after the named clip and leaves the clips unchanged, so the
   media needs handles:

```tool-call
{"name":"fcpxml_add_transition_template","arguments":{"path":"edit_1.fcpxml","after_clip_name":"Talk","effect_name":"Swipe","effect_uid":"~/Transitions.localized/Example Pack/Swipe/Swipe.motr","duration":"1s","output_path":"edit_2.fcpxml"}}
```

5. For a title template whose text is a published parameter, pass it in
   `params_json` and set `text` to `""` so no `<text>` element is written:

```tool-call
{"name":"fcpxml_add_connected_title","arguments":{"path":"edit_2.fcpxml","text":"","timeline_offset":"0s","duration":"3s","lane":2,"title_name":"Headline","title_uid":"~/Titles.localized/Example Pack/Headline/Headline.moti","params_json":"{\"9999/30001/30010/2/369\": {\"name\": \"Headline\", \"value\": \"Big News\"}}","output_path":"edit_3.fcpxml"}}
```

**What you get back:** template records with uids and preview paths, a
parameter list, and committed FCPXML whose `<param name key value>` entries
match what you passed.

**Limits:** the `9999/<layer>/<object>/…` form (with `5/<style id>` for text
styles) matches keys Final Cut stored for Basic Title, and such parameters
report `key_confidence: "confirmed_pattern"`. Keys that pass through a filter
(for example an FxPlug plug-in wrapped in a template), a behavior, or the
template root report `"unverified"`; check those in Final Cut Pro. Whether Final Cut prefers a published text
parameter over a `<text>` element depends on the template.
