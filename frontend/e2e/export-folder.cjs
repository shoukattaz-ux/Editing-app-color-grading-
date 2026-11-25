// End-to-end: the Export dialog lets you choose the folder; the file lands there and an existing file is never overwritten.
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
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "evp-e2e-x-")), video = path.join(work, "v.mp4"), dest = path.join(work, "My Exports");
  fs.mkdirSync(dest);
  execFileSync(process.env.EVP_FFMPEG || "ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=2", "-pix_fmt", "yuv420p", video]);
  process.env.EVP_PROJECTS_DIR = path.join(work, "proj");
  const be = await startBackend(path.join(work, "data")), web = await serveDist(), browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 940 } });
  const errs = []; page.on("pageerror", e => errs.push(e.message));
  try {
    await page.goto(`http://127.0.0.1:${web.address().port}/?port=${be.port}&token=${be.token}`);
    await page.waitForSelector(".drop"); await page.click(".drop"); await page.fill('input[placeholder="Full path"]', video); await page.keyboard.press("Enter");
    await page.waitForSelector(".timeline");
    await page.click("button:has-text('Export') >> nth=0"); await page.waitForSelector(".exportdir");
    check(/project's exports folder/i.test(await page.locator(".exportdir input").inputValue()), "the Export dialog has a 'save to' folder row (default: the project's folder)");
    await page.click(".exportdir >> text=Choose folder…"); await page.fill('input[placeholder="Full path"]', dest); await page.keyboard.press("Enter");
    await page.waitForFunction((d) => document.querySelector(".exportdir input").value === d, dest);
    check(true, "after choosing, the dialog shows the chosen folder", dest);
    await page.fill(".modal .field input >> nth=0", "final");
    const run = async () => { await page.click("text=Start export"); await page.waitForSelector("text=Saved to", { timeout: 90000 }); };
    await run();
    check(fs.existsSync(path.join(dest, "final.mp4")) && fs.statSync(path.join(dest, "final.mp4")).size > 1000, "the exported video is in the chosen folder");
    check(/Saved to .*final\.mp4/.test(await page.locator(".issue.ok").innerText()) && (await page.locator(".issue.ok").innerText()).includes(dest), "the dialog shows the full path it was saved to");
    await page.waitForTimeout(500);
    await page.waitForSelector("text=Start export"); await page.waitForFunction(() => !document.querySelector(".modal .jobbar, .modal progress") || true);
    await page.click("text=Start export"); await page.waitForTimeout(4000);
    await page.waitForTimeout(1500);
    const files = fs.readdirSync(dest).sort();
    check(files.includes("final.mp4") && files.some(f => /^final \(2\)\.mp4$/.test(f)), "a second export does not overwrite the first (saved as 'final (2)')", files.join(", "));
    check(errs.length === 0, "no page errors", errs.join(" | ").slice(0, 200));
  } finally { await browser.close(); be.proc.kill(); web.close(); }
  if (failures) { console.log(`\n${failures} check(s) FAILED`); process.exit(1); } console.log("\nall export-folder checks passed");
})().catch(e => { console.error(e); process.exit(1); });
