// End-to-end: many coloured-light boxes: add, drag on the preview, edit colour/side, delete; stored in the blueprint.
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
    const zones = async () => (await bpNow()).operations.find(o => o.type === "studio_look").parameters.colour_zones ?? [];
    await page.click(".colourlights >> text=Cyan / magenta portrait"); await page.waitForTimeout(800);
    check((await zones()).length === 0, "no boxes to start with");
    await page.click(".colourlights >> text=Add a box"); await page.click(".colourlights >> text=Add a box"); await page.waitForTimeout(900);
    check((await zones()).length === 2, "two boxes were added", String((await zones()).length));
    check(await page.locator(".cl-tab").count() === 2, "each box has its own tab");
    check(await page.locator("[aria-label='Light box 1']").count() === 1 && await page.locator("[aria-label='Light box 2']").count() === 1, "both boxes are drawn on the preview");
    const before = (await zones())[0].x0;
    const r = await page.locator("[aria-label='Light box 1']").boundingBox();
    await page.mouse.move(r.x + r.width / 2, r.y + r.height / 2); await page.mouse.down(); await page.mouse.move(r.x + r.width / 2 + 60, r.y + r.height / 2 + 5, { steps: 6 }); await page.mouse.up();
    await page.waitForTimeout(900);
    check((await zones())[0].x0 > before + 0.02, "dragging a box on the preview moves it", `${before} -> ${(await zones())[0].x0}`);
    await page.click(".cl-tab >> nth=1"); await page.selectOption("[aria-label='Box 2 side']", "top"); await page.waitForTimeout(700);
    check((await zones())[1].side === "top" && (await zones())[0].side !== "top", "a box's side is set on its own", JSON.stringify((await zones()).map(z => z.side)));
    await page.fill("[aria-label='Box 2 colour']", "#00ff00"); await page.waitForTimeout(700);
    check((await zones())[1].color === "#00ff00", "a box's colour is set on its own");
    await page.check("[aria-label='Box 2 follow the subject']"); await page.waitForTimeout(700);
    check((await zones())[1].follow === true && (await zones())[0].follow === false, "a box can be set to follow the subject, on its own", JSON.stringify((await zones()).map(z => z.follow)));
    check(await page.locator("[aria-label='Box 2 grow and shrink with the subject']").isEnabled(), "the sizing tick is available once the box follows");
    await page.check("[aria-label='Box 2 grow and shrink with the subject']"); await page.waitForTimeout(700);
    check((await zones())[1].scale === true && (await zones())[0].scale === false, "a box can be set to grow and shrink with the subject, on its own");
    await page.click(".cl-tab >> nth=0"); check(await page.locator("[aria-label='Box 1 grow and shrink with the subject']").isDisabled(), "the sizing tick is off-limits until Follow is ticked"); await page.click(".cl-tab >> nth=1");
    await page.click(".colourlights button:has-text('Boxes only')"); await page.waitForTimeout(2500);
    const sl = (await bpNow()).operations.find(o => o.type === "studio_look").parameters;
     check(sl.colour_light_side_a === "off" && sl.colour_light_side_b === "off", "Boxes only switches Light A and B off");
    await page.click(".colourlights >> text=Delete this box"); await page.waitForTimeout(700);
    check((await zones()).length === 1, "a box can be deleted");
    for (let i = 0; i < 6; i++) { if (await page.locator(".colourlights >> text=Add a box").isEnabled()) await page.click(".colourlights >> text=Add a box"); }
    await page.waitForTimeout(700);
    check((await zones()).length === 6 && await page.locator(".colourlights >> text=Add a box").isDisabled(), "six boxes is the limit", String((await zones()).length));
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall colour-box checks passed");
})().catch(e => { console.error(e); process.exit(1); });
