#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");

function fail(message) {
  console.error(`iOS Home Screen check failed: ${message}`);
  process.exit(1);
}

function attribute(tag, name) {
  const match = tag.match(new RegExp(`\\b${name}\\s*=\\s*["']([^"']+)["']`, "i"));
  return match?.[1] ?? null;
}

function matchingTag(html, element, attributes) {
  const tags = html.match(new RegExp(`<${element}\\b[^>]*>`, "gi")) ?? [];
  return tags.find((tag) =>
    Object.entries(attributes).every(([name, value]) => attribute(tag, name) === value),
  );
}

function requireTag(html, element, attributes) {
  if (!matchingTag(html, element, attributes)) {
    const expected = Object.entries(attributes)
      .map(([name, value]) => `${name}="${value}"`)
      .join(" ");
    fail(`missing <${element} ${expected}>`);
  }
}

function pngDimensions(filePath) {
  if (!fs.existsSync(filePath)) {
    fail(`missing icon ${filePath}`);
  }

  const data = fs.readFileSync(filePath);
  const pngSignature = "89504e470d0a1a0a";
  if (
    data.length < 26 ||
    data.subarray(0, 8).toString("hex") !== pngSignature ||
    data.subarray(12, 16).toString("ascii") !== "IHDR"
  ) {
    fail(`${filePath} is not a valid PNG`);
  }

  return {
    width: data.readUInt32BE(16),
    height: data.readUInt32BE(20),
    colorType: data[25],
  };
}

function requireIcon(assetRoot, relativePath, expectedSize) {
  const filePath = path.join(assetRoot, relativePath.replace(/^\//, ""));
  const { width, height, colorType } = pngDimensions(filePath);
  if (width !== expectedSize || height !== expectedSize) {
    fail(`${relativePath} must be ${expectedSize}x${expectedSize}, got ${width}x${height}`);
  }
  if (colorType !== 2) {
    fail(`${relativePath} must be an opaque RGB PNG without an alpha channel`);
  }
}

function main() {
  const mode = process.argv[2] || "source";
  if (!new Set(["source", "dist"]).has(mode)) {
    fail(`unknown mode ${mode}; expected source or dist`);
  }

  const frontendRoot = path.resolve(__dirname, "../frontend");
  const assetRoot =
    mode === "dist" ? path.join(frontendRoot, "dist") : path.join(frontendRoot, "public");
  const indexPath =
    mode === "dist" ? path.join(assetRoot, "index.html") : path.join(frontendRoot, "index.html");
  if (!fs.existsSync(indexPath)) {
    fail(`missing ${indexPath}`);
  }

  const html = fs.readFileSync(indexPath, "utf8");
  const viewport = matchingTag(html, "meta", { name: "viewport" });
  const viewportParts = attribute(viewport ?? "", "content")
    ?.split(",")
    .map((part) => part.trim());
  if (!viewportParts?.includes("viewport-fit=cover")) {
    fail("viewport metadata must include viewport-fit=cover for iPhone safe areas");
  }

  requireTag(html, "meta", { name: "theme-color", content: "#151915" });
  requireTag(html, "meta", { name: "mobile-web-app-capable", content: "yes" });
  requireTag(html, "meta", { name: "apple-mobile-web-app-capable", content: "yes" });
  requireTag(html, "meta", { name: "apple-mobile-web-app-status-bar-style", content: "black-translucent" });
  requireTag(html, "meta", { name: "apple-mobile-web-app-title", content: "GardenOps" });
  requireTag(html, "link", { rel: "manifest", href: "/manifest.webmanifest" });
  requireTag(html, "link", {
    rel: "apple-touch-icon",
    sizes: "180x180",
    href: "/icons/apple-touch-icon.png",
  });
  requireTag(html, "link", {
    rel: "icon",
    type: "image/png",
    sizes: "32x32",
    href: "/icons/favicon-32.png",
  });

  const manifestPath = path.join(assetRoot, "manifest.webmanifest");
  if (!fs.existsSync(manifestPath)) {
    fail(`missing ${manifestPath}`);
  }
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  for (const [field, value] of Object.entries({
    name: "GardenOps",
    short_name: "GardenOps",
    id: "/",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#151915",
    theme_color: "#151915",
  })) {
    if (manifest[field] !== value) {
      fail(`manifest ${field} must be ${JSON.stringify(value)}`);
    }
  }

  const expectedManifestIcons = new Map([
    ["/icons/gardenops-192.png", "192x192"],
    ["/icons/gardenops-512.png", "512x512"],
  ]);
  for (const icon of manifest.icons ?? []) {
    if (
      expectedManifestIcons.get(icon.src) === icon.sizes &&
      icon.type === "image/png" &&
      icon.purpose === "any"
    ) {
      expectedManifestIcons.delete(icon.src);
    }
  }
  if (expectedManifestIcons.size > 0) {
    fail(`manifest is missing required icons: ${Array.from(expectedManifestIcons.keys()).join(", ")}`);
  }

  requireIcon(assetRoot, "/icons/apple-touch-icon.png", 180);
  requireIcon(assetRoot, "/icons/gardenops-192.png", 192);
  requireIcon(assetRoot, "/icons/gardenops-512.png", 512);
  requireIcon(assetRoot, "/icons/favicon-32.png", 32);
  console.log(`iOS Home Screen check passed for ${mode} assets.`);
}

main();
