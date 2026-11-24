// End-to-end check of colour wheels and scopes against the REAL backend (no mocks).
//   prerequisites: python deps + FFmpeg on PATH, `npm run build`, Playwright resolvable (npm i -g playwright; NODE_PATH=$(npm root -g))
//   run:           npm run e2e          (exit code 1 on any failed assertion)
const { chromium } = require("playwright");
const { spawn, execFileSync } = require("child_process");
const http = require("http"), fs = require("fs"), path = require("path"), os = require("os");

const ROOT = path.resolve(__dirname, "..", ".."), DIST = path.join(ROOT, "frontend", "dist"), BACKEND = path.join(ROOT, "backend");
let failures = 0;
const check = (ok, what, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${what}${extra ? "  (" + extra + ")" : ""}`); if (!ok) failures++; };

function startBackend() {
  return new Promise((resolve, reject) => {
    const proc = spawn(process.env.EVP_PYTHON || "python3", ["-m", "evp.server", "--data-dir", fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-data-"))], { cwd: BACKEND });
    let buf = ""; proc.stdout.on("data", d => { buf += d; const nl = buf.indexOf("\n"); if (nl >= 0) { try { resolve({ proc, ...JSON.parse(buf.slice(0, nl)) }); } catch (e) { reject(e); } } });
    proc.on("exit", c => reject(new Error("backend exited " + c)));
    setTimeout(() => reject(new Error("backend did not start")), 20000);
  });
}
function serveDist() {
  const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
  const srv = http.createServer((q, r) => { const f = path.join(DIST, q.url.split("?")[0] === "/" ? "index.html" : q.url.split("?")[0]); fs.readFile(f, (e, d) => { if (e) { r.writeHead(404); r.end(); } else { r.writeHead(200, { "content-type": types[path.extname(f)] || "application/octet-stream" }); r.end(d); } }); });
  return new Promise(res => srv.listen(0, "127.0.0.1", () => res(srv)));
}
const pngChunks = (buf) => { const n = []; let i = 8; while (i + 8 <= buf.length) { const len = buf.readUInt32BE(i); n.push(buf.toString("ascii", i + 4, i + 8)); i += 12 + len; } return n; };

(async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-"));
  // a black-to-white ramp: it contains every tone, so "lift affects the shadows, not the highlights" can be measured
  const video = path.join(work, "ramp.mp4");
  execFileSync(process.env.EVP_FFMPEG || "ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "nullsrc=s=320x180:r=25:d=3,format=gbrp,geq=r='X*255/W':g='X*255/W':b='X*255/W'", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12", video]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");            // where one-click projects go
  const be = await startBackend(), web = await serveDist();
  const base = `http://127.0.0.1:${be.port}`, H = { "x-evp-token": be.token, "content-type": "application/json" };
  const browser = await chromium.launch(), page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message)); page.on("console", m => { if (m.type() === "error") errs.push(m.text()); });
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    // start screen: ONE step from a video to the editor (no folder, no name to fill in)
    await page.waitForSelector(".drop");
    check(await page.locator(".drop").count() === 1 && await page.locator("text=Drop a video here").count() === 1, "the start screen offers a drop zone");
    await page.click(".drop"); await page.fill('input[placeholder="Full path"]', video); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    check(fs.readdirSync(path.join(work, "proj")).some(n => n.endsWith(".evproj")), "the project was created in the default projects folder");
    await page.click("button[aria-label=Color]"); await page.click("text=Blank grade"); await page.waitForSelector(".wheel");
    const op = async () => { await page.waitForTimeout(1500); return (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint.operations[0]; };

    check(await page.locator(".wheel").count() === 3, "three colour wheels (Lift, Gamma, Gain) are shown");
    check(await page.locator('.fl:text-is("lift")').count() === 0, "the raw lift/gamma/gain text boxes are replaced by the wheels");

    // drag Lift toward blue (hue 240 = lower-left): a pure, zero-mean blue cast
    const disc = page.locator(".wheel-disc").nth(0), bb = await disc.boundingBox(), cx = bb.x + bb.width / 2, cy = bb.y + bb.height / 2, R = bb.width / 2;
    await page.mouse.move(cx, cy); await page.mouse.down(); await page.mouse.move(cx - R * 0.45, cy + R * 0.78, { steps: 6 }); await page.mouse.up();
    let lift = (await op()).parameters.lift;
    check(Array.isArray(lift) && lift[2] > lift[0] && lift[2] > lift[1], "dragging toward blue makes blue the strongest lift channel", JSON.stringify(lift));
    check(Math.abs(lift[0] + lift[1] + lift[2]) < 1e-3 && Math.abs(lift[0] - lift[1]) < 2e-3, "the cast is zero-mean (pure colour, no brightness change)");

    // the rendered shadows really get bluer (independent measurement inside the page, on the engine's frame)
    const proj = (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint;
    const frame = async (original) => Buffer.from(await (await fetch(`${base}/api/preview/frame`, { method: "POST", headers: H, body: JSON.stringify({ blueprint: proj, t: 1.0, width: 320, original }) })).arrayBuffer());
    const [gbuf, obuf] = [await frame(false), await frame(true)];
    const shift = await page.evaluate(async ([g, o]) => {
      const px = async (b64) => { const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0)), bmp = await createImageBitmap(new Blob([bin], { type: "image/png" }), { colorSpaceConversion: "none" }); const c = document.createElement("canvas"); c.width = bmp.width; c.height = bmp.height; const x = c.getContext("2d"); x.drawImage(bmp, 0, 0); return { d: x.getImageData(0, 0, c.width, c.height).data, w: c.width, h: c.height }; };
      const [a, b] = [await px(g), await px(o)], zone = (x0, x1) => { let n = 0, db = 0, dr = 0, dg = 0; for (let y = 0; y < a.h; y++) for (let x = Math.floor(a.w * x0); x < Math.floor(a.w * x1); x++) { const i = (y * a.w + x) * 4; n++; db += a.d[i + 2] - b.d[i + 2]; dr += a.d[i] - b.d[i]; dg += a.d[i + 1] - b.d[i + 1]; } return { b: db / n, r: dr / n, g: dg / n }; };
      return { shadows: zone(0.05, 0.25), highlights: zone(0.8, 0.98) };
    }, [gbuf.toString("base64"), obuf.toString("base64")]);
    check(shift.shadows.b > 8 && shift.shadows.r < -3 && shift.shadows.g < -3, "the grade tints the SHADOWS blue (more blue, less red/green) in the rendered frame", `shadows B ${shift.shadows.b.toFixed(1)} R ${shift.shadows.r.toFixed(1)} G ${shift.shadows.g.toFixed(1)}`);
    check(Math.abs(shift.highlights.b) < 0.2 * shift.shadows.b && Math.abs(shift.highlights.r) < 0.2 * Math.abs(shift.shadows.r), "...and barely touches the HIGHLIGHTS (lift tapers off: in + lift*(1-in))", `highlights B ${shift.highlights.b.toFixed(1)} R ${shift.highlights.r.toFixed(1)}`);

    // frame PNGs carry no colour-management tags (browsers would otherwise show them ~10% darker than the real pixels)
    const names = pngChunks(gbuf);
    check(!names.some(n => ["cICP", "cHRM", "gAMA", "iCCP", "sRGB"].includes(n)), "preview frames carry no colour-management chunks", names.join(","));

    // keyboard, reset, master, numeric entry
    await disc.focus(); for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowUp");
    const moved = (await op()).parameters.lift; check(JSON.stringify(moved) !== JSON.stringify(lift), "arrow keys move the puck");
    await page.keyboard.press("Delete"); check(!("lift" in (await op()).parameters), "Delete resets the wheel (parameter removed)");
    await page.locator(".wheel").nth(1).locator("input[type=range]").fill("0.2");
    const gm = (await op()).parameters.gamma_rgb; check(gm && gm.every(v => Math.abs(v - 0.2) < 1e-6), "master slider adds equally to R, G and B", JSON.stringify(gm));
    await page.locator(".wheel").nth(2).locator("input[type=number]").nth(0).fill("0.25");
    const gn = (await op()).parameters.gain; check(gn && Math.abs(gn[0] - 0.25) < 1e-6 && gn[1] === 0 && gn[2] === 0, "numeric R entry sets exactly that channel", JSON.stringify(gn));
    await page.locator(".wheel-disc").nth(2).dblclick(); check(!("gain" in (await op()).parameters), "double-click resets the wheel");

    // scopes
    await page.click("button[aria-label=Scopes]"); await page.waitForSelector(".scope-canvas"); await page.waitForFunction(() => !document.querySelector(".scope-wait"), null, { timeout: 20000 });
    const lit = () => page.evaluate(() => { const c = document.querySelector(".scope-canvas"), d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] + d[i + 1] + d[i + 2] > 60) n++; return n; });
    for (const k of ["Waveform", "RGB Parade", "Vectorscope", "Histogram"]) { await page.click(`.scope-seg button:text-is("${k}")`); await page.waitForTimeout(500); const n = await lit(); check(n > 300, `${k} draws the picture's signal`, `${n} lit pixels`); }

    // the readouts equal an independent measurement of the SAME frame (playhead 0)
    const pj = (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint;
    const f0 = Buffer.from(await (await fetch(`${base}/api/preview/frame`, { method: "POST", headers: H, body: JSON.stringify({ blueprint: pj, t: 0, width: 360, original: false }) })).arrayBuffer());
    const expect = await page.evaluate(async (b64) => {
      const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0)), bmp = await createImageBitmap(new Blob([bin], { type: "image/png" }), { colorSpaceConversion: "none" }); const c = document.createElement("canvas"); c.width = bmp.width; c.height = bmp.height; const x = c.getContext("2d"); x.drawImage(bmp, 0, 0);
      const d = x.getImageData(0, 0, c.width, c.height).data, n = d.length / 4; let sy = 0, hi = 0, lo = 0;
      for (let i = 0; i < d.length; i += 4) { sy += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]; if (Math.max(d[i], d[i + 1], d[i + 2]) === 255) hi++; if (Math.min(d[i], d[i + 1], d[i + 2]) === 0) lo++; }
      return { luma: Math.round((sy / n / 255) * 100), hi: (100 * hi / n).toFixed(1), lo: (100 * lo / n).toFixed(1) };
    }, f0.toString("base64"));
    await page.waitForTimeout(800); const text = await page.locator(".scope-stats").innerText();
    check(text.includes(`${expect.luma}%`) && text.includes(`${expect.hi}%`) && text.includes(`${expect.lo}%`), "scope readouts equal an independent measurement of the same frame", `UI: ${text.replace(/\n/g, " | ")}  expected luma ${expect.luma}% hi ${expect.hi}% lo ${expect.lo}%`);

    // live scopes while the video plays (requires CORS-enabled canvas reads of the video)
    await page.click('button[title^="Play / pause"]'); await page.waitForTimeout(900);
    check((await page.locator(".side-content .muted").first().innerText()).includes("playing video") && await page.locator(".side-content .issue").count() === 0 && await lit() > 300, "scopes follow the playing video without a cross-origin error");
    await page.click('button[title^="Play / pause"]');
    // importing a background picture from the toolbar's Import button (this used to do nothing useful)
    const pic = path.join(work, "studio picture.png");
    execFileSync(process.env.EVP_FFMPEG || "ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x3060a0:s=320x180", "-frames:v", "1", pic]);
    await page.click("button[aria-label=Backgrounds]"); await page.click("text=AI remove background"); await page.waitForTimeout(500);   // a background needs something to replace
    await page.click("button:has-text('Import')");
    check(await page.locator("[role=menuitem]").count() === 3, "Import opens a menu: video, background, LUT");
    await page.click("[role=menuitem]:has-text('Background image')");
    await page.fill('input[placeholder="Full path"]', pic); await page.keyboard.press("Enter");
    await page.waitForTimeout(1500);
    const bpNow = (await (await fetch(`${base}/api/project`, { headers: H })).json()).blueprint;
    check(bpNow.assets.some(a => a.id === "studio_picture" && a.type === "image") && fs.readdirSync(path.join(work, "proj")).length > 0, "the picture was imported into the project as an asset (file name with a space is fine)");
    check(bpNow.operations.some(o => o.type === "background_replacement" && o.parameters.asset_id === "studio_picture"), "and it is applied as the background immediately");
    check(errs.length === 0, "no console or page errors", errs.join(" | "));
  } catch (e) { console.log("FAIL  unexpected error: " + e.message); failures++; }
  await browser.close(); web.close(); be.proc.kill();
  console.log(failures ? `\n${failures} check(s) FAILED` : "\nall end-to-end checks passed"); process.exit(failures ? 1 : 0);
})();
