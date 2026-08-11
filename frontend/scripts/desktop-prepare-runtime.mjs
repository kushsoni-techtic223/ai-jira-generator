import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const standaloneDir = path.join(root, ".next", "standalone");
const runtimeDir = path.join(root, ".desktop-runtime");

function copyDir(src, dest) {
  fs.cpSync(src, dest, { recursive: true });
}

if (!fs.existsSync(path.join(standaloneDir, "server.js"))) {
  console.error("Missing .next/standalone/server.js. Run DESKTOP_BUILD=1 yarn build first.");
  process.exit(1);
}

fs.rmSync(runtimeDir, { recursive: true, force: true });
copyDir(standaloneDir, runtimeDir);
copyDir(path.join(root, ".next", "static"), path.join(runtimeDir, ".next", "static"));

const publicDir = path.join(root, "public");
if (fs.existsSync(publicDir)) {
  copyDir(publicDir, path.join(runtimeDir, "public"));
}

console.log(`Prepared desktop runtime at ${runtimeDir}`);
