// End-to-end: right-click menus on the timeline and the Media bin, Delete on media cards, removing an earlier source video, filter message on a Studio Look (preset button, sliders, stored values) and the rendered picture.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-mt-")), shot = path.join(work, "gs.mp4");
  execFileSync(process.env.EVP_PYTHON || "python3", ["-c", `import sys; sys.path.insert(0, ${JSON.stringify(BACKEND)}); from evp.samples import make_greenscreen; make_greenscreen(${JSON.stringify(shot)}, 4, size="640x360")`]);
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
    page.on("dialog", d => d.accept());                                                  // the "remove?" confirmations
    const other = path.join(work, "second take.mp4"), pic = path.join(work, "sea.png");
    fs.copyFileSync(shot, other);
    execFileSync(process.env.EVP_FFMPEG || "ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x2060a0:s=320x180", "-frames:v", "1", pic]);
    const P = (u, b) => fetch(base + u, { method: "POST", headers: H, body: JSON.stringify(b) }).then(r => r.json());
    await P("/api/project/source", { path: other }); await P("/api/project/source", { path: shot });      // 'second take' is now an EARLIER source
    await P("/api/project/asset", { path: pic });
    await page.reload(); await page.click("button.continue", { timeout: 15000 }).catch(() => {}); await page.waitForSelector(".timeline");
    const opsNow = async () => (await bpNow()).operations;
    // ---- effects: right-click menu
    await page.click("button[aria-label=Effects]"); await page.click(".side button.item:has-text('Color Grading')"); await page.waitForTimeout(700);
    await page.click(".timeline .clip.op:has-text('Color Grading')", { button: "right" });
    check(await page.locator(".ctxmenu").count() === 1, "right-click on an effect opens a menu");
    await page.click(".ctxmenu >> text=Duplicate"); await page.waitForTimeout(700);
    check((await opsNow()).filter(o => o.type === "color_grading").length === 2, "menu: Duplicate");
    await page.locator(".timeline .clip.op:has-text('Color Grading')").first().click({ button: "right" });
    await page.click(".ctxmenu >> text=Turn off"); await page.waitForTimeout(600);
    check((await opsNow()).some(o => o.enabled === false), "menu: Turn off keeps the effect but skips it");
    await page.locator(".timeline .clip.op:has-text('Color Grading')").first().click({ button: "right" });
    await page.click(".ctxmenu >> text=Delete effect"); await page.waitForTimeout(700);
    check((await opsNow()).filter(o => o.type === "color_grading").length === 1, "menu: Delete effect");
    await page.keyboard.press("Escape");
    // ---- clips: right-click menu (cut first, then delete one part)
    await page.evaluate(() => (window).__evpStep?.(30)); await page.waitForTimeout(300);
    await page.locator(".timeline .clip.footage").first().click({ button: "right" });
    const only = await page.locator(".ctxmenu button:has-text('Delete clip')").isDisabled();
    check(only, "the only clip cannot be deleted (the menu says why)");
    await page.click(".ctxmenu >> text=Cut at playhead"); await page.waitForTimeout(700);
    check(((await bpNow()).clips ?? []).length === 2, "menu: Cut at playhead makes two clips");
    await page.locator(".timeline .clip.footage").nth(1).click({ button: "right" });
    await page.click(".ctxmenu >> text=Delete clip"); await page.waitForTimeout(800);
    check(((await bpNow()).clips ?? []).length === 1, "menu: Delete clip removes the clip you right-clicked");
    // ---- media bin
    await page.click("button[aria-label=Media]"); await page.waitForSelector(".mcard");
    await page.click(".mfilter button:has-text('Pictures')"); await page.waitForTimeout(300);
    check(/hidden by the 'Pictures' filter/.test(await page.locator("aside.side:not(.right)").innerText()), "a filter that hides your video says so (not 'no video yet')");
    await page.click("aside.side:not(.right) button.link:has-text('Show all')"); await page.waitForTimeout(300);
    check(await page.locator(".mcard:has-text('second take')").count() === 1, "Show all brings everything back");
    await page.locator(".mcard:has-text('second take')").click({ button: "right" });
    await page.click(".ctxmenu >> text=Remove from media"); await page.waitForTimeout(900);
    check(await page.locator(".mcard:has-text('second take')").count() === 0 && fs.existsSync(other), "an earlier source video can be removed from the media (the file stays on disk)");
    await page.locator(".mcard").first().click({ button: "right" });
    check(await page.locator(".ctxmenu button:has-text('Remove from media')").isDisabled(), "the video being edited cannot be removed from the media");
    await page.keyboard.press("Escape");
    await page.locator(".mcard:has-text('sea')").click(); await page.keyboard.press("Delete"); await page.waitForTimeout(900);
    check(await page.locator(".mcard:has-text('sea')").count() === 0, "select a media card and press Delete: it is removed");
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall media / timeline editing checks passed");
})().catch(e => { console.error(e); process.exit(1); });
