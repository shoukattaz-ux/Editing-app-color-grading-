// End-to-end: Settings > AI device > Test speed measures the AI on this computer and shows real numbers.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-s-"));
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop");
    await page.click("button[title^='Settings'], .welcome2 button[aria-label*='ettings']").catch(() => {});
    if (!(await page.locator("[aria-label='Speed test']").count())) {                       // the gear is in the editor header: open an empty project first
      await page.click("text=Empty project"); await page.waitForSelector(".timeline, .status", { timeout: 20000 });
      await page.click("button[title^='Settings']");
    }
    await page.waitForSelector("[aria-label='Speed test']", { timeout: 10000 });
    check(true, "Settings has a Test speed button in the AI device section");
    await page.click("[aria-label='Speed test'] button:has-text('Test speed')");
    await page.waitForSelector("[aria-label='Speed test'] .speedresult", { timeout: 240000 });
    const txt = await page.locator("[aria-label='Speed test'] .speedresult").innerText();
    check(/AI upscale \(Fast, 2x\): about [\d.]+ s per 720p frame/.test(txt), "the result shows the upscale speed", txt.slice(0, 120).replace(/\n/g, " "));
    check(/Face AI, .*: [\d.]+ s per face per frame/.test(txt), "the result shows the face AI speed per face");
    check(/estimates and warnings in the editor now use these numbers/.test(txt), "it says the estimates now use the numbers");
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall speed-test checks passed");
})().catch(e => { console.error(e); process.exit(1); });
