---
name: video-digest
description: Turn YouTube videos/playlists or TikTok videos into evidence-grounded notes and takeaways with timecodes and visual observations. Use when the user wants to understand or summarize a video, including silent demos and timelapses.
---

Use the installed `video-digest` tool to collect evidence, then read it and write
`notes.md` beside each video's `digest.md`. This works in an ordinary folder;
no project, repository, account, issue tracker, or particular model is required.
The tool extracts evidence; the assistant writes the notes.

## Collect evidence

Run `video-digest "<url>"` with the user's output folder as `--out "<folder>"`
when specified. If the command is not on PATH, use the installed environment's
full executable path, or `python -m video_digest "<url>"` with that environment's
Python. Installation and prerequisite instructions are in the distribution's
README.md; do not assume Nix or a specific checkout location.

Without `--out`, output is under the user's home directory in
`video-digests/<title-slug>/`. Record the actual output path. A video produces
`digest.md`, `transcript.txt`, `meta.json`, and `frames/`; a playlist also produces
`index.md` linking the numbered video folders. Long videos can take minutes;
wait for the process to finish using the available execution tools.

TikTok video URLs and short share links use the same command. TikTok timecodes
are offsets within the clip; links open the source video without a guaranteed
seek position. Cite those offsets and the local frame filenames instead of
inventing YouTube or TikTok seek URLs. Captions may be absent or only burned
into the images; the tool does not transcribe audio or OCR text. Inspect visible
text as visual evidence, and report missing spoken evidence rather than treating
the description, music title, or a frames-only digest as a speech transcript.

Reruns reuse completed digests; use `--force` only when regeneration is needed.
If `notes.md` already exists, inspect and reuse it when it covers the user's
request and current digest. Do not silently overwrite user edits. A playlist
can partially fail: inspect its index and report missing videos. Retry a
transient failure once if useful, then report the remaining gap instead of
repeatedly downloading.

## Read, observe, and take notes

Read the digest header for source details and caption limitations. Inspect the
transcript and the actual frame images using available image-reading tools;
Markdown image links alone are not visual evidence. For a long video, work in
bounded timestamp sections. If delegation is available and appropriate, assign
one video or section per worker with the digest path and the user's question;
otherwise process directly. There is no required provider, model, or concurrency.

Use [references/notes-template.md](references/notes-template.md) as a starting
shape. Keep only sections that help this request:

- State the thesis and useful takeaways, each linked to its video timestamp.
- Attribute the speaker's claims; distinguish them from your inferences.
- Record what the frames visibly show beyond the speech, with a frame filename
  and timestamp. In silent videos, derive observations from the frames without
  inventing narration, hidden steps, or causal explanations.
- Flag uncertainties and sampling gaps. Missing or sparse English captions do
  not establish that the video is actually silent. Frame sections marked
  "no speech" mean no usable captions in that interval. Frequent music markers
  may indicate lyrics rather than speech.
- If images cannot be inspected, say that visual review is incomplete and limit
  the notes to evidence you could read. Do not claim to have watched the video.

Before finishing, verify the central takeaways against their cited transcript
sections or frames (at least one per video). For playlists, read every video's
notes and provide a short synthesis with links, preserving disagreements and
noting any failed videos. Report the notes paths and key limitations.

## Optional project follow-up

Only when the user asks to apply the material to a project, relate the evidence
to the project context they provide. Suggest concrete next steps separately from
source claims. Create or update issues only if the user explicitly requests it,
using their chosen tracker and conventions. No labels, project maps, private
notes, or other skills are required. The default outcome is usable notes alone.
