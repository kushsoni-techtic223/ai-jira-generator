const { app, BrowserWindow, dialog, shell, ipcMain } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const http = require("node:http");

const FRONTEND_PORT = 3000;
const BACKEND_PORT = 8000;
const isProdFlag = process.argv.includes("--prod");
const isDev = !app.isPackaged && !isProdFlag;
const HOME_URL = `http://localhost:${FRONTEND_PORT}`;

let backendProc = null;
let frontendProc = null;
let win = null;

function logPath() {
  try {
    return path.join(app.getPath("userData"), "desktop-startup.log");
  } catch {
    return path.join(process.cwd(), "desktop-startup.log");
  }
}

function logLine(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  try {
    fs.appendFileSync(logPath(), line);
  } catch {
    // ignore
  }
  process.stdout.write(line);
}

function rootPaths() {
  const devFrontendDir = path.resolve(__dirname, "..");
  const packagedRuntimeDir = path.join(process.resourcesPath, ".desktop-runtime");
  const packagedFrontendDir = path.resolve(__dirname, "..");
  const frontendDir = app.isPackaged
    ? fs.existsSync(path.join(packagedRuntimeDir, "server.js"))
      ? packagedRuntimeDir
      : packagedFrontendDir
    : devFrontendDir;
  const backendDir = app.isPackaged
    ? path.join(process.resourcesPath, ".desktop-backend")
    : path.resolve(__dirname, "..", "..", "backend");
  return {
    frontendDir,
    backendDir,
    useStandalone: app.isPackaged && frontendDir === packagedRuntimeDir,
  };
}

function commandExists(cmd) {
  const probe = spawnSync(cmd, ["--version"], {
    encoding: "utf8",
    env: {
      ...process.env,
      PATH: [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/Library/Frameworks/Python.framework/Versions/Current/bin",
        "/usr/bin",
        "/bin",
        process.env.PATH || "",
      ].join(":"),
    },
  });
  return !probe.error && probe.status === 0;
}

function resolvePythonCommand(cmd) {
  if (cmd.includes(path.sep)) return cmd;
  const dirs = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/Library/Frameworks/Python.framework/Versions/Current/bin",
    "/usr/bin",
    "/bin",
  ];
  for (const dir of dirs) {
    const full = path.join(dir, cmd);
    if (fs.existsSync(full)) return full;
  }
  return cmd;
}

function pythonHasModule(pythonCmd, moduleName) {
  const probe = spawnSync(pythonCmd, ["-c", `import ${moduleName}`], {
    encoding: "utf8",
    env: {
      ...process.env,
      PATH: [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/Library/Frameworks/Python.framework/Versions/Current/bin",
        "/usr/bin",
        "/bin",
        process.env.PATH || "",
      ].join(":"),
    },
  });
  return !probe.error && probe.status === 0;
}

function pythonReadyForBackend(pythonCmd) {
  return (
    pythonHasModule(pythonCmd, "uvicorn") &&
    pythonHasModule(pythonCmd, "fastapi") &&
    pythonHasModule(pythonCmd, "requests") &&
    pythonHasModule(pythonCmd, "dotenv")
  );
}

function ensureBackendPython(backendDir) {
  const userVenvPython = path.join(
    app.getPath("userData"),
    "backend-venv",
    "bin",
    "python"
  );
  const candidates = [
    userVenvPython,
    path.join(backendDir, ".venv", "bin", "python"),
    "python3",
    "python",
  ];

  for (const candidate of candidates) {
    const resolved = resolvePythonCommand(candidate);
    const exists = resolved.includes(path.sep)
      ? fs.existsSync(resolved)
      : commandExists(resolved);
    if (!exists) continue;
    if (pythonReadyForBackend(resolved)) {
      return resolved;
    }
  }

  const basePython = ["python3", "python"]
    .map(resolvePythonCommand)
    .find((cmd) => commandExists(cmd) || (cmd.includes(path.sep) && fs.existsSync(cmd)));
  if (!basePython) {
    throw new Error("Python 3 was not found. Install Python 3 and reopen the app.");
  }

  const venvDir = path.join(app.getPath("userData"), "backend-venv");
  const requirements = path.join(backendDir, "requirements.txt");
  if (!fs.existsSync(requirements)) {
    throw new Error(`Missing backend requirements at ${requirements}`);
  }

  logLine(`Creating backend venv at ${venvDir}`);
  fs.rmSync(venvDir, { recursive: true, force: true });
  const venvResult = spawnSync(basePython, ["-m", "venv", venvDir], {
    encoding: "utf8",
  });
  if (venvResult.status !== 0) {
    throw new Error(
      `Failed to create Python venv:\n${venvResult.stderr || venvResult.stdout || "unknown error"}`
    );
  }

  const pipPython = path.join(venvDir, "bin", "python");
  logLine("Installing backend Python packages");
  const pipResult = spawnSync(
    pipPython,
    ["-m", "pip", "install", "--upgrade", "pip", "-r", requirements],
    { encoding: "utf8" }
  );
  if (pipResult.status !== 0) {
    throw new Error(
      `Failed to install backend packages:\n${pipResult.stderr || pipResult.stdout || "unknown error"}`
    );
  }

  if (!pythonReadyForBackend(pipPython)) {
    throw new Error("Backend packages installed, but required modules are still missing.");
  }
  return pipPython;
}

