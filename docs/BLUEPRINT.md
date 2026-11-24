# Blueprint format (v1.0)

Machine-readable schema: [`blueprint.schema.json`](blueprint.schema.json). Every operation's parameters: [`OPERATIONS.md`](OPERATIONS.md). Working example: [`../samples/space_presenter.blueprint.json`](../samples/space_presenter.blueprint.json).

```json
{
  "blueprint_version": "1.0",
  "project": { "name": "Space Presenter", "source_video": "greenscreen.mp4" },
  "output": { "resolution": "source", "fps": "source", "format": "mp4", "codec": "h264", "crf": 20 },
  "assets": [ { "id": "space_background", "type": "image", "path": "assets/space_background.png" } ],
  "operations": [
    { "id": "operation_001", "type": "background_removal", "start": 0, "end": 6, "parameters": { "quality": "high" } },
    { "id": "operation_002", "type": "background_replacement", "start": 0, "end": 6,
      "parameters": { "asset_id": "space_background", "fit": "cover" }, "depends_on": ["operation_001"] },
    { "id": "operation_003", "type": "color_grading", "start": "00:00:01.000", "end": 5, "parameters": { "contrast": 1.12 } }
  ]
}
```

## Cutting: `clips`
Optional. The timeline is the listed ranges of the source video played back to back (ripple — no gaps); audio follows the same cuts. Omit `clips` to use the whole video.
```json
"clips": [ { "id": "intro", "start": 0.0, "end": 12.5 }, { "id": "take2", "start": "0:31.0", "end": "1:02.5" } ]
```
* `start`/`end` are **source** times (same formats as operations), snapped to the output frame grid. A clip may appear more than once (repeat), overlap another, or be listed out of order (reorder).
* **Operation `start`/`end` are on the cut timeline** (after the cuts), not on the source video. Total timeline length = sum of the clips.
* A single clip that covers the whole source is the same as no cuts. An empty `clips` array is an error.
* Limits: 500 clips; if clips are reordered or overlap in the source, at most 48 (each needs its own decoder). Clips in source order have no such limit.
* Audio: sample-accurate cuts with a 4 ms fade at every real joint (no fade where two clips are contiguous in the source). If the source's audio is shorter than its video, the missing part is silence so later clips stay in sync.
* One source video per project (clips cannot yet come from different files). AI passes (matting, face analysis, upscaling) are keyed to the cuts, so changing the cuts recomputes the affected AI effects.

## Rules
* **Timestamps**: seconds (`1.5`), `"1:02.5"` or `"00:01:02.500"`. Snapped to the output frame grid (a `TIME_SNAPPED` warning is issued if that moved a time by >1 ms). `end` omitted = end of video. `end` beyond the video is an error (`TIME_OUT_OF_RANGE`).
* **ids**: `[A-Za-z0-9_.-]{1,64}`, unique.
* **parameters**: unknown names, wrong types, out-of-range values and unknown enum values are errors. Missing ones take registry defaults.
* **assets**: `image` (png/jpg/webp/bmp), `video` (mp4/mov/mkv/webm/avi), `lut` (`.cube`/`.3dl`). Paths are relative to the project folder; `..`, URLs, UNC paths and anything resolving (including via symlink) outside the project are rejected (`UNSAFE_PATH`). Import files with the Assets tab so they are copied into `assets/`.
* **depends_on**: must exist, no cycles. A `background_replacement` needs an alpha source (`chroma_key` or `background_removal`) that fully covers its time range — name it in `depends_on` or leave it implicit (the covering one is chosen). A key consumed by a replacement is composited over that replacement's background; an unconsumed key composites over its own `background_color`.
* **Overlaps**: two overlapping `background_replacement`s are an error; overlapping grades/keys/enhancements stack (warning).
* **enabled**: `false` skips the operation (used by the UI's Disable).
* Unknown operation → `UNKNOWN_OPERATION`; recognised-but-unavailable (face/AI operations) → `UNSUPPORTED_OPERATION`. Neither is ever skipped silently, and nothing runs until the blueprint validates.

## Error / warning codes
`TOO_MANY_CLIPS, EMPTY_TIMELINE, TOO_MANY_REORDERED_CLIPS, INVALID_JSON, INVALID_STRUCTURE, UNSUPPORTED_VERSION, INVALID_ID, DUPLICATE_ID, UNKNOWN_OPERATION, UNSUPPORTED_OPERATION, INVALID_TIMESTAMP, TIME_OUT_OF_RANGE, INVALID_PARAMETER, UNKNOWN_ASSET, ASSET_TYPE_MISMATCH, MISSING_ASSET, UNSAFE_PATH, INVALID_ASSET_EXT, UNKNOWN_DEPENDENCY, DEPENDENCY_CYCLE, DEPENDENCY_RANGE, MISSING_ALPHA_SOURCE, OVERLAP_CONFLICT, BAD_SOURCE` (errors) · `TIME_SNAPPED, UNKNOWN_FIELD, OVERLAP_STACKED, NO_AUDIO, NO_VIDEO, DEPENDENCY_DISABLED, ASPECT_CHANGE, FPS_UPCONVERT, SPILL_UNSUPPORTED_COLOR, NO_OPERATIONS` (warnings).

## Prompt template for the external AI
> You are generating an editing blueprint for the EVP editor. Output **only** JSON conforming to `blueprint.schema.json` (version 1.0). Use only operation types listed in `OPERATIONS.md` that are not marked "Not available". Give each operation a unique `id`, timestamps in seconds, and use `depends_on` for background replacement → key. Do not reference files other than assets you list in `assets` with relative paths like `assets/name.png`. Video duration: {DURATION}s, {WIDTH}x{HEIGHT} @ {FPS}fps.
