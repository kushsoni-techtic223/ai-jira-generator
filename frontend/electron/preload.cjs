const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("dtlDesktop", {
  isDesktop: true,
  goBack: () => ipcRenderer.invoke("nav:go-back"),
  goHome: () => ipcRenderer.invoke("nav:go-home"),
});
