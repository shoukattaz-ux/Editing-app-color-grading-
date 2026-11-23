// Electron main process: starts the local Python engine, then opens the UI.
// Security: contextIsolation on, nodeIntegration off, backend bound to 127.0.0.1 with a per-launch secret token.
const { app, BrowserWindow, ipcMain, dialog, session } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const readline = require("readline");

let backend = null;
let info = null;

function resources() {
  return app.isPackaged ? process.resourcesPath : path.join(__dirname, "..");
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const py = process.env.EVP_PYTHON || (process.platform === "win32" ? "python" : "python3");
    const cwd = app.isPackaged ? path.join(resources(), "backend") : path.join(resources(), "backend");
    backend = spawn(py, ["-m", "evp.server", "--data-dir", path.join(app.getPath("userData"), "evp")], { cwd, stdio: ["ignore", "pipe", "pipe"] });
    const rl = readline.createInterface({ input: backend.stdout });
    let done = false;
    rl.on("line", (line) => {
      if (done) return;
      try { info = JSON.parse(line); done = true; resolve(info); } catch { /* ignore non-JSON output */ }
    });
    let err = "";
    backend.stderr.on("data", (d) => { err += d.toString(); });
    backend.on("exit", (code) => { if (!done) reject(new Error(`Python engine exited (${code}).\n${err.slice(-800)}\nInstall Python 3.10+ and run: pip install -r backend/requirements.txt (and FFmpeg).`)); });
    setTimeout(() => { if (!done) reject(new Error("Python engine did not start within 20s")); }, 20000);
  });
}

async function waitHealthy(port) {
  for (let i = 0; i < 50; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/api/health`); if (r.ok) return; } catch { /* retry */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("Engine health check failed");
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1500, height: 920, backgroundColor: "#0e1014", title: "EVP Editor",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: false },
  });
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (e) => e.preventDefault());
  const index = app.isPackaged ? path.join(resources(), "frontend", "index.html") : path.join(__dirname, "..", "frontend", "dist", "index.html");
  await win.loadFile(index);
}

ipcMain.on("backend-info", (e) => { e.returnValue = info; });
ipcMain.handle("pick-file", async (_e, opts) => {
  const r = await dialog.showOpenDialog({ title: opts?.title, properties: ["openFile"], filters: opts?.filters });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("pick-folder", async (_e, title) => {
  const r = await dialog.showOpenDialog({ title, properties: ["openDirectory", "createDirectory"] });
  return r.canceled ? null : r.filePaths[0];
});

app.whenReady().then(async () => {
  try {
    await startBackend();
    await waitHealthy(info.port);
    await createWindow();
  } catch (e) {
    dialog.showErrorBox("EVP could not start", String(e.message || e));
    app.quit();
  }
});
app.on("window-all-closed", () => app.quit());
app.on("will-quit", () => { if (backend && !backend.killed) backend.kill(); });