function spawnLogged(cmd, args, cwd, name, extraEnv = {}) {
  const child = spawn(cmd, args, {
    cwd,
    env: {
      ...process.env,
      PATH: [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/Library/Frameworks/Python.framework/Versions/Current/bin",
        "/usr/bin",
        "/bin",
        process.env.PATH || "",
      ].join(":"),
      ...extraEnv,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  child.stdout.on("data", (buf) => {
    const text = buf.toString();
    process.stdout.write(`[${name}] ${text}`);
    logLine(`[${name}] ${text.trim()}`);
  });
  child.stderr.on("data", (buf) => {
    const text = buf.toString();
    process.stderr.write(`[${name}] ${text}`);
    logLine(`[${name}] ${text.trim()}`);
  });
  child.on("exit", (code) => {
    const msg = `[${name}] exited with code ${code}`;
    process.stdout.write(`${msg}\n`);
    logLine(msg);
  });
  return child;
}

function pickNodeRuntime() {
  // Packaged app may not have system "node" on PATH.
  // Reuse Electron binary as Node when ELECTRON_RUN_AS_NODE is set.
  const candidates = ["node", "nodejs"];
  if (!app.isPackaged) {
    return { cmd: "node", env: {} };
  }
  return {
    cmd: process.execPath,
    env: { ELECTRON_RUN_AS_NODE: "1" },
    fallbackCmds: candidates,
  };
}

function canConnect(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      res.resume();
      resolve(true);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(1000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

function waitForHttp(url, timeoutMs = 60000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(url, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", () => {
        if (Date.now() - started > timeoutMs) {
          reject(new Error(`Timed out waiting for ${url}`));
          return;
        }
        setTimeout(tick, 500);
      });
      req.setTimeout(1500, () => {
        req.destroy();
      });
    };
    tick();
  });
}

async function startServices() {
  const { frontendDir, backendDir, useStandalone } = rootPaths();
  logLine(`frontendDir=${frontendDir}`);
  logLine(`backendDir=${backendDir}`);
  logLine(`useStandalone=${useStandalone}`);
  if (!backendDir || !fs.existsSync(backendDir)) {
    throw new Error(`Backend folder not found: ${backendDir || "(undefined)"}`);
  }
  const python = ensureBackendPython(backendDir);
  logLine(`Using python=${python}`);
  const writableDataDir = path.join(app.getPath("userData"), "backend-data");
  fs.mkdirSync(writableDataDir, { recursive: true });

  const backendUrl = `http://127.0.0.1:${BACKEND_PORT}/auth/github/setup`;
  const backendUp = await canConnect(backendUrl);
  if (!backendUp) {
    backendProc = spawnLogged(
      python,
      [
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "127.0.0.1",
        "--port",
        String(BACKEND_PORT),
      ],
      backendDir,
      "backend",
      {
        BACKEND_DATA_DIR: writableDataDir,
        DESKTOP_MODE: "1",
        FRONTEND_URL: `http://localhost:${FRONTEND_PORT}`,
      }
    );
  }
  await waitForHttp(`http://127.0.0.1:${BACKEND_PORT}/auth/github/setup`);

  const frontendUrl = `http://localhost:${FRONTEND_PORT}`;
  const frontendUp = await canConnect(frontendUrl);
  if (!frontendUp) {
    if (isDev && !app.isPackaged) {
      frontendProc = spawnLogged(
        "yarn",
        ["--cwd", frontendDir, "dev", "-p", String(FRONTEND_PORT)],
        frontendDir,
        "frontend"
      );
    } else if (useStandalone) {
      const serverJs = path.join(frontendDir, "server.js");
      if (!fs.existsSync(serverJs)) {
        throw new Error(`Standalone server not found: ${serverJs}`);
      }
      const nodeRt = pickNodeRuntime();
      frontendProc = spawnLogged(
        nodeRt.cmd,
        [serverJs],
        frontendDir,
        "frontend",
        {
          ...(nodeRt.env || {}),
          PORT: String(FRONTEND_PORT),
          HOSTNAME: "localhost",
        }
      );
    } else {
      const nextBin = path.join(
        frontendDir,
        "node_modules",
        "next",
        "dist",
        "bin",
        "next"
      );
      if (!fs.existsSync(nextBin)) {
        throw new Error(`Next runtime not found: ${nextBin}`);
      }
      const nodeRt = pickNodeRuntime();
      try {
        frontendProc = spawnLogged(
          nodeRt.cmd,
          [nextBin, "start", "-p", String(FRONTEND_PORT)],
          frontendDir,
          "frontend",
          nodeRt.env || {}
        );
      } catch (err) {
        const fallbackCmds = nodeRt.fallbackCmds || [];
        if (fallbackCmds.length === 0) throw err;
        let spawned = null;
        for (const cmd of fallbackCmds) {
          try {
            spawned = spawnLogged(
              cmd,
              [nextBin, "start", "-p", String(FRONTEND_PORT)],
              frontendDir,
              "frontend"
            );
            break;
          } catch {
            // try next fallback command
          }
        }
        if (!spawned) throw err;
        frontendProc = spawned;
      }
    }
  }
  await waitForHttp(`http://localhost:${FRONTEND_PORT}`);
}

function stopServices() {
  for (const proc of [frontendProc, backendProc]) {
    if (proc && !proc.killed) {
      try {
        proc.kill("SIGTERM");
      } catch {
        // ignore
      }
    }
  }
}

function createWindow() {
  const preloadPath = path.join(__dirname, "preload.cjs");
  win = new BrowserWindow({
    width: 1400,
    height: 900,
    title: "Daily Time Logger",
    icon: path.join(__dirname, "icons", process.platform === "win32" ? "icon.png" : "icon.icns"),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: preloadPath,
    },
  });

  if (process.platform === "darwin") {
    const dockIcon = path.join(__dirname, "icons", "icon.png");
    if (fs.existsSync(dockIcon) && app.dock) {
      try {
        app.dock.setIcon(dockIcon);
      } catch {
        // ignore dock icon failures
      }
    }
  }

  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  const injectBackButton = async () => {
    try {
      await win.webContents.executeJavaScript(`
        (function () {
          if (document.getElementById("dtl-electron-back")) return;
          const btn = document.createElement("button");
          btn.id = "dtl-electron-back";
          btn.type = "button";
          btn.title = "Go back";
          btn.setAttribute("aria-label", "Go back");
          btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M15.5 4.5L8 12l7.5 7.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
          btn.style.cssText = [
            "position:fixed",
            "top:40px",
            "left:40px",
            "z-index:2147483647",
            "box-sizing:border-box",
            "width:44px",
            "height:44px",
            "border-radius:9999px",
            "border:1px solid #cbd5e1",
            "background:#ffffff",
            "box-shadow:0 10px 28px rgba(15,23,42,0.22)",
            "cursor:pointer",
            "color:#0f172a",
            "display:inline-flex",
            "align-items:center",
            "justify-content:center",
            "padding:0",
            "margin:0",
            "line-height:0",
            "appearance:none",
            "-webkit-appearance:none",
          ].join(";");
          btn.addEventListener("click", function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (window.dtlDesktop && typeof window.dtlDesktop.goBack === "function") {
              window.dtlDesktop.goBack();
              return;
            }
            if (window.history.length > 1) window.history.back();
            else window.location.href = ${JSON.stringify(HOME_URL)};
          });
          (document.body || document.documentElement).appendChild(btn);
        })();
      `);
    } catch (err) {
      logLine(`Back button inject failed: ${err && err.message ? err.message : err}`);
    }
  };

  win.webContents.on("did-finish-load", () => {
    void injectBackButton();
  });
  win.webContents.on("dom-ready", () => {
    void injectBackButton();
  });

  win.loadURL(HOME_URL);
}

ipcMain.handle("nav:go-back", (event) => {
  const wc = event.sender;
  if (wc.canGoBack()) {
    wc.goBack();
    return { ok: true, action: "back" };
  }
  wc.loadURL(HOME_URL);
  return { ok: true, action: "home" };
});

ipcMain.handle("nav:go-home", (event) => {
  event.sender.loadURL(HOME_URL);
  return { ok: true, action: "home" };
});

app.on("window-all-closed", () => {
  stopServices();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  stopServices();
});

app.whenReady().then(async () => {
  try {
    fs.writeFileSync(logPath(), "");
    logLine("App starting");
    await startServices();
    createWindow();
    logLine("Window created");
  } catch (err) {
    const message = err && err.stack ? err.stack : String(err);
    logLine(`Startup failed: ${message}`);
    console.error(err);
    try {
      dialog.showErrorBox(
        "Daily Time Logger failed to start",
        `${err.message || err}\n\nDetails: ${logPath()}`
      );
    } catch {
      // ignore
    }
    stopServices();
    app.quit();
  }
});
