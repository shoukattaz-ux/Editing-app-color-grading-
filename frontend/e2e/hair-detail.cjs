// End-to-end: the Hair detail card on a screen key (controls and stored values; the AI model itself is not run here).
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-h-")), shot = path.join(work, "gs.mp4");
  execFileSync(process.env.EVP_PYTHON || "python3", ["-c", `import sys; sys.path.insert(0, ${JSON.stringify(BACKEND)}); from evp.samples import make_greenscreen; make_greenscreen(${JSON.stringify(shot)}, 2)`]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  const H = { "x-evp-token": be.token }, base = `http://127.0.0.1:${be.port}`;
  const key = async () => (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint.operations.find(o => o.type === "background_removal");
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', shot); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    await page.click("button[aria-label=Effects]"); await page.click(".side button.item:has-text('Background Removal')"); await page.waitForTimeout(400);
    check(await page.locator(".hairdetail").count() === 0, "Hair detail appears only for the screen key");
    await page.selectOption(".props select >> nth=0", "screen"); await page.waitForSelector(".hairdetail");
    check(/off/.test(await page.locator(".hairdetail .master-head").innerText()) && /band around the subject's edge/.test(await page.locator(".hairdetail").innerText()), "the card explains it and starts off");
    await page.click(".hairdetail >> text=Turn on"); await page.waitForTimeout(900);
    let k = await key(); check(k.parameters.hair_detail === 0.7, "Turn on sets 70 %", String(k.parameters.hair_detail));
    check(await page.locator('input[aria-label="Hair band width"]').count() === 1, "the band width slider appears");
    await page.locator('input[aria-label="Hair band width"]').evaluate(el => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; set.call(el, "30"); el.dispatchEvent(new Event("input", { bubbles: true })); });
    await page.waitForTimeout(900); k = await key(); check(k.parameters.hair_width === 30, "the band width is stored", String(k.parameters.hair_width));
    await page.click(".hairdetail button:has-text('Off')"); await page.waitForTimeout(900);
    k = await key(); check(k.parameters.hair_detail === 0, "Off turns it off");
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall hair-detail checks passed");
})().catch(e => { console.error(e); process.exit(1); });
