// End-to-end: floor reflection + contact shadow on a Background Replacement (controls, the measured floor line, and the rendered picture).
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-f-")), shot = path.join(work, "gs.mp4");
  execFileSync(process.env.EVP_PYTHON || "python3", ["-c", `import sys; sys.path.insert(0, ${JSON.stringify(BACKEND)}); from evp.samples import make_greenscreen; make_greenscreen(${JSON.stringify(shot)}, 2, size="640x360")`]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  const H = { "x-evp-token": be.token, "content-type": "application/json" }, base = `http://127.0.0.1:${be.port}`;
  const rep = async () => (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint.operations.find(o => o.type === "background_replacement");
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', shot); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    const bpj = { blueprint_version: "1.0", operations: [
      { id: "k", type: "background_removal", start: 0, parameters: { mode: "chroma", spill_suppression: false } },
      { id: "r", type: "background_replacement", start: 0, depends_on: ["k"], parameters: { color: "#8a9bb0" } }] };
    await page.click("button[aria-label=Blueprint]"); await page.fill("textarea.bp-text", JSON.stringify(bpj)); await page.click(".side >> text=Execute Blueprint");
    await page.waitForTimeout(2000);
    await page.click('button[role=tab]:has-text("Edited")');
    await page.click(".timeline .clip.op:has-text('Background Replacement')"); await page.waitForSelector(".floorfx");
    check(/off/.test(await page.locator(".floorfx .master-head").innerText()), "the Background Replacement has a Floor card, off by default");
    await page.locator('input[aria-label="Reflection"]').evaluate(el => { const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; s.call(el, "70"); el.dispatchEvent(new Event("input", { bubbles: true })); });
    await page.locator('input[aria-label="Contact shadow"]').evaluate(el => { const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; s.call(el, "60"); el.dispatchEvent(new Event("input", { bubbles: true })); });
    await page.waitForTimeout(1000);
    const r = await rep();
    check(Math.abs(r.parameters.reflection - 0.7) < 1e-9 && Math.abs(r.parameters.contact_shadow - 0.6) < 1e-9, "the sliders store the values", `${r.parameters.reflection} / ${r.parameters.contact_shadow}`);
    await page.waitForSelector(".floorfx .issue.ok", { timeout: 60000 });
    const txt = await page.locator(".floorfx .issue.ok").innerText();
    check(/Floor found at 7\d%/.test(txt), "the floor line was measured from the subject (the disc ends at 75 %)", txt);
    // the rendered frame really has a reflection under the disc
    const px = await page.evaluate(async ([base, token, bp]) => {
      const f = async (b) => { const r = await fetch(base + "/api/preview/frame", { method: "POST", headers: { "x-evp-token": token, "content-type": "application/json" }, body: JSON.stringify({ blueprint: b, t: 1, width: 640 }) });
        const bmp = await createImageBitmap(await r.blob()); const c = document.createElement("canvas"); c.width = bmp.width; c.height = bmp.height; const x = c.getContext("2d"); x.drawImage(bmp, 0, 0);
        return Array.from(x.getImageData(392, 285, 1, 1).data); };
      const on = await f(bp); const off = JSON.parse(JSON.stringify(bp)); const o = off.operations.find(x => x.type === "background_replacement"); delete o.parameters.reflection; delete o.parameters.contact_shadow;
      return [await f(off), on]; }, [base, be.token, (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint]);
    check(Math.abs(px[0][0] - px[1][0]) + Math.abs(px[0][1] - px[1][1]) + Math.abs(px[0][2] - px[1][2]) > 25, "the rendered picture differs just under the feet", `${px[0].slice(0, 3)} -> ${px[1].slice(0, 3)}`);
    await page.click(".floorfx >> text=Set the floor line by hand");
    await page.waitForSelector('input[aria-label="Floor line"]');
    await page.waitForTimeout(900);
    check(typeof (await rep()).parameters.floor_y === "number", "a floor line can be set by hand (stored as floor_y)");
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall floor-fx checks passed");
})().catch(e => { console.error(e); process.exit(1); });
