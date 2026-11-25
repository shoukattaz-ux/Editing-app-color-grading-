// End-to-end: the Light pattern card on a Studio Look (preset button, sliders, stored values) and the rendered picture.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-l-")), shot = path.join(work, "gs.mp4");
  execFileSync(process.env.EVP_PYTHON || "python3", ["-c", `import sys; sys.path.insert(0, ${JSON.stringify(BACKEND)}); from evp.samples import make_greenscreen; make_greenscreen(${JSON.stringify(shot)}, 2, size="640x360")`]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  const H = { "x-evp-token": be.token, "content-type": "application/json" }, base = `http://127.0.0.1:${be.port}`;
  const bpNow = async () => (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint;
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', shot); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    const bpj = { blueprint_version: "1.0", operations: [
      { id: "k", type: "background_removal", start: 0, parameters: { mode: "chroma", spill_suppression: false } },
      { id: "r", type: "background_replacement", start: 0, depends_on: ["k"], parameters: { color: "#3a5a80" } },
      { id: "look", type: "studio_look", start: 0, depends_on: ["r"], parameters: { preset: "none" } }] };
    await page.click("button[aria-label=Blueprint]"); await page.fill("textarea.bp-text", JSON.stringify(bpj)); await page.click(".side >> text=Execute Blueprint");
    await page.waitForTimeout(2000);
    await page.click(".timeline .clip.op:has-text('Studio Look')"); await page.waitForSelector(".lightpattern");
    check(/off/.test(await page.locator(".lightpattern .master-head").innerText()), "the Studio Look has a Light pattern card, off by default");
    await page.click(".lightpattern >> text=Underwater ripples"); await page.waitForTimeout(1000);
    let look = (await bpNow()).operations.find(o => o.type === "studio_look");
    check(look.parameters.light_pattern === 0.6 && look.parameters.light_pattern_scale === 1.3 && look.parameters.light_pattern_falloff === 0.55, "the preset sets strength, size and fall-off", JSON.stringify(look.parameters));
    check(await page.locator('input[aria-label="Pattern size"]').count() === 1 && await page.locator('input[aria-label="Speed"]').count() === 1, "size and speed sliders appear");
    await page.locator('input[aria-label="Speed"]').evaluate(el => { const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; s.call(el, "2"); el.dispatchEvent(new Event("input", { bubbles: true })); });
    await page.waitForTimeout(900); look = (await bpNow()).operations.find(o => o.type === "studio_look"); check(look.parameters.light_pattern_speed === 2, "the speed slider stores its value");
    await page.click('.lightpattern button:has-text("Particles")'); await page.waitForTimeout(700);
    await page.click('.lightpattern button:has-text("Solid")'); await page.waitForTimeout(700);
    look = (await bpNow()).operations.find(o => o.type === "studio_look");
    check(look.parameters.light_pattern_style === "solid", "the style buttons store the chosen style");
    check(look.parameters.light_pattern_on === "subject", "apply-to defaults to subject only");
    await page.click('.lightpattern button:has-text("Both")'); await page.waitForTimeout(700);
    check((await bpNow()).operations.find(o => o.type === "studio_look").parameters.light_pattern_on === "both", "apply-to can be switched");
    await page.click('.lightpattern button:has-text("Subject only")'); await page.click('.lightpattern button:has-text("Ripples")'); await page.waitForTimeout(700);
    await page.locator('input[aria-label="Light colour"]').evaluate(el => { const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; s.call(el, "#ff8800"); el.dispatchEvent(new Event("input", { bubbles: true })); });
    await page.waitForTimeout(700);
    check((await bpNow()).operations.find(o => o.type === "studio_look").parameters.light_pattern_color === "#ff8800", "the light colour picker stores its colour");
    check(await page.locator('input[aria-label="Spread (softness)"]').count() === 1 && await page.locator('input[aria-label="Bend (warp)"]').count() === 1, "spread and bend sliders appear");
    // the render: the subject varies, the background does not
    const stats = await page.evaluate(async ([base, token, bp]) => {
      const f = async (b) => { const r = await fetch(base + "/api/preview/frame", { method: "POST", headers: { "x-evp-token": token, "content-type": "application/json" }, body: JSON.stringify({ blueprint: b, t: 1, width: 640 }) });
        const bmp = await createImageBitmap(await r.blob()); const c = document.createElement("canvas"); c.width = bmp.width; c.height = bmp.height; const x = c.getContext("2d"); x.drawImage(bmp, 0, 0);
        const sd = (x0, y0, w, h) => { const d = x.getImageData(x0, y0, w, h).data; let m = 0, n = 0; for (let i = 0; i < d.length; i += 4) { m += d[i]; n++; } m /= n; let v = 0; for (let i = 0; i < d.length; i += 4) v += (d[i] - m) ** 2; return Math.sqrt(v / n); };
        return [sd(360, 140, 60, 80), sd(40, 280, 120, 50)]; };
      const off = JSON.parse(JSON.stringify(bp)); delete off.operations.find(o => o.type === "studio_look").parameters.light_pattern;
      return [await f(off), await f(bp)]; }, [base, be.token, await bpNow()]);
    check(stats[0][0] < 3 && stats[1][0] > 10, "the rendered subject is rippled (it is flat without)", `${stats[0][0].toFixed(1)} -> ${stats[1][0].toFixed(1)}`);
    check(Math.abs(stats[0][1] - stats[1][1]) < 1, "the background is unchanged");
    await page.click(".lightpattern button:has-text('Off')"); await page.waitForTimeout(900);
    check((await bpNow()).operations.find(o => o.type === "studio_look").parameters.light_pattern === 0, "Off turns it off");
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall light-pattern checks passed");
})().catch(e => { console.error(e); process.exit(1); });
