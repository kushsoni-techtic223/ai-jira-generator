import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const svgPath = path.join(root, "app", "icon.svg");
const buildDir = path.join(root, "build");
const iconsetDir = path.join(buildDir, "icon.iconset");
const icnsOut = path.join(root, "electron", "icons", "icon.icns");
const pngOut = path.join(root, "electron", "icons", "icon.png");
const publicPng = path.join(root, "public", "icon.png");

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, {
    cwd: root,
    encoding: "utf8",
    ...opts,
  });
  if (res.status !== 0) {
    const err = (res.stderr || res.stdout || "").trim();
    throw new Error(`${cmd} ${args.join(" ")} failed: ${err || res.status}`);
  }
  return res;
}

if (!fs.existsSync(svgPath)) {
  console.error(`Missing ${svgPath}`);
  process.exit(1);
}

fs.mkdirSync(path.join(root, "electron", "icons"), { recursive: true });
fs.mkdirSync(buildDir, { recursive: true });
fs.rmSync(iconsetDir, { recursive: true, force: true });
fs.mkdirSync(iconsetDir, { recursive: true });

// Render SVG → 1024 PNG via macOS Quick Look.
run("qlmanage", ["-t", "-s", "1024", "-o", buildDir, svgPath]);
const rendered = path.join(buildDir, "icon.svg.png");
if (!fs.existsSync(rendered)) {
  console.error("qlmanage did not produce build/icon.svg.png");
  process.exit(1);
}

const sizes = [
  ["icon_16x16.png", 16],
  ["icon_16x16@2x.png", 32],
  ["icon_32x32.png", 32],
  ["icon_32x32@2x.png", 64],
  ["icon_128x128.png", 128],
  ["icon_128x128@2x.png", 256],
  ["icon_256x256.png", 256],
  ["icon_256x256@2x.png", 512],
  ["icon_512x512.png", 512],
  ["icon_512x512@2x.png", 1024],
];

for (const [name, px] of sizes) {
  run("sips", ["-z", String(px), String(px), rendered, "--out", path.join(iconsetDir, name)], {
    stdio: "ignore",
  });
}

run("iconutil", ["-c", "icns", iconsetDir, "-o", icnsOut]);
fs.copyFileSync(rendered, pngOut);
fs.mkdirSync(path.join(root, "public"), { recursive: true });
fs.copyFileSync(rendered, publicPng);

console.log(`Prepared app icon:\n  ${icnsOut}\n  ${pngOut}`);
