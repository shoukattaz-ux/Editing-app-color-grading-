// End-to-end: Photo mode. A photo opens, has no timeline/transport, shows a live preview, and Save photo writes a real image.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-p-")), photo = path.join(work, "pic.png"), dest = path.join(work, "Saved");
  fs.mkdirSync(dest);
  execFileSync(process.env.EVP_FFMPEG || "ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=400x300:d=1", "-frames:v", "1", photo]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', photo); await page.keyboard.press("Enter");
    await page.waitForSelector(".photostrip", { timeout: 30000 });
    check(true, "opening a photo shows the photo strip instead of the timeline");
    check(await page.locator(".timeline").count() === 0, "there is no timeline for a photo");
    check(await page.locator("button[aria-label='Scrub'], input[aria-label='Scrub']").count() === 0, "there is no scrub bar / playback for a photo");
    check(await page.locator("button:has-text('Apply to full video')").count() === 0, "'Apply to full video' is hidden for a photo");
    check((await page.locator(".photostrip").innerText()).includes("400 × 300"), "the strip shows the photo size");
    await page.waitForSelector("video.media, img.media", { timeout: 30000 });
    check(true, "the live preview picture appears");
    // the one-click "Fix an overexposed photo" look: needs the photo itself as a picture, and has a Strength slider
    const H = { "x-evp-token": be.token, "content-type": "application/json" }, base = `http://127.0.0.1:${be.port}`;
    await page.click("nav.rail button[aria-label='Ready-made']"); await page.waitForSelector(".rm-card");
    const card = page.locator(".rm-card", { hasText: "Fix an overexposed or hazy photo" });
    await card.locator("input[type=range]").evaluate((el, v) => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; set.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); }, "60");
    await card.locator("button:has-text('Add to project')").click(); await page.waitForSelector("text=Added 5 effects", { timeout: 20000 }); await page.waitForTimeout(2500);
    const bpj = (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint;
    const wall = bpj.operations.find(o => o.id === "wall" || (o.parameters || {}).apply_to === "background");
    check(bpj.assets.some(a => a.id === "orig" && /^assets\//.test(a.path)), "the photo itself was copied into the project as a picture", JSON.stringify(bpj.assets.map(a => a.path)));
    check(wall && Math.abs(wall.parameters.amount - 0.6) < 0.01, "the Strength slider set the corrections' amount to 60%", String(wall && wall.parameters.amount));
    await page.click("button:has-text('Save photo')"); await page.waitForSelector("[aria-label='Save photo']");
    await page.selectOption("[aria-label='Photo format']", "png");
    await page.click(".modal >> text=Choose folder…"); await page.fill('input[placeholder="Full path"]', dest); await page.keyboard.press("Enter");
    await page.waitForFunction((d) => document.querySelector("[aria-label='Photo folder']").value === d, dest);
    await page.fill("[aria-label='Photo file name']", "result");
    await page.click(".modal button:has-text('Save photo')"); await page.waitForSelector("text=Saved:", { timeout: 60000 });
    const out = path.join(dest, "result.png");
    const sig = fs.existsSync(out) ? fs.readFileSync(out).subarray(0, 4).toString("hex") : "";
    check(sig === "89504e47", "a real PNG was written to the chosen folder", sig);
    await page.click(".modal button:has-text('Save photo')"); await page.waitForFunction((d) => true, 0); await page.waitForTimeout(3000);
    check(fs.readdirSync(dest).includes("result (2).png"), "saving again does not overwrite (result (2).png)", fs.readdirSync(dest).join(", "));
    const shoot = path.join(work, "shoot"); fs.mkdirSync(shoot);
    for (const n of ["one.png", "two.png"]) fs.copyFileSync(photo, path.join(shoot, n));
    await page.click("[aria-label='Batch folder']"); await page.fill('input[placeholder="Full path"]', shoot); await page.keyboard.press("Enter");
    await page.waitForFunction((d) => document.querySelector("[aria-label='Batch folder']").value === d, shoot);
    await page.click(".modal button:has-text('Edit all')"); await page.waitForSelector("text=Finished: 2 of 2", { timeout: 60000 });
    check(true, "the batch reports 'Finished: 2 of 2'");
    const outs = fs.existsSync(path.join(dest)) ? fs.readdirSync(dest) : [];
    check(outs.includes("one.png") && outs.includes("two.png"), "edited copies of every photo were saved (to the chosen folder)", outs.join(", "));
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall photo-mode checks passed");
})().catch(e => { console.error(e); process.exit(1); });
