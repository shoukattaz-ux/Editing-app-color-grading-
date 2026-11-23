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
`INVALID_JSON, INVALID_STRUCTURE, UNSUPPORTED_VERSION, INVALID_ID, DUPLICATE_ID, UNKNOWN_OPERATION, UNSUPPORTED_OPERATION, INVALID_TIMESTAMP, TIME_OUT_OF_RANGE, INVALID_PARAMETER, UNKNOWN_ASSET, ASSET_TYPE_MISMATCH, MISSING_ASSET, UNSAFE_PATH, INVALID_ASSET_EXT, UNKNOWN_DEPENDENCY, DEPENDENCY_CYCLE, DEPENDENCY_RANGE, MISSING_ALPHA_SOURCE, OVERLAP_CONFLICT, BAD_SOURCE` (errors) · `TIME_SNAPPED, UNKNOWN_FIELD, OVERLAP_STACKED, NO_AUDIO, NO_VIDEO, DEPENDENCY_DISABLED, ASPECT_CHANGE, FPS_UPCONVERT, SPILL_UNSUPPORTED_COLOR, NO_OPERATIONS` (warnings).

## Prompt template for the external AI
> You are generating an editing blueprint for the EVP editor. Output **only** JSON conforming to `blueprint.schema.json` (version 1.0). Use only operation types listed in `OPERATIONS.md` that are not marked "Not available". Give each operation a unique `id`, timestamps in seconds, and use `depends_on` for background replacement → key. Do not reference files other than assets you list in `assets` with relative paths like `assets/name.png`. Video duration: {DURATION}s, {WIDTH}x{HEIGHT} @ {FPS}fps.
