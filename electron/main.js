// Electron main process: starts the local engine, then opens the UI.
// Security: contextIsolation on, nodeIntegration off, strict CSP, no navigation/new windows, backend bound to 127.0.0.1 with a
// per-launch secret token. In the installed app the engine is a self-contained executable (no Python needed) and FFmpeg is bundled.
const { app, BrowserWindow, Menu, ipcMain, dialog, session, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const readline = require("readline");

const SMOKE = process.env.EVP_SMOKE === "1";            // automated check: start, load the UI, report, quit
let backend = null;
let info = null;
let win = null;
let quitting = false;

if (!app.requestSingleInstanceLock()) { app.quit(); process.exit(0); }
app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });

const logDir = () => path.join(app.getPath("userData"), "logs");
function log(...a) {
  const line = `[${new Date().toISOString()}] ${a.join(" ")}\n`;
  try { fs.mkdirSync(logDir(), { recursive: true }); fs.appendFileSync(path.join(logDir(), "app.log"), line); } catch { /* ignore */ }
  if (!app.isPackaged) process.stderr.write(line);
}
process.on("uncaughtException", (e) => { log("uncaughtException", e && e.stack || e); });
process.on("unhandledRejection", (e) => { log("unhandledRejection", e && e.stack || e); });

const exe = (n) => (process.platform === "win32" ? n + ".exe" : n);
const root = () => (app.isPackaged ? process.resourcesPath : path.join(__dirname, ".."));

// How to start the engine: bundled executable if present (installed app), else the Python sources (development).
function engineCommand() {
  const bundled = path.join(root(), "engine", exe("evp-engine"));
  const env = { ...process.env, PYTHONUNBUFFERED: "1" };
  const ff = path.join(root(), "ffmpeg", exe("ffmpeg")), fp = path.join(root(), "ffmpeg", exe("ffprobe"));
  if (fs.existsSync(ff)) env.EVP_FFMPEG = ff;
  if (fs.existsSync(fp)) env.EVP_FFPROBE = fp;
  const dataDir = path.join(app.getPath("userData"), "evp");
  if (fs.existsSync(bundled)) return { cmd: bundled, args: ["--data-dir", dataDir], cwd: path.dirname(bundled), env };
  const py = process.env.EVP_PYTHON || (process.platform === "win32" ? "python" : "python3");
  return { cmd: py, args: ["-m", "evp.server", "--data-dir", dataDir], cwd: path.join(root(), "backend"), env };
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const c = engineCommand();
    log("starting engine:", c.cmd, c.args.join(" "));
    backend = spawn(c.cmd, c.args, { cwd: c.cwd, env: c.env, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    backend.on("error", (e) => reject(new Error(`Could not start the engine (${c.cmd}): ${e.message}`)));
    const rl = readline.createInterface({ input: backend.stdout });
    let done = false;
    rl.on("line", (line) => {
      if (done) return;
      try { info = JSON.parse(line); done = true; resolve(info); } catch { log("engine:", line); }
    });
    let err = "";
    backend.stderr.on("data", (d) => { err += d.toString(); if (err.length > 20000) err = err.slice(-10000); });
    backend.on("exit", (code) => {
      log("engine exited", code);
      if (!done) reject(new Error(`The engine exited during start-up (code ${code}).\n${err.slice(-800)}`));
      else if (!quitting) dialog.showErrorBox("The engine stopped", "The video engine stopped unexpectedly. Your project is auto-saved; restart the app to continue.\n\n" + err.slice(-600));
    });
    setTimeout(() => { if (!done) reject(new Error("The engine did not start within 40 seconds")); }, 40000);
  });
}

