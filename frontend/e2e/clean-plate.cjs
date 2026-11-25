// End-to-end: import a clean plate in the Properties panel; the matte view gets cleaner and the panel says the plate fits.
const { chromium } = require("playwright");
const { spawn, execFileSync } = require("child_process");
const http = require("http"), fs = require("fs"), path = require("path"), os = require("os");
const ROOT = path.resolve(__dirname, "..", ".."), DIST = path.join(ROOT, "frontend", "dist"), BACKEND = path.join(ROOT, "backend");
let failures = 0;
const check = (ok, what, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${what}${extra ? "  (" + extra + ")" : ""}`); if (!ok) failures++; };
const startBackend = (dataDir) => new Promise((resolve, reject) => {
  const proc = spawn(process.env.EVP_PYTHON || "python3", ["-m", "evp.server", "--data-dir", dataDir], { cwd: BACKEND });
  let buf = ""; proc.stdout.on("data", d => { buf += d; const nl = buf.indexOf("\n"); if (nl >= 0) { try { resolve({ proc, ...JSON.parse(buf.slice(0, nl)) }); } catch (e) { reject(e); } } });
  proc.on("exit", c => reject(new Error("backend exited " + c))); setTimeout(() => reject(new Error("backend did not start")), 20000);
});
const serveDist = () => { const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
  const srv = http.createServer((q, r) => { const f = path.join(DIST, q.url.split("?")[0] === "/" ? "index.html" : q.url.split("?")[0]); fs.readFile(f, (e, d) => { if (e) { r.writeHead(404); r.end(); } else { r.writeHead(200, { "content-type": types[path.extname(f)] || "application/octet-stream" }); r.end(d); } }); });
  return new Promise(res => srv.listen(0, "127.0.0.1", () => res(srv))); };
// the brightest value of the key-view picture in the part of the frame far from the subject (haze that should be black)
const haze = (page) => page.evaluate(() => { const img = document.querySelector('img[alt="Key view"]'); const c = document.createElement("canvas"); c.width = img.naturalWidth; c.height = img.naturalHeight;
  const x = c.getContext("2d"); x.drawImage(img, 0, 0); const d = x.getImageData(0, 0, c.width, c.height).data; let m = 0;
  for (let y = 0; y < c.height; y++) for (let xx = 0; xx < c.width; xx++) { if (Math.hypot(xx - c.width * 0.46, y - c.height * 0.5) > c.height * 0.36) m = Math.max(m, d[(y * c.width + xx) * 4]); } return m; });

const hazeApi = (page, base, token, blueprint) => page.evaluate(async ([base, token, blueprint]) => {
  const r = await fetch(base + "/api/preview/frame", { method: "POST", headers: { "x-evp-token": token, "content-type": "application/json" }, body: JSON.stringify({ blueprint, t: 0.2, width: 320, view: "alpha" }) });
  const bmp = await createImageBitmap(await r.blob()); const c = document.createElement("canvas"); c.width = bmp.width; c.height = bmp.height; const x = c.getContext("2d"); x.drawImage(bmp, 0, 0);
  const d = x.getImageData(0, 0, c.width, c.height).data; let m = 0;
  for (let y = 0; y < c.height; y++) for (let xx = 0; xx < c.width; xx++) if (Math.hypot(xx - 148, y - 90) > 60) m = Math.max(m, d[(y * c.width + xx) * 4]);
  return m; }, [base, token, blueprint]);

(async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-p-")), shot = path.join(work, "shot.mp4"), plate = path.join(work, "empty screen.png");
  execFileSync(process.env.EVP_PYTHON || "python3", ["-c", `
import sys; sys.path.insert(0, ${JSON.stringify(BACKEND)}); sys.path.insert(0, ${JSON.stringify(path.join(BACKEND, "tests"))})
import numpy as np, subprocess
from PIL import Image
import test_cleanplate as T
from evp.media import ffmpeg_bin
S = T.screen(0.45, 0.4)
raw = b"".join((np.clip(T.scene(S, 0.94, cx=148)[0], 0, 1) * 255 + 0.5).astype(np.uint8).tobytes() for _ in range(25))
subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "320x180", "-r", "25", "-i", "-", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p", ${JSON.stringify(shot)}], input=raw, check=True)
Image.fromarray((S * 255 + 0.5).astype(np.uint8)).save(${JSON.stringify(plate)})
`]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  const H = { "x-evp-token": be.token }, base = `http://127.0.0.1:${be.port}`;
  const ops = async () => (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint.operations;
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', shot); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    await page.click("button[aria-label=Effects]"); await page.click(".side button.item:has-text('Background Removal')"); await page.waitForTimeout(500);
    check(await page.locator(".cleanplate").count() === 0, "the Clean plate section appears only for the screen key");
    await page.selectOption(".props select >> nth=0", "screen"); await page.waitForSelector(".cleanplate");
    check(await page.locator(".cleanplate").count() === 1, "choosing the screen key shows the Clean plate section");
    await page.click('button[role=tab]:has-text("Edited")');
    await page.selectOption('select[aria-label="Key view"]', "alpha");
    await page.waitForFunction(() => { const i = document.querySelector('img[alt="Key view"]'); return i && i.complete && i.naturalWidth > 0; });
    await page.waitForTimeout(2500);                                   // the screen-mode matte of this frame
    const stable = async () => { let a = await haze(page); for (let i = 0; i < 6; i++) { await page.waitForTimeout(700); const b = await haze(page); if (b === a) return b; a = b; } return a; };
    const before = await stable();
    await page.click(".cleanplate >> text=Import clean plate…"); await page.fill('input[placeholder="Full path"]', plate); await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.querySelector(".cleanplate").innerText.includes("Replace clean plate"));
    await page.waitForTimeout(2500);
    await page.waitForTimeout(800);
    const k = (await ops()).find(o => o.type === "background_removal");
    check(typeof k.parameters.clean_plate_asset_id === "string", "the plate was imported and linked to the key", String(k.parameters.clean_plate_asset_id));
    await page.waitForFunction(() => document.querySelector(".cleanplate").innerText.includes("fits"), null, { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const after = await stable();
    const full = (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint;
    const noPlate = JSON.parse(JSON.stringify(full)); delete noPlate.operations.find(o => o.type === "background_removal").parameters.clean_plate_asset_id;
    const hz0 = await hazeApi(page, base, be.token, noPlate), hz1 = await hazeApi(page, base, be.token, full);
    check(hz0 > 60 && hz1 < hz0 * 0.4, "the matte is much cleaner far from the subject with the plate", `max haze without ${hz0} -> with ${hz1} (of 255)`);
    check(/plate fits/i.test(await page.locator(".cleanplate").innerText()), "the panel reports that the plate fits the footage");
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall clean-plate checks passed");
})().catch(e => { console.error(e); process.exit(1); });
