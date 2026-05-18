#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");

function referencedAssets(indexHtml) {
  const matches = new Set();
  const patterns = [
    /(?:src|href)=["']\/?(assets\/[^"']+)["']/g,
    /import\(["']\/?(assets\/[^"']+)["']\)/g,
  ];

  for (const pattern of patterns) {
    for (const match of indexHtml.matchAll(pattern)) {
      matches.add(match[1]);
    }
  }

  return Array.from(matches).sort();
}

function main() {
  const distDir = path.resolve(process.cwd(), process.argv[2] || "frontend/dist");
  const indexPath = path.join(distDir, "index.html");

  if (!fs.existsSync(indexPath)) {
    console.error(`Frontend asset check failed: missing ${indexPath}`);
    process.exit(1);
  }

  const indexHtml = fs.readFileSync(indexPath, "utf8");
  const assets = referencedAssets(indexHtml);
  if (assets.length === 0) {
    console.error(`Frontend asset check failed: no asset references found in ${indexPath}`);
    process.exit(1);
  }

  const missing = assets.filter((assetPath) => !fs.existsSync(path.join(distDir, assetPath)));
  if (missing.length > 0) {
    console.error(`Frontend asset check failed. Missing ${missing.length} asset(s):`);
    for (const assetPath of missing) {
      console.error(`- ${assetPath}`);
    }
    process.exit(1);
  }

  console.log(`Frontend asset check passed for ${assets.length} asset reference(s).`);
}

main();
