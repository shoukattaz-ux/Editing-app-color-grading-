// End-to-end: the Master amount slider in the Properties panel of colour grading / studio look / background removal, and the Strength of pasted blueprints.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-a-")), video = path.join(work, "v.mp4");
  execFileSync(process.env.EVP_FFMPEG || "ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=3", "-pix_fmt", "yuv420p", video]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  const H = { "x-evp-token": be.token }, base = `http://127.0.0.1:${be.port}`;
  const ops = async () => (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint.operations;
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', video); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    await page.click("button[aria-label=Color]"); await page.click("text=Blank grade"); await page.waitForSelector(".wheel");
    check(await page.locator(".master").count() === 1 && /master amount/i.test(await page.locator(".master").innerText()), "a colour grade shows the Master amount block");
    check(await page.locator("h4:text-is('Fine-tune')").count() === 1, "and the individual sliders sit below it as 'Fine-tune'");
    check(await page.locator(".props .pf >> text=Amount").count() === 0, "the amount is not repeated among the fine-tune sliders");
    await page.fill(".master .pct input", "50"); await page.waitForTimeout(900);
    let g = (await ops()).find(o => o.type === "color_grading");
    check(g.parameters.amount === 0.5, "typing 50 % sets amount 0.5", String(g.parameters.amount));
    await page.locator(".master input[type=range]").evaluate(el => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; set.call(el, "130"); el.dispatchEvent(new Event("input", { bubbles: true })); });
    await page.waitForTimeout(900);
    g = (await ops()).find(o => o.type === "color_grading"); check(Math.abs(g.parameters.amount - 1.3) < 1e-9, "the slider goes above 100 % (130 %)", String(g.parameters.amount));
    await page.dblclick(".master input[type=range]"); await page.waitForTimeout(900);
    g = (await ops()).find(o => o.type === "color_grading"); check(g.parameters.amount === 1, "double-click resets to 100 %");
    if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT });
    // studio look + background removal + (none for video enhancement)
    await page.click("button[aria-label=Effects]"); await page.click(".side >> text=Studio Look"); await page.waitForSelector(".master");
    check(await page.locator(".master").count() === 1, "a Studio Look shows the Master amount block");
    await page.click(".side >> text=Background Removal"); await page.waitForSelector(".master");
    check(/original footage \(nothing removed\)/.test(await page.locator(".master").innerText()), "Background Removal shows it too, explained for keying");
    check(await page.locator(".master input[type=range]").getAttribute("max") === "100", "a key goes from 0 to 100 % (no 150 %)");
    await page.click(".side >> text=Video Enhancement"); await page.waitForTimeout(400);
    check(await page.locator(".master").count() === 0, "effects without an amount have no master block");
    // live preview follows the slider: the engine frame changes with the amount
    const frameAt = async (amount) => { const bp = (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint;
      bp.operations = bp.operations.filter(o => o.type === "color_grading").map(o => ({ ...o, parameters: { ...o.parameters, saturation: 2, contrast: 1.4, amount } }));
      return Buffer.from(await (await fetch(`${base}/api/preview/frame`, { method: "POST", headers: { ...H, "content-type": "application/json" }, body: JSON.stringify({ blueprint: bp, t: 1, width: 320 }) })).arrayBuffer()); };
    const f0 = await frameAt(0), f1 = await frameAt(1), f2 = await frameAt(0.5);
    const orig = Buffer.from(await (await fetch(`${base}/api/preview/frame`, { method: "POST", headers: { ...H, "content-type": "application/json" }, body: JSON.stringify({ blueprint: { blueprint_version: "1.0", operations: [] }, t: 1, width: 320 }) })).arrayBuffer());
    check(f0.equals(orig) && !f1.equals(orig) && !f2.equals(f1) && !f2.equals(f0), "the rendered frame at 0 % is the original, at 50 % and 100 % it differs");
    // blueprint tab: strength
    await page.click("button[aria-label=Blueprint]");
    await page.fill("textarea.bp-text", JSON.stringify({ blueprint_version: "1.0", operations: [{ id: "bold", type: "color_grading", start: 0, parameters: { saturation: 2 } }] }));
    await page.locator(".strength input[type=range]").evaluate(el => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; set.call(el, "40"); el.dispatchEvent(new Event("input", { bubbles: true })); });
    await page.click(".side >> text=Execute Blueprint"); await page.waitForTimeout(1500);
    const bold = (await ops()).find(o => o.id === "bold");
    check(bold && Math.abs(bold.parameters.amount - 0.4) < 1e-9 && bold.parameters.saturation === 2, "a pasted blueprint is applied at the chosen Strength (40 %), its own values kept", JSON.stringify(bold && bold.parameters));
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall master amount checks passed");
})().catch(e => { console.error(e); process.exit(1); });
