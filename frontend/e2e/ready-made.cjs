// End-to-end: ready-made looks are listed in the Ready-made tab and in the section panels, and one click adds the whole tested set of effects.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-r-")), video = path.join(work, "v.mp4");
  execFileSync(process.env.EVP_FFMPEG || "ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=2", "-pix_fmt", "yuv420p", video]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  const errs = []; page.on("pageerror", e => { errs.push(e.message); console.log("STACK:", (e.stack||"").slice(0,500)); });
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', video); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    await page.click("nav.rail button[aria-label='Ready-made']"); await page.waitForSelector(".rm-card");
    const n = await page.locator(".rm-card").count();
    check(n >= 10, "the Ready-made tab lists the looks", String(n));
    const text = await page.locator(".side-content").innerText();
    check(["Start to finish", "Green screen", "Face", "Colour", "Quality"].every(s => text.toLowerCase().includes(s.toLowerCase())), "all five sections are present");
    await page.locator(".rm-card", { hasText: "Clean and sharp" }).locator("button:has-text('Add to project')").click();
    await page.waitForTimeout(2500);      await page.waitForSelector("text=Added 1 effect", { timeout: 15000 });
    check(true, "one click adds the look ('Added 1 effect')");
    await page.waitForSelector("text=1 effect ready", { timeout: 15000 });
    check(true, "the project check accepts it");
    await page.click("nav.rail button[aria-label='Color']"); await page.waitForSelector(".rm-card");
    check(await page.locator(".rm-card", { hasText: "Cinematic teal and orange" }).count() === 1, "the Color panel shows the colour looks");
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall ready-made checks passed");
})().catch(e => { console.error(e); process.exit(1); });
