import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "electron", "icons", "icon.icns");
const dist = path.join(root, "dist");

if (!fs.existsSync(src)) {
  console.error("Missing electron/icons/icon.icns — run yarn desktop:prepare-icon first.");
  process.exit(1);
}

const apps = [];
function walk(dir) {
  if (!fs.existsSync(dir)) return;
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    if (name.endsWith(".app") && fs.statSync(full).isDirectory()) {
      apps.push(full);
    } else if (fs.statSync(full).isDirectory() && name.includes("darwin")) {
      walk(full);
    }
  }
}
walk(dist);

if (!apps.length) {
  console.warn("No .app found under dist/ — skip icon apply.");
  process.exit(0);
}

for (const appPath of apps) {
  const dest = path.join(appPath, "Contents", "Resources", "electron.icns");
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
  console.log(`Applied app icon → ${dest}`);
}
