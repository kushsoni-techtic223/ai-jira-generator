const { app, BrowserWindow, shell } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const http = require("node:http");

const FRONTEND_PORT = 3000;
const BACKEND_PORT = 8000;
const isProdFlag = process.argv.includes("--prod");
const isDev = !app.isPackaged && !isProdFlag;

let backendProc = null;
let frontendProc = null;
let win = null;

function rootPaths() {
  const packagedFrontendExtra = path.join(process.resourcesPath, "frontend");
  const packagedFrontendFallback = path.resolve(__dirname, "..");
  const frontendDir = app.isPackaged
    ? fs.existsSync(packagedFrontendExtra)
      ? packagedFrontendExtra
      : packagedFrontendFallback
    : path.resolve(__dirname, "..");
  const backendDir = app.isPackaged
    ? path.join(process.resourcesPath, "backend")
    : path.resolve(__dirname, "..", "..", "backend");
  return { frontendDir, backendDir };
}

function pickPython(backendDir) {
  const candidates = [
    path.join(backendDir, ".venv", "bin", "python"),
    "python3",
    "python",
  ];
  return candidates.find((p) => (p.includes(path.sep) ? fs.existsSync(p) : true));
}

function spawnLogged(cmd, args, cwd, name, extraEnv = {}) {
  const child = spawn(cmd, args, {
    cwd,
    env: { ...process.env, ...extraEnv },
    stdio: ["ignore", "pipe", "pipe"],
  });

  child.stdout.on("data", (buf) => process.stdout.write(`[${name}] ${buf}`));
  child.stderr.on("data", (buf) => process.stderr.write(`[${name}] ${buf}`));
  child.on("exit", (code) => {
    process.stdout.write(`[${name}] exited with code ${code}\n`);
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
  const { frontendDir, backendDir } = rootPaths();
  const python = pickPython(backendDir);
  if (!python) throw new Error("Python not found for backend startup.");
  const writableDataDir = path.join(app.getPath("userData"), "backend-data");

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
      { BACKEND_DATA_DIR: writableDataDir }
    );
  }
  await waitForHttp(`http://127.0.0.1:${BACKEND_PORT}/auth/github/setup`);

  const frontendUrl = `http://127.0.0.1:${FRONTEND_PORT}`;
  const frontendUp = await canConnect(frontendUrl);
  if (!frontendUp) {
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
    if (isDev && !app.isPackaged) {
      frontendProc = spawnLogged(
        "yarn",
        ["--cwd", frontendDir, "dev", "-p", String(FRONTEND_PORT)],
        frontendDir,
        "frontend"
      );
    } else {
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
  await waitForHttp(`http://127.0.0.1:${FRONTEND_PORT}`);
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
  win = new BrowserWindow({
    width: 1400,
    height: 900,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  win.loadURL(`http://127.0.0.1:${FRONTEND_PORT}`);
}

app.on("window-all-closed", () => {
  stopServices();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  stopServices();
});

app.whenReady().then(async () => {
  try {
    await startServices();
    createWindow();
  } catch (err) {
    console.error(err);
    stopServices();
    app.quit();
  }
});
