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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-cl-")), shot = path.join(work, "gs.mp4");
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
    await page.click(".timeline .clip.op:has-text('Studio Look')"); await page.waitForSelector(".colourlights");
    check(/off/.test(await page.locator(".colourlights .master-head").innerText()), "the Studio Look has a Colour lights card, off by default");
    await page.click(".colourlights >> text=Cyan / magenta portrait"); await page.waitForTimeout(1000);
    let look = (await bpNow()).operations.find(o => o.type === "studio_look");
    check(look.parameters.colour_light === 0.8 && look.parameters.colour_light_color_a === "#19b8ff" && look.parameters.colour_light_side_b === "right", "the preset sets strength, colours and sides", JSON.stringify(look.parameters));
    await page.locator('select[aria-label="Light A side"]').selectOption("top"); await page.waitForTimeout(800);
    check((await bpNow()).operations.find(o => o.type === "studio_look").parameters.colour_light_side_a === "top", "a light's side can be changed");
    await page.locator('select[aria-label="Light A side"]').selectOption("left");
    await page.locator('input[aria-label="Light B colour"]').evaluate(el => { const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; s.call(el, "#ff2200"); el.dispatchEvent(new Event("input", { bubbles: true })); });
    await page.waitForTimeout(800);
    check((await bpNow()).operations.find(o => o.type === "studio_look").parameters.colour_light_color_b === "#ff2200", "a light's colour can be changed");
    await page.click('.colourlights button:has-text("Follow the subject")'); await page.waitForTimeout(700);
    check((await bpNow()).operations.find(o => o.type === "studio_look").parameters.colour_light_place === "follow", "Follow the subject is stored");
    check(await page.locator("svg.zone-overlay").count() === 0, "no zone rectangle unless My own spot is chosen");
    await page.click('.colourlights button:has-text("My own spot")'); await page.waitForSelector("svg.zone-overlay rect[aria-label='Light zone']"); await page.waitForTimeout(800);
    check((await bpNow()).operations.find(o => o.type === "studio_look").parameters.colour_light_place === "zone", "My own spot is stored");
    const zr = page.locator("svg.zone-overlay rect[aria-label='Light zone']"); const bb = await zr.boundingBox();
    await page.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2); await page.mouse.down(); await page.mouse.move(bb.x + bb.width / 2 + 60, bb.y + bb.height / 2 + 10, { steps: 6 }); await page.mouse.up(); await page.waitForTimeout(700);
    let lp = (await bpNow()).operations.find(o => o.type === "studio_look").parameters;
    check(lp.colour_light_zone_x0 > 0.25 + 0.02 && Math.abs((lp.colour_light_zone_x1 - lp.colour_light_zone_x0) - 0.5) < 0.01, "dragging the rectangle moves the zone without resizing it", JSON.stringify([lp.colour_light_zone_x0, lp.colour_light_zone_x1]));
    const hs = await page.locator("svg.zone-overlay rect[style*='nwse-resize']").first().boundingBox();
    await page.mouse.move(hs.x + 5, hs.y + 5); await page.mouse.down(); await page.mouse.move(hs.x + 45, hs.y + 35, { steps: 6 }); await page.mouse.up(); await page.waitForTimeout(700);
    const lp2 = (await bpNow()).operations.find(o => o.type === "studio_look").parameters;
    check(lp2.colour_light_zone_x0 > lp.colour_light_zone_x0 + 0.02 && lp2.colour_light_zone_y0 > (lp.colour_light_zone_y0 ?? 0.1) + 0.02 && lp2.colour_light_zone_x1 === lp.colour_light_zone_x1, "dragging a handle resizes it", JSON.stringify(lp2));
    await page.click('.colourlights button:has-text("Fixed")'); await page.waitForTimeout(700);
    const stats = await page.evaluate(async ([base, token, bp]) => {
      const f = async (b) => { const r = await fetch(base + "/api/preview/frame", { method: "POST", headers: { "x-evp-token": token, "content-type": "application/json" }, body: JSON.stringify({ blueprint: b, t: 1, width: 640 }) });
        const bmp = await createImageBitmap(await r.blob()); const c = document.createElement("canvas"); c.width = bmp.width; c.height = bmp.height; const x = c.getContext("2d"); x.drawImage(bmp, 0, 0);
        const px = (x0, y0) => Array.from(x.getImageData(x0, y0, 1, 1).data).slice(0, 3); return [px(380, 180), px(60, 300)]; };
      const off = JSON.parse(JSON.stringify(bp)); off.operations.find(o => o.type === "studio_look").parameters.colour_light = 0;
      return [await f(off), await f(bp)]; }, [base, be.token, await bpNow()]);
    const dSub = stats[1][0].map((v, i) => Math.abs(v - stats[0][0][i])), dBg = stats[1][1].map((v, i) => Math.abs(v - stats[0][1][i]));
    check(Math.max(...dSub) > 8, "the subject is lit", JSON.stringify(dSub)); check(Math.max(...dBg) <= 2, "the background is unchanged", JSON.stringify(dBg));
    await page.click(".colourlights button:has-text('Off')"); await page.waitForTimeout(900);
    check((await bpNow()).operations.find(o => o.type === "studio_look").parameters.colour_light === 0, "Off turns it off");
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall colour-lights checks passed");
})().catch(e => { console.error(e); process.exit(1); });
