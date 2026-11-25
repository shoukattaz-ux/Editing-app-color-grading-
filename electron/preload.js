const { contextBridge, ipcRenderer, webUtils } = require("electron");
contextBridge.exposeInMainWorld("evp", {
  isElectron: true,
  backend: ipcRenderer.sendSync("backend-info"),
  pickFile: (opts) => ipcRenderer.invoke("pick-file", opts),
  pickFolder: (title) => ipcRenderer.invoke("pick-folder", title),
  reveal: (p) => ipcRenderer.invoke("reveal-path", p),
  pathFor: (file) => { try { return webUtils.getPathForFile(file); } catch { return file.path || ""; } },   // dropped files
  app: {
    info: () => ipcRenderer.invoke("app-info"),
    checkUpdates: () => ipcRenderer.invoke("check-updates"),
    openLogs: () => ipcRenderer.invoke("open-logs"),
  },
});
