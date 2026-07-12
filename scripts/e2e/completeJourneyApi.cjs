"use strict";

async function browserJson(page, requestPath, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  if (!new Set(["GET", "HEAD"]).has(method)) {
    throw new Error(`Complete journey read-only API helper rejects ${method}`);
  }
  if (options.body !== undefined) {
    throw new Error("Complete journey read-only API helper rejects request bodies");
  }
  return page.evaluate(async ({ body, gardenId, method, path }) => {
    const csrf = document.cookie
      .split("; ")
      .find((part) => part.startsWith("gardenops_csrf="))
      ?.slice("gardenops_csrf=".length) || "";
    const headers = { Accept: "application/json" };
    if (gardenId !== undefined && gardenId !== null) headers["x-garden-id"] = String(gardenId);
    if (body !== undefined) headers["content-type"] = "application/json";
    if (method && method !== "GET") headers["x-csrf-token"] = decodeURIComponent(csrf);
    const response = await fetch(path, {
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "include",
      headers,
      method: method || "GET",
    });
    const text = await response.text();
    let parsed = null;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text;
      }
    }
    return { body: parsed, status: response.status };
  }, { ...options, method, path: requestPath });
}

module.exports = { browserJson };
