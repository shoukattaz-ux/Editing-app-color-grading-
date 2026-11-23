const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("evp", {
  isElectron: true,
  backend: ipcRenderer.sendSync("backend-info"),
  pickFile: (opts) => ipcRenderer.invoke("pick-file", opts),
  pickFolder: (title) => ipcRenderer.invoke("pick-folder", title),
});