async function waitHealthy(port) {
  for (let i = 0; i < 100; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/api/health`); if (r.ok) return; } catch { /* retry */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("Engine health check failed");
}

// ---- window with remembered size/position
const statePath = () => path.join(app.getPath("userData"), "window.json");
function loadState() {
  try { const s = JSON.parse(fs.readFileSync(statePath(), "utf8")); if (s.width > 600 && s.height > 400) return s; } catch { /* default */ }
  return { width: 1500, height: 920 };
}
function saveState() {
  if (!win || win.isDestroyed()) return;
  const b = win.getNormalBounds();
  try { fs.writeFileSync(statePath(), JSON.stringify({ ...b, maximized: win.isMaximized() })); } catch { /* ignore */ }
}

function splash() {
  const s = new BrowserWindow({ width: 420, height: 240, frame: false, resizable: false, show: false, backgroundColor: "#0e1014", alwaysOnTop: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false } });
  const html = `<html><body style="margin:0;background:#0e1014;color:#e6e8ee;font:15px system-ui,Segoe UI,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh">
    <div style="font-size:26px;font-weight:600;letter-spacing:.5px">EVP Editor</div><div style="margin-top:10px;color:#9aa3b5">Starting the video engine…</div>
    <div style="margin-top:22px;width:200px;height:3px;background:#222733;border-radius:2px;overflow:hidden"><div style="width:40%;height:100%;background:#5b8cff;animation:m 1.1s ease-in-out infinite"></div></div>
    <style>@keyframes m{0%{margin-left:-40%}100%{margin-left:100%}}</style></body></html>`;
  s.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html));
  s.once("ready-to-show", () => s.show());
  return s;
}

function buildMenu() {
  const t = [
    { label: "&File", submenu: [
      { label: "Open logs folder", click: () => shell.openPath(logDir()) },
      { label: "Open data folder", click: () => shell.openPath(app.getPath("userData")) },
      { type: "separator" }, { role: "quit" } ] },
    { label: "&Edit", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
    { label: "&View", submenu: [{ role: "reload" }, { role: "togglefullscreen" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, ...(app.isPackaged ? [] : [{ role: "toggleDevTools" }])] },
    { label: "&Help", submenu: [
      { label: "About EVP Editor", click: () => dialog.showMessageBox({ type: "info", title: "EVP Editor", message: "EVP Editor " + app.getVersion(),
          detail: "AI blueprint-based video editor.\nVideo engine: " + (info ? "running on 127.0.0.1:" + info.port : "not running") + "\nLogs: " + logDir() }) },
      { label: "Problem reports…", click: () => win && win.webContents.executeJavaScript("document.querySelector('[title=\"Settings: problem reports\"]')?.click()") } ] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(t));
}

async function createWindow() {
  const st = loadState();
  win = new BrowserWindow({
    width: st.width, height: st.height, x: st.x, y: st.y, minWidth: 1100, minHeight: 680, show: false,
    backgroundColor: "#0e1014", title: "EVP Editor",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: false },
  });
  if (st.maximized) win.maximize();
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (e) => e.preventDefault());
  win.on("close", saveState);
  win.once("ready-to-show", () => win.show());
  const index = app.isPackaged ? path.join(root(), "frontend", "index.html") : path.join(__dirname, "..", "frontend", "dist", "index.html");
  await win.loadFile(index);
  return win;
}

ipcMain.on("backend-info", (e) => { e.returnValue = info; });
ipcMain.handle("pick-file", async (_e, opts) => {
  const r = await dialog.showOpenDialog(win, { title: opts?.title, properties: ["openFile"], filters: opts?.filters });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("pick-folder", async (_e, title) => {
  const r = await dialog.showOpenDialog(win, { title, properties: ["openDirectory", "createDirectory"] });
  return r.canceled ? null : r.filePaths[0];
});

app.whenReady().then(async () => {
  // strict CSP: only our own files and the local engine
  session.defaultSession.webRequest.onHeadersReceived((d, cb) => cb({ responseHeaders: { ...d.responseHeaders,
    "Content-Security-Policy": ["default-src 'self'; img-src 'self' data: blob: http://127.0.0.1:*; media-src 'self' blob: http://127.0.0.1:*; connect-src 'self' http://127.0.0.1:*; style-src 'self' 'unsafe-inline'; script-src 'self'"] } }));
  const sp = SMOKE ? null : splash();
  try {
    buildMenu();
    await startBackend();
    await waitHealthy(info.port);
    await createWindow();
    if (sp && !sp.isDestroyed()) sp.close();
    if (SMOKE) {
      let body = "";
      for (let i = 0; i < 40; i++) {                       // wait until the UI has connected to the engine
        body = await win.webContents.executeJavaScript("document.body.innerText.slice(0,300)");
        if (!/Connecting|Starting…/.test(body)) break;
        await new Promise((r) => setTimeout(r, 250));
      }
      const title = await win.webContents.executeJavaScript("document.title");
      if (process.env.EVP_SMOKE_SHOT) fs.writeFileSync(process.env.EVP_SMOKE_SHOT, (await win.webContents.capturePage()).toPNG());
      const health = await (await fetch(`http://127.0.0.1:${info.port}/api/health`)).json();
      process.stdout.write("SMOKE " + JSON.stringify({ title, body, health, packaged: app.isPackaged, port: info.port }) + "\n");
      quitting = true; app.quit();
    }
  } catch (e) {
    log("start-up failed", e && e.stack || e);
    if (sp && !sp.isDestroyed()) sp.close();
    dialog.showErrorBox("EVP could not start", String(e.message || e) + "\n\nLog: " + logDir());
    quitting = true; app.quit();
  }
});
app.on("window-all-closed", () => { quitting = true; app.quit(); });
app.on("will-quit", () => { quitting = true; if (backend && !backend.killed) backend.kill(); });
