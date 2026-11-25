// End-to-end: executing a blueprint ADDS to the timeline (keeps background removal etc.); "Replace" is an explicit choice and Undo brings it back.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-m-"));
  const video = path.join(work, "v.mp4");
  execFileSync(process.env.EVP_FFMPEG || "ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=3", "-pix_fmt", "yuv420p", video]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  const types = async () => (await (await fetch(`http://127.0.0.1:${be.port}/api/project`, { headers: { "x-evp-token": be.token } })).json()).blueprint.operations.map(o => o.type);
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', video); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    await page.click("button[aria-label=Effects]"); await page.click(".side >> text=Background Removal");     // work the user did by hand
    await page.waitForTimeout(900);
    check((await types()).join() === "background_removal", "the timeline already holds a hand-made Background Removal");
    const grade = JSON.stringify({ blueprint_version: "1.0", operations: [{ id: "grade", type: "color_grading", start: 0, parameters: { contrast: 1.2 } }] });
    await page.click("button[aria-label=Blueprint]"); await page.fill("textarea.bp-text", grade);
    check(await page.locator('input[name=how]').first().isChecked(), "'Add to my timeline' is the default");
    await page.click(".side >> text=Execute Blueprint"); await page.waitForTimeout(1500);
    let t = await types();
    check(t.includes("background_removal") && t.includes("color_grading"), "after executing a colour-grade blueprint BOTH are on the timeline", t.join());
    // the same blueprint again: ids clash, nothing is lost
    await page.click("button[aria-label=Blueprint]"); await page.fill("textarea.bp-text", grade);
    await page.click(".side >> text=Execute Blueprint"); await page.waitForTimeout(1500);
    t = await types(); check(t.length === 3 && t.filter(x => x === "color_grading").length === 2, "running it again adds a second grade (no id clash, nothing removed)", t.join());
    // explicit replace
    await page.click("button[aria-label=Blueprint]"); await page.fill("textarea.bp-text", grade);
    await page.click("input[name=how] >> nth=1"); await page.click(".side >> text=Execute Blueprint"); await page.waitForTimeout(1500);
    t = await types(); check(t.join() === "color_grading", "'Replace the whole timeline' still replaces when chosen", t.join());
    await page.click(".tl-bar button[aria-label=Undo]"); await page.waitForTimeout(900);
    t = await types(); check(t.length === 3, "and Undo brings the replaced timeline back", t.join());
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall merge checks passed");
})().catch(e => { console.error(e); process.exit(1); });
