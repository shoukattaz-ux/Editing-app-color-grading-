// End-to-end: the Key doctor finds a screen that does not fill the frame and an uneven screen; one click adds the garbage mask.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-d-")), shot = path.join(work, "shot.mp4");
  execFileSync(process.env.EVP_PYTHON || "python3", ["-c", `
import sys; sys.path.insert(0, ${JSON.stringify(BACKEND)}); sys.path.insert(0, ${JSON.stringify(path.join(BACKEND, "tests"))})
import numpy as np, subprocess
import test_cleanplate as T, test_keydoctor as K
from evp.media import ffmpeg_bin
S = T.screen(0.45, 0.4)
raw = b"".join(K.scene(S, cx=200 + 3 * (i % 8), room=True).tobytes() for i in range(40))
subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "320x180", "-r", "25", "-i", "-", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p", ${JSON.stringify(shot)}], input=raw, check=True)
`]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  const H = { "x-evp-token": be.token }, base = `http://127.0.0.1:${be.port}`;
  const ops = async () => (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint.operations;
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', shot); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    await page.click("button[aria-label=Effects]"); await page.click(".side button.item:has-text('Background Removal')"); await page.waitForTimeout(400);
    await page.selectOption(".props select >> nth=0", "screen"); await page.waitForSelector(".doctor");
    await page.click(".doctor >> text=Check this key");
    await page.waitForSelector(".finding", { timeout: 60000 });
    if (process.env.SHOT) { await page.locator(".doctor").scrollIntoViewIfNeeded(); await page.screenshot({ path: process.env.SHOT }); }
    const titles = await page.locator(".finding .ft").allInnerTexts();
    check(titles.some(t => /does not fill the frame on the left/i.test(t)), "the doctor finds that the screen does not fill the frame on the left", titles.join(" | "));
    check(titles.some(t => /uneven/i.test(t)), "and that the screen is uneven");
    check(/\d+\/100/.test(await page.locator(".doctor .score").innerText()), "it shows a score");
    check(await page.locator(".finding:has-text('uneven') >> text=Clean plate").count() >= 0 && /clean plate/i.test(await page.locator(".finding:has-text('uneven')").innerText()), "the uneven-screen finding points to the clean plate");
    await page.click(".finding:has-text('does not fill') >> button:has-text('Add a garbage mask')"); await page.waitForTimeout(1200);
    const mk = (await ops()).find(o => o.type === "matte_mask");
    check(mk && mk.parameters.mode === "garbage" && mk.parameters.x === 0 && mk.parameters.width > 0.15 && mk.parameters.width < 0.3, "one click adds the garbage mask with the measured size", JSON.stringify(mk && mk.parameters));
    await page.click("button[aria-label=Effects]");
    const keyBtn = page.locator(".timeline .clip.op:has-text('Background Removal')").first(); await keyBtn.click();
    await page.waitForSelector(".doctor");
    await page.click(".doctor button:has-text('Check')"); await page.waitForTimeout(500);
    await page.waitForFunction(() => !document.querySelector(".doctor").innerText.includes("Checking"), null, { timeout: 60000 });
    await page.waitForTimeout(800);
    const after = await page.locator(".finding .ft").allInnerTexts();
    check(!after.some(t => /does not fill the frame on the left/i.test(t)), "after the mask exists, the doctor no longer reports that problem", after.join(" | "));
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall key-doctor checks passed");
})().catch(e => { console.error(e); process.exit(1); });
