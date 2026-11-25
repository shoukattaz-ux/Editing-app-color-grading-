// End-to-end: work, kill the engine abruptly (no save, no clean exit), start again, and the project is in Recent and restored; GPU chip is visible.
//   run: npm run e2e:restore      (needs the same prerequisites as the other e2e)
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-r-")), data = path.join(work, "data");
  const video = path.join(work, "v.mp4");
  execFileSync(process.env.EVP_FFMPEG || "ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=3", "-pix_fmt", "yuv420p", video]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const web = await serveDist(), browser = await chromium.launch();
  const errs = [];
  // ---- session 1: create, edit, never save
  let be = await startBackend(data);
  let page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  page.on("pageerror", e => errs.push(e.message));
  await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
  await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', video); await page.keyboard.press("Enter");
  await page.waitForSelector(".timeline");
  await page.waitForSelector(".gpuchip");
  await page.waitForFunction(() => !document.querySelector(".gpuchip").textContent.includes("…"), null, { timeout: 8000 });
  const chip = await page.locator(".gpuchip").innerText();
  check(/^GPU/.test(chip), "a GPU indicator is always visible in the toolbar", chip);
  check(await page.locator(".gpuchip").getAttribute("title") !== null && (await page.locator(".gpuchip").getAttribute("title")).length > 20, "it explains itself on hover");
  let renders = 0; page.on("request", r => { if (r.method() === "POST" && r.url().endsWith("/api/render")) renders++; });
  await page.click('button[role=tab]:has-text("Edited")');                           // live preview, never a button to press
  await page.click("button[aria-label=Color]"); await page.click("text=Blank grade"); await page.waitForSelector(".wheel");
  check(await page.locator(".banner").count() === 0, "no blocking 'Update preview video' banner over the picture");
  await page.waitForSelector("video.media", { timeout: 30000 });
  await page.waitForFunction(() => !document.querySelector(".chip-top"), null, { timeout: 30000 });
  check(renders >= 1, "the preview video was re-rendered by itself after the edit (no click)", `${renders} render request(s)`);
  // undo / redo right above the timeline: step back and forward through the edit, with a description of each step
  const apiOps = async () => (await (await fetch(`http://127.0.0.1:${be.port}/api/project`, { headers: { "x-evp-token": be.token } })).json()).blueprint.operations.length;
  check(await page.locator(".tl-bar .undo-group button[aria-label=Undo]").isEnabled() && !(await page.locator(".tl-bar .undo-group button[aria-label=Redo]").isEnabled()), "Undo / Redo sit in the timeline bar: undo is available, redo is not yet");
  check(/Undo: Add Color Grading/.test(await page.locator("button[aria-label=Undo]").first().getAttribute("title")), "the Undo tooltip names the step it will undo", await page.locator("button[aria-label=Undo]").first().getAttribute("title"));
  await page.click(".tl-bar button[aria-label=Undo]"); await page.waitForTimeout(900);
  check(await apiOps() === 0 && await page.locator("text=Undid: Add Color Grading").count() === 1, "Undo removes the effect and says what it undid");
  check(await page.locator(".tl-bar button[aria-label=Redo]").isEnabled(), "Redo is now available");
  await page.click(".tl-bar button[aria-label=Redo]"); await page.waitForTimeout(900);
  check(await apiOps() === 1, "Redo brings it back");
  await page.waitForTimeout(1500);                                                    // auto-save debounce, no Save click
  check(/Auto-saved/.test(await page.locator(".autosave").innerText()), "the toolbar says the work is auto-saved", await page.locator(".autosave").innerText());
  const saved = JSON.parse(fs.readFileSync(fs.readdirSync(path.join(work, "proj")).map(n => path.join(work, "proj", n, "project.json"))[0], "utf8"));
  check(saved.blueprint.operations.length === 0, "nothing was saved with the Save button (the file on disk is still the empty version)");
  be.proc.kill("SIGKILL"); await page.close();                                         // abrupt end: no clean shutdown, no flush
  // ---- session 2: fresh engine, same data folder
  be = await startBackend(data);
  page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  page.on("pageerror", e => errs.push(e.message));
  await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
  await page.waitForSelector(".recent-list .item");
  check(await page.locator(".recent-list .item").count() === 1, "the project is listed under Recent projects");
  check(await page.locator(".recent-list .badge").count() === 1, "and is marked as holding auto-saved work");
  check(await page.locator(".continue").count() === 1, "there is a one-click 'Continue where you left off'");
  await page.click(".continue"); await page.waitForSelector(".timeline");
  await page.waitForSelector(".recovery");
  check(/Restored your work exactly as you left it/.test(await page.locator(".recovery").innerText()), "it says the work was restored");
  const H = { "x-evp-token": be.token };
  const cur = await (await fetch(`http://127.0.0.1:${be.port}/api/project`, { headers: H })).json();
  check(cur.blueprint.operations.length === 1 && cur.blueprint.operations[0].type === "color_grading", "the colour-grade operation is back");
  check(await page.locator("button[aria-label=Color].on").count() === 1, "the same panel (Color) is open as when it was left");
  await page.click("text=Back to last saved version"); await page.waitForTimeout(400);
  check((await (await fetch(`http://127.0.0.1:${be.port}/api/project`, { headers: H })).json()).blueprint.operations.length === 0, "going back to the last saved version works");
  check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  await browser.close(); be.proc.kill(); web.close();
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall restore / GPU checks passed");
})().catch(e => { console.error(e); process.exit(1); });
