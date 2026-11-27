// End-to-end: a queued render says which render it is waiting for, and 'Stop the earlier render' lets it start.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-q-")), video = path.join(work, "v.mp4");
  execFileSync(process.env.EVP_FFMPEG || "ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=40", "-pix_fmt", "yuv420p", video]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  const H = { "x-evp-token": be.token, "content-type": "application/json" }, base = `http://127.0.0.1:${be.port}`;
  const api = async (m, u, b) => (await fetch(base + u, { method: m, headers: H, body: b ? JSON.stringify(b) : undefined })).json();
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', video); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    await page.click("nav.rail button[aria-label='Ready-made']"); await page.waitForSelector(".rm-card");
    await page.locator(".rm-card", { hasText: "Clean and sharp" }).locator("button:has-text('Add to project')").click();
    await page.waitForSelector("text=1 effect ready", { timeout: 15000 });
    const bp = (await api("GET", "/api/project")).blueprint;
    const first = (await api("POST", "/api/render", { blueprint: bp, preset: "export", filename: "first" })).job;      // holds the slot
    await api("POST", `/api/jobs/${first.id}/pause`);
    const paused = await api("GET", `/api/jobs/${first.id}`);
    check(paused.state === "paused", "the first render holds the slot (paused for the test)", paused.state);
    await page.click("button:has-text('Apply to full video') >> nth=0");
    await page.waitForSelector("[aria-label='Waiting for the earlier render']", { timeout: 20000 });
    const txt = await page.locator("[aria-label='Waiting for the earlier render']").innerText();
    check(/earlier export render \(paused\)/.test(txt), "the bar says what it is waiting for", txt);
    check(await page.locator("button:has-text('Stop the earlier render')").count() === 1, "there is a 'Stop the earlier render' button");
    await page.click("button:has-text('Stop the earlier render')");
    await page.waitForFunction(() => !document.querySelector("[aria-label='Waiting for the earlier render']"), null, { timeout: 20000 });
    check(true, "after stopping it, the waiting message goes away and the preview render starts");
    const f = await api("GET", `/api/jobs/${first.id}`);
    check(f.state === "cancelled", "the earlier render was cancelled", f.state);
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall job-queue checks passed");
})().catch(e => { console.error(e); process.exit(1); });
