// End-to-end: imported backgrounds stay in the Media bin (with thumbnails), survive Undo, can be reused by click and by drag onto the timeline.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-b-"));
  const ff = process.env.EVP_FFMPEG || "ffmpeg", video = path.join(work, "v.mp4"), bg = path.join(work, "Sea Background.png");
  execFileSync(ff, ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=3", "-pix_fmt", "yuv420p", video]);
  execFileSync(ff, ["-v", "error", "-y", "-f", "lavfi", "-i", "gradients=s=640x360:c0=0x0a3d91:c1=0x23c4c4:d=1", "-frames:v", "1", bg]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  const H = { "x-evp-token": be.token, "content-type": "application/json" }, base = `http://127.0.0.1:${be.port}`;
  const ops = async () => (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint.operations;
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', video); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    await page.click("button[aria-label=Media]");
    await page.waitForSelector(".mcard");
    check(await page.locator(".mcard").count() === 1, "the Media bin lists the source video", await page.locator(".mname").first().innerText());
    // import a background through the bin's button (the path dialog of the browser build)
    await page.click(".side .btn-row button:has(span:text-is(\"Background\"))"); await page.fill('input[placeholder="Full path"]', bg); await page.keyboard.press("Enter");
    await page.waitForSelector('.mname:text("Sea Background")');
    check(true, "an imported background appears in the Media bin");
    const img = page.locator(".mcard:has-text('Sea Background') img");
    await img.waitFor(); await page.waitForFunction(() => { const i = document.querySelector(".mcard img[src*='src=asset']"); return i && i.complete && i.naturalWidth > 0; });
    check(true, "with a real thumbnail picture");
    // the import also used it right away (existing behaviour); undo that - the file must stay in the bin
    await page.waitForTimeout(1000);
    if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT });
    check((await ops()).some(o => o.type === "background_replacement"), "importing also placed it as a background");
    await page.click(".tl-bar button[aria-label=Undo]"); await page.click(".tl-bar button[aria-label=Undo]"); await page.waitForTimeout(900);
    check((await ops()).length === 0, "after Undo the timeline is empty again");
    check(await page.locator(".mcard:has-text('Sea Background')").count() === 1, "but the background is STILL in the Media bin");
    // reuse 1: the Use button
    await page.click(".mcard:has-text('Sea Background') >> text=Use"); await page.waitForTimeout(900);
    let o = await ops();
    check(o.some(x => x.type === "background_replacement" && x.parameters.asset_id), "'Use' puts the stored background on the timeline again (no re-import)", o.map(x => x.type).join());
    // reuse 2: drag onto the Background track
    const before = (await ops()).filter(x => x.type === "background_replacement").length;
    const lane = page.locator(".tl-row:has-text('Background') .tl-lane");
    await page.locator(".mcard:has-text('Sea Background')").dragTo(lane, { targetPosition: { x: 200, y: 12 } }); await page.waitForTimeout(900);
    o = await ops(); const reps = o.filter(x => x.type === "background_replacement");
    check(reps.length === before + 1, "dragging the card onto the Background track adds another background layer", `${reps.length} layers`);
    check(reps[reps.length - 1].start > 0.2, "at the place where it was dropped", `start ${reps[reps.length - 1].start}`);
    // restart: still there
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall media bin checks passed");
})().catch(e => { console.error(e); process.exit(1); });
