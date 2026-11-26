// End-to-end: everyday usability - Esc closes dialogs, a new effect is shown, one background at a time, clear status texts on a Studio Look (preset button, sliders, stored values) and the rendered picture.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-ux-")), shot = path.join(work, "gs.mp4");
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
    await page.waitForFunction(() => !/Checking/.test(document.querySelector("footer.status")?.textContent ?? ""), null, { timeout: 15000 });
    check(/No effects yet/.test(await page.locator("footer.status").innerText()), "an empty project says 'No effects yet', not a warning", await page.locator("footer.status").innerText());
    check(await page.locator(".chip-top").count() === 0, "nothing to apply while there are no effects");
    await page.click("button[aria-label=Effects]"); await page.click(".side button.item:has-text('Color Grading')"); await page.waitForTimeout(900);
    check(await page.locator('button[role=tab][aria-selected=true]').innerText() === "Edited", "adding an effect switches the preview to Edited, so it is visible");
    await page.click("button[aria-label=Backgrounds]"); await page.click("text=Solid colour background"); await page.waitForTimeout(600);
    await page.click("text=Solid colour background"); await page.waitForTimeout(900);
    const reps = (await bpNow()).operations.filter(o => o.type === "background_replacement");
    check(reps.length === 1, "adding a background twice changes the one there instead of stacking a conflicting second one", `${reps.length}`);
    check(!/problem/.test(await page.locator("footer.status").innerText()), "the project has no problems", await page.locator("footer.status").innerText());
    await page.click("header >> text=Export"); await page.waitForSelector(".modal"); await page.keyboard.press("Escape"); await page.waitForTimeout(300);
    check(await page.locator(".modal").count() === 0, "Esc closes the Export dialog");
    await page.click('header button[title^="Settings"]'); await page.waitForSelector(".modal"); await page.keyboard.press("Escape"); await page.waitForTimeout(300);
    check(await page.locator(".modal").count() === 0, "Esc closes Settings");
    await page.click("header >> text=Export"); await page.waitForSelector(".modal");
    const n0 = (await bpNow()).operations.length; await page.keyboard.press("Delete"); await page.waitForTimeout(600);
    check((await bpNow()).operations.length === n0, "keys do not reach the timeline behind an open dialog");
    await page.keyboard.press("Escape");
    check(!/\b(Db|S)\b$/.test((await page.locator("aside.right").innerText())), "no unit mangling like 'Gain Db DB'");
    // the applied preview video stays valid when an edit is undone back to the same content (it is compared by content, not by object)
    let renders = 0; page.on("request", r => { if (r.method() === "POST" && r.url().endsWith("/api/render")) renders++; });
    await page.click(".timeline .clip.op:has-text('Color Grading')"); await page.waitForSelector(".master input[type=range]");
    const setMaster = v => page.locator(".master input[type=range]").evaluate((el, v) => { const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; s.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); }, v);
    await setMaster("60"); await page.waitForTimeout(500);
    await page.click("header >> text=Apply to full video"); await page.waitForSelector("video.media", { timeout: 60000 });
    const r0 = renders;
    await setMaster("80"); await page.waitForTimeout(500);
    check(await page.locator("video.media").count() === 0, "a real change makes the preview video out of date (live frame shown)");
    await setMaster("60"); await page.waitForTimeout(800);
    check(await page.locator("video.media").count() === 1 && renders === r0, "changing it back makes the applied video valid again, with no new render", `${renders - r0} render(s)`);
    await page.click(".tl-bar button[aria-label=Undo]"); await page.waitForTimeout(500); await page.click(".tl-bar button[aria-label=Redo]"); await page.waitForTimeout(800);
    check(await page.locator("video.media").count() === 1, "undo + redo keeps it playable");
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall usability checks passed");
})().catch(e => { console.error(e); process.exit(1); });
