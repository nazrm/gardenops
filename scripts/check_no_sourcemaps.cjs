#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");

function collectSourceMaps(rootDir) {
  const matches = [];
  const stack = [rootDir];

  while (stack.length > 0) {
    const current = stack.pop();
    if (!current) continue;
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const absolutePath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(absolutePath);
        continue;
      }
      if (entry.isFile() && entry.name.endsWith(".map")) {
        matches.push(path.relative(rootDir, absolutePath));
        continue;
      }
      if (entry.isFile() && /\.(?:js|css)$/i.test(entry.name)) {
        const data = fs.readFileSync(absolutePath, "utf8");
        if (/sourceMappingURL\s*=/.test(data)) {
          matches.push(`${path.relative(rootDir, absolutePath)} contains sourceMappingURL`);
        }
      }
    }
  }

  return matches.sort();
}

function main() {
  const rootDir = path.resolve(process.cwd(), process.argv[2] || "frontend/dist");
  if (!fs.existsSync(rootDir)) {
    console.error(`Source-map check failed: build directory not found: ${rootDir}`);
    process.exit(1);
  }

  const matches = collectSourceMaps(rootDir);
  if (matches.length > 0) {
    console.error(`Source-map check failed. Found ${matches.length} deploy artifact(s):`);
    for (const match of matches) {
      console.error(`- ${match}`);
    }
    process.exit(1);
  }

  console.log(`No sourcemaps found under ${rootDir}`);
}

main();
