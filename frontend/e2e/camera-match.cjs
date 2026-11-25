// End-to-end: Camera match on a Studio Look. Sharp clean footage on a soft grainy picture: the panel shows what was measured and what is adjusted.
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

(async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-c-")), shot = path.join(work, "shot.mp4"), bg = path.join(work, "soft grainy.png");
  execFileSync(process.env.EVP_PYTHON || "python3", ["-c", `
import sys; sys.path.insert(0, ${JSON.stringify(BACKEND)}); sys.path.insert(0, ${JSON.stringify(path.join(BACKEND, "tests"))})
import numpy as np, subprocess
from PIL import Image
import test_camera as T
from evp.media import ffmpeg_bin
H, W = T.H, T.W
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
tex = 0.8 + 0.15 * np.sin(xx / 6.0) * np.sin(yy / 6.0)
disc = (np.hypot(xx - 296, yy - 180) < 90)[..., None]
f = np.where(disc, np.array([210, 160, 122], np.float32) / 255.0 * tex[..., None], np.array([0, 1, 0], np.float32))
subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "25", "-i", "-", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p", ${JSON.stringify(shot)}], input=T.u8(f).tobytes() * 25, check=True)
class M(dict): pass
m = M(dir=__import__("pathlib").Path(${JSON.stringify(work)}))
T.bg_png(m, "soft grainy.png", sigma_blur=3.0, noise=0.03, base=(0.25, 0.3, 0.4))
`]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  const H = { "x-evp-token": be.token, "content-type": "application/json" }, base = `http://127.0.0.1:${be.port}`;
  const ops = async () => (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint.operations;
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', shot); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    const asset = (await (await fetch(`${base}/api/project/asset`, { method: "POST", headers: H, body: JSON.stringify({ path: bg }) })).json()).path;
    const bpj = { blueprint_version: "1.0", assets: [{ id: "bg", type: "image", path: asset }], operations: [
      { id: "k", type: "background_removal", start: 0, parameters: { mode: "chroma", spill_suppression: false } },
      { id: "r", type: "background_replacement", start: 0, depends_on: ["k"], parameters: { asset_id: "bg", fit: "cover" } },
      { id: "look", type: "studio_look", start: 0, depends_on: ["r"], parameters: { preset: "none" } }] };
    await page.click("button[aria-label=Blueprint]"); await page.fill("textarea.bp-text", JSON.stringify(bpj)); await page.click(".side >> text=Execute Blueprint");
    await page.waitForTimeout(2500);
    await page.click('button[role=tab]:has-text("Edited")');
    await page.click(".timeline .clip.op:has-text('Studio Look')"); await page.waitForSelector(".cameramatch");
    check(/off/.test(await page.locator(".cameramatch .master-head").innerText()), "a Studio Look shows a Camera match block, off by default");
    await page.click(".cameramatch >> text=Match now");
    await page.waitForSelector(".cm-list", { timeout: 60000 });
    const txt = await page.locator(".cm-list").innerText();
    check(/Sharpness/.test(txt) && /softening the subject/.test(txt), "it measured the sharpness and softens the subject", txt.split("\n")[0]);
    check(/Grain/.test(txt) && /adding grain to the subject/.test(txt), "it measured the grain and adds grain to the subject (the picture is grainier)", txt.split("\n")[1]);
    const lk = (await ops()).find(o => o.type === "studio_look");
    check(lk.parameters.camera_match === 1, "camera_match is stored in the blueprint", String(lk.parameters.camera_match));
    await page.click(".cameramatch button:has-text('Off')"); await page.waitForTimeout(900);
    check((await ops()).find(o => o.type === "studio_look").parameters.camera_match === 0, "Off turns it off again");
    check(await page.locator("text=fringe").count() >= 1, "the lens fringe control is available in the fine-tune list");
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall camera-match checks passed");
})().catch(e => { console.error(e); process.exit(1); });
