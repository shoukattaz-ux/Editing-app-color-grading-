// End-to-end: Auto enhance - one click, one master slider, measured note, styles, and the picture really improves on a Studio Look (preset button, sliders, stored values) and the rendered picture.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-ae-")), shot = path.join(work, "gs.mp4");
  execFileSync(process.env.EVP_PYTHON || "python3", ["-c", `import sys; sys.path.insert(0, ${JSON.stringify(BACKEND)}); from evp.samples import make_greenscreen; make_greenscreen(${JSON.stringify(shot)}, 1, size="320x180")`]);
  const dull = path.join(work, "dull clip.mp4");                     // a dim, warm, flat clip (like an overcast phone video)
  execFileSync(process.env.EVP_FFMPEG || "ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=3", "-vf", "colorchannelmixer=rr=0.8:gg=0.74:bb=0.66,format=yuv420p", dull]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  const H = { "x-evp-token": be.token, "content-type": "application/json" }, base = `http://127.0.0.1:${be.port}`;
  const bpNow = async () => (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint;
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', dull); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    await page.click("button[aria-label=Color]"); await page.click("text=Auto enhance (one slider)"); await page.waitForSelector(".autoenhance");
    let g = null;
    for (let i = 0; i < 40 && !g; i++) { await page.waitForTimeout(250); g = (await bpNow()).operations.find(o => o.type === "color_grading"); }
    check(g && g.parameters.auto_enhance === 1 && g.parameters.enhance_style === "crisp", "one click adds Auto enhance (100 %, crisp) over the whole clip", JSON.stringify(g && g.parameters));
    await page.waitForFunction(() => /Measured:/.test(document.querySelector(".autoenhance")?.textContent ?? ""), null, { timeout: 30000 });
    check(/Applying at 100%/.test(await page.locator(".autoenhance").innerText()), "the card says what it measured and what it applies");
    const setAmt = v => page.locator('input[aria-label="Auto enhance amount"]').evaluate((el, v) => { const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; s.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); }, v);
    await setAmt("60"); await page.waitForTimeout(800);
    check((await bpNow()).operations.find(o => o.type === "color_grading").parameters.auto_enhance === 0.6, "the master slider stores its value");
    await page.click(".autoenhance button:has-text('Natural')"); await page.waitForTimeout(800);
    check((await bpNow()).operations.find(o => o.type === "color_grading").parameters.enhance_style === "natural", "the style buttons store the style");
    const stats = await page.evaluate(async ([base, token, bp]) => {
      const f = async (b) => { const r = await fetch(base + "/api/preview/frame", { method: "POST", headers: { "x-evp-token": token, "content-type": "application/json" }, body: JSON.stringify({ blueprint: b, t: 1, width: 640 }) });
        const bmp = await createImageBitmap(await r.blob()); const c = document.createElement("canvas"); c.width = bmp.width; c.height = bmp.height; const x = c.getContext("2d"); x.drawImage(bmp, 0, 0);
        const d = x.getImageData(0, 0, c.width, c.height).data; let r_ = 0, g_ = 0, b_ = 0; for (let i = 0; i < d.length; i += 4) { r_ += d[i]; g_ += d[i + 1]; b_ += d[i + 2]; } const n = d.length / 4; return [r_ / n, g_ / n, b_ / n]; };
      const off = JSON.parse(JSON.stringify(bp)); off.operations.find(o => o.type === "color_grading").parameters.auto_enhance = 0;
      const on = JSON.parse(JSON.stringify(bp)); Object.assign(on.operations.find(o => o.type === "color_grading").parameters, { auto_enhance: 1, enhance_style: "crisp" });
      return [await f(off), await f(on)]; }, [base, be.token, await bpNow()]);
    const lum = c => (c[0] + c[1] + c[2]) / 3, rb = c => c[0] / c[2];
    check(lum(stats[1]) > lum(stats[0]) + 8 && rb(stats[1]) < rb(stats[0]) - 0.05, "the rendered clip is brighter and less warm", `${stats[0].map(Math.round)} -> ${stats[1].map(Math.round)}`);
    await page.click(".autoenhance button:has-text('Subject only')"); await page.waitForTimeout(800);
    check((await bpNow()).operations.find(o => o.type === "color_grading").parameters.apply_to === "subject", "Apply to: Subject only is stored");
    check(/only, and measures only that part/.test(await page.locator(".autoenhance").innerText()), "the card explains what Subject only means");
    await page.click(".autoenhance button:has-text('Both')"); await page.waitForTimeout(600);
    await page.click(".autoenhance button:has-text('Off')"); await page.waitForTimeout(800);
    check((await bpNow()).operations.find(o => o.type === "color_grading").parameters.auto_enhance === 0, "Off turns it off");
    // cinematic look: one click, style, bars
    await page.click("button[aria-label=Color]"); await page.click("text=Cinematic look"); await page.waitForSelector(".cinematic");
    let cg = null;
    for (let i = 0; i < 40 && !cg; i++) { await page.waitForTimeout(250); cg = (await bpNow()).operations.find(o => o.type === "color_grading" && o.parameters.cinematic); }
    check(cg && cg.parameters.cinematic === 1 && cg.parameters.cinematic_style === "teal_orange" && cg.parameters.auto_enhance === 0.7, "one click adds the Cinematic look (on an auto-enhanced base)", JSON.stringify(cg && cg.parameters));
    await page.click(".cinematic button:has-text('Moody')"); await page.selectOption('select[aria-label="Cinema bars"]', "2.39"); await page.waitForTimeout(900);
    cg = (await bpNow()).operations.find(o => o.id === cg.id);
    check(cg.parameters.cinematic_style === "moody" && cg.parameters.letterbox === "2.39", "style and cinema bars are stored");
    // portrait mode: from the Effects list, over the whole clip, renders a frame
    await page.click("button[aria-label=Effects]"); await page.click(".side button.item:has-text('Portrait Mode')");
    let pm = null;
    for (let i = 0; i < 40 && !pm; i++) { await page.waitForTimeout(250); pm = (await bpNow()).operations.find(o => o.type === "portrait_blur"); }
    check(pm && pm.start === 0 && Math.abs((pm.end ?? 3) - 3) < 0.05, "Portrait Mode is added over the whole clip", JSON.stringify(pm));
    const pf = await page.evaluate(async ([base, token, bp]) => { const r = await fetch(base + "/api/preview/frame", { method: "POST", headers: { "x-evp-token": token, "content-type": "application/json" }, body: JSON.stringify({ blueprint: bp, t: 1, width: 480 }) }); return r.status; }, [base, be.token, await bpNow()]);
    check(pf === 200, "a live frame with Portrait Mode renders (AI person detection + background blur)", String(pf));
    // smart HDR: one click in the Color panel, whole clip, renders
    await page.click("button[aria-label=Color]"); await page.click(".side >> text=Smart HDR");
    let hd = null;
    for (let i = 0; i < 40 && !hd; i++) { await page.waitForTimeout(250); hd = (await bpNow()).operations.find(o => o.type === "smart_hdr"); }
    check(hd && hd.start === 0 && Math.abs((hd.end ?? 3) - 3) < 0.05, "Smart HDR is added over the whole clip", JSON.stringify(hd));
    const hf = await page.evaluate(async ([base, token, bp]) => { const r = await fetch(base + "/api/preview/frame", { method: "POST", headers: { "x-evp-token": token, "content-type": "application/json" }, body: JSON.stringify({ blueprint: bp, t: 1, width: 480 }) }); return r.status; }, [base, be.token, await bpNow()]);
    check(hf === 200, "a live frame with Smart HDR renders", String(hf));
    // skin retouch: from the Effects list (Face), whole clip, renders (no face in this synthetic clip: the picture is simply unchanged)
    await page.click("button[aria-label=Effects]"); await page.click(".side button.item:has-text('Skin Retouch')");
    let sr = null;
    for (let i = 0; i < 40 && !sr; i++) { await page.waitForTimeout(250); sr = (await bpNow()).operations.find(o => o.type === "skin_retouch"); }
    check(sr && sr.start === 0 && Math.abs((sr.end ?? 3) - 3) < 0.05, "Skin Retouch is added over the whole clip", JSON.stringify(sr));
    const sf = await page.evaluate(async ([base, token, bp]) => { const r = await fetch(base + "/api/preview/frame", { method: "POST", headers: { "x-evp-token": token, "content-type": "application/json" }, body: JSON.stringify({ blueprint: bp, t: 1, width: 480 }) }); return r.status; }, [base, be.token, await bpNow()]);
    check(sf === 200, "a live frame with Skin Retouch renders", String(sf));
    // AI face restoration: from the Effects list; the card shows the model state (stubbed here to test the download flow without 348 MB)
    let phase = "missing";
    await page.route("**/api/models/gfpgan", r => r.fulfill({ contentType: "application/json", body: JSON.stringify(phase === "missing" ? { name: "gfpgan", installed: false, downloading: false, done: 0, total: 348632874, size: 348632874, license: "Apache-2.0", error: null }
      : phase === "downloading" ? { name: "gfpgan", installed: false, downloading: true, done: 174316437, total: 348632874, size: 348632874, license: "Apache-2.0", error: null }
      : { name: "gfpgan", installed: true, downloading: false, done: 0, total: 348632874, size: 348632874, license: "Apache-2.0", error: null }) }));
    await page.route("**/api/models/gfpgan/install", r => { phase = "downloading"; r.fulfill({ contentType: "application/json", body: JSON.stringify({ name: "gfpgan", installed: false, downloading: true, done: 1000, total: 348632874, size: 348632874, license: "Apache-2.0", error: null }) }); });
    await page.click("button[aria-label=Effects]"); await page.click(".side button.item:has-text('AI Face Restoration')");
    await page.waitForSelector(".facerestore");
    check(/model needed/.test(await page.locator(".facerestore").innerText()) && await page.locator('.facerestore button:has-text("Download the model (349 MB)")').count() === 1, "the card offers 'Download the model (349 MB)' when it is missing");
    check(/re-draws/.test(await page.locator(".facerestore").innerText()), "the card says honestly that the AI re-draws detail");
    await page.click('.facerestore button:has-text("Download the model")');
    await page.waitForSelector(".facerestore .gpubar"); check(/Downloading the model: 174 MB of 349 MB/.test(await page.locator(".facerestore").innerText()), "a download shows its progress");
    phase = "ready"; await page.waitForFunction(() => /model ready/.test(document.querySelector(".facerestore")?.textContent ?? ""), null, { timeout: 15000 });
    check(await page.locator(".facerestore .gpubar").count() === 0, "once installed the download box is gone");
    let fr = null;
    for (let i = 0; i < 40 && !fr; i++) { await page.waitForTimeout(250); fr = (await bpNow()).operations.find(o => o.type === "face_restoration"); }
    check(fr && fr.start === 0 && Math.abs((fr.end ?? 3) - 3) < 0.05, "AI Face Restoration is added over the whole clip", JSON.stringify(fr));
    await page.unroute("**/api/models/gfpgan");
    const ff = await page.evaluate(async ([base, token, bp]) => { const r = await fetch(base + "/api/preview/frame", { method: "POST", headers: { "x-evp-token": token, "content-type": "application/json" }, body: JSON.stringify({ blueprint: bp, t: 1, width: 480 }) }); return r.status; }, [base, be.token, await bpNow()]);
    check(ff === 200, "a live frame with AI Face Restoration renders (no face in this clip: unchanged)", String(ff));
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall auto-enhance checks passed");
})().catch(e => { console.error(e); process.exit(1); });
