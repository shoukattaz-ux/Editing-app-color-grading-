// End-to-end: key QC views (matte / edge band / on colour) and hand-drawn garbage / keep masks, against the real engine.
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
// brightness (0..255) of the key-view picture at a fraction of its width / height
const sample = (page, fx, fy) => page.evaluate(([fx, fy]) => new Promise(res => {
  const img = document.querySelector('img[alt="Key view"]'); const c = document.createElement("canvas"); c.width = img.naturalWidth; c.height = img.naturalHeight;
  const x = c.getContext("2d"); x.drawImage(img, 0, 0); const d = x.getImageData(Math.floor(fx * c.width), Math.floor(fy * c.height), 1, 1).data; res([d[0], d[1], d[2]]);
}), [fx, fy]);

(async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-k-")), video = path.join(work, "gs.mp4");
  execFileSync(process.env.EVP_PYTHON || "python3", ["-c", `import sys; sys.path.insert(0, ${JSON.stringify(BACKEND)}); from evp.samples import make_greenscreen; make_greenscreen(${JSON.stringify(video)}, 2)`]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  const H = { "x-evp-token": be.token }, base = `http://127.0.0.1:${be.port}`;
  const ops = async () => (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint.operations;
  const key = page.locator('select[aria-label="Key view"]');
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', video); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    check(await key.isDisabled(), "the Key view menu is disabled until there is a key");
    await page.click("button[aria-label=Effects]"); await page.click(".side >> text=Background Removal"); await page.waitForTimeout(500);
    await page.click('button[role=tab]:has-text("Edited")');
    check(await key.isEnabled(), "with a key and the Edited view it is enabled");
    await key.selectOption("alpha"); await page.waitForSelector('img[alt="Key view"]');
    await page.waitForFunction(() => { const i = document.querySelector('img[alt="Key view"]'); return i && i.complete && i.naturalWidth > 0; });
    const subj = await sample(page, 0.61, 0.5), green = await sample(page, 0.03, 0.05);
    check(Math.min(...subj) > 235 && Math.max(...green) < 20, "Matte view: the subject is white, the removed screen is black", `${subj} | ${green}`);
    await key.selectOption("red"); await page.waitForTimeout(900);
    const red = await sample(page, 0.03, 0.05); check(red[0] > 200 && red[1] < 60, "On red: the removed screen shows red", String(red));
    await key.selectOption("edge"); await page.waitForTimeout(900);
    check(Math.max(...(await sample(page, 0.61, 0.5))) < 20, "Edge band: solid areas are black (only the soft edge is lit)");
    await key.selectOption("alpha"); await page.waitForTimeout(900);
    // ---- garbage mask
    await page.click("button[aria-label=Effects]"); await page.click(".side button.item:has-text(\"Garbage / Keep Mask\")"); await page.waitForSelector(".masktools");
    check(await page.locator("svg.mask-overlay").count() === 1, "adding a mask shows a shape you can drag on the picture");
    await page.waitForTimeout(900);
    const m0 = (await ops()).find(o => o.type === "matte_mask");
    check(m0 && m0.end >= 1.9, "a new mask covers the whole clip", `${m0 && m0.start}-${m0 && m0.end}`);
    await page.click(".masktools .chip:has-text('Centre')"); await page.waitForTimeout(1200);
    const cut = await sample(page, 0.5, 0.5);
    check(Math.max(...cut) < 20, "a garbage mask over the subject cuts it out of the matte (live)", String(cut));
    // drag the shape
    const body = page.locator("svg.mask-overlay rect").first(); const bb = await body.boundingBox();
    const x0 = (await ops()).find(o => o.type === "matte_mask").parameters.x;
    await page.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2); await page.mouse.down(); await page.mouse.move(bb.x + bb.width / 2 + 60, bb.y + bb.height / 2 + 10, { steps: 6 }); await page.mouse.up();
    await page.waitForTimeout(900);
    const m1 = (await ops()).find(o => o.type === "matte_mask");
    check(m1.parameters.x > x0 + 0.02, "dragging the shape on the picture moves the mask", `x ${x0} -> ${m1.parameters.x}`);
    // resize with a handle
    const handle = page.locator("svg.mask-overlay rect").nth(4); const hb = await handle.boundingBox(), w0 = m1.parameters.width;
    await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2); await page.mouse.down(); await page.mouse.move(hb.x + hb.width / 2 - 40, hb.y + hb.height / 2, { steps: 5 }); await page.mouse.up();
    await page.waitForTimeout(900);
    const m2 = (await ops()).find(o => o.type === "matte_mask"); check(m2.parameters.width < w0 - 0.01, "dragging a handle resizes it", `width ${w0} -> ${m2.parameters.width}`);
    // keep + polygon
    await page.click(".masktools button:has-text('Keep')"); await page.click(".masktools button:text-is('Polygon')"); await page.waitForTimeout(900);
    let m3 = (await ops()).find(o => o.type === "matte_mask");
    check(m3.parameters.mode === "keep" && m3.parameters.shape === "polygon" && m3.parameters.polygon.length === 3, "Keep mode and a polygon (3 corners to start)");
    const ov = await page.locator("svg.mask-overlay").boundingBox();
    await page.mouse.click(ov.x + ov.width * 0.2, ov.y + ov.height * 0.8); await page.waitForTimeout(900);
    m3 = (await ops()).find(o => o.type === "matte_mask"); check(m3.parameters.polygon.length === 4, "clicking the picture adds a corner", String(m3.parameters.polygon.length));
    if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT });
    // zoom beside the normal picture
    await page.click("button[aria-label=Effects]");
    await page.selectOption('select[aria-label="Key view zoom"]', "2"); await page.waitForSelector(".loupe");
    check(/NORMAL/.test(await page.locator(".loupe").innerText()) && /MATTE/.test(await page.locator(".loupe").innerText()), "zoom shows the normal picture beside the matte");
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall key-tools checks passed");
})().catch(e => { console.error(e); process.exit(1); });
