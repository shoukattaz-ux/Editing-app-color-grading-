# Developer guide

## Layout
```
backend/evp/ml/{models,frames,matting,face,srvgg,rrdb,upscale}.py   # model store, RVM matting, face analysis, Real-ESRGAN converter + upscaler
backend/evp/{keyframes,animate}.py       # keyframe maths + FFmpeg command generation (shared/keyframe_vectors.json is tested by Python AND TypeScript)
backend/evp/edl.py                     # timeline<->source frame mapping for cuts (used by compiler, AI passes, previews)
backend/evp/{registry,blueprint,planner,compiler,engine,media,project,security,server,cli,samples}.py
backend/tests/          # 212 tests (+2 opt-in: EVP_TEST_PERSON_VIDEO=clip.mp4); real FFmpeg renders + pixel assertions
backend/scripts/{gen_docs,gen_keyframe_vectors}.py   # regenerates docs/blueprint.schema.json + docs/OPERATIONS.md (a test fails if stale)
frontend/src/           # model.ts (pure logic, vitest) · App/Preview/Timeline/Properties/Sidebar (React)
electron/               # main.js, preload.js, electron-builder config
```
## Run
```bash
pip install -r backend/requirements.txt
cd backend && python -m pytest -q                      # engine tests
cd ../frontend && npm i && npm test && npm run build   # UI logic tests + production build
python -m evp.cli sample /tmp/demo                      # synthetic green-screen video + background
python -m evp.cli run --video /tmp/demo/greenscreen.mp4 --blueprint samples/space_presenter.blueprint.json --out /tmp/demo/out.mp4
```
Dev UI in a browser: `cd backend && python -m evp.server --port 8765` prints `{"port":…,"token":…}`; serve `frontend/dist` (`python -m http.server`) and open `http://127.0.0.1:PORT/?port=8765&token=TOKEN`.
Desktop: `cd electron && npm i && npm start` (needs Python deps + built `frontend/dist`; set `EVP_PYTHON` to pick the interpreter). Env overrides: `EVP_FFMPEG`, `EVP_FFPROBE`, `EVP_FONT`.

## Adding an operation (e.g. a model-based one)
1. Add an `OpSpec` in `registry.py` (params with limits; `supported=True`).
2. Handle it in `compiler.py` (`compile_plan`'s walk) – return a filter-graph stream label – or add a pre-processing job stage in `engine.py` for non-FFmpeg work (decode frames → model → write mask video → feed as an extra input). Models must be loaded lazily and fail with a clear message when weights/GPU are missing.
3. Add validation rules in `blueprint.py`/`planner.py` if it has cross-operation constraints.
4. `python backend/scripts/gen_docs.py`, add tests (render a frame, assert pixels).
The UI needs no change: the properties panel and Effects list are generated from `/api/schema`.

## Security notes
Blueprints are data: operations come from a closed registry, parameters are type/range-checked, text is passed via a file with `expansion=none`, paths go through `PathPolicy`, FFmpeg is run without a shell, the HTTP API requires a per-launch token and listens on loopback only, outputs can't alias the source.
