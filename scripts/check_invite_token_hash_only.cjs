#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require(path.resolve(__dirname, "../frontend/node_modules/typescript"));

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function loadModule() {
  const sourcePath = path.resolve(__dirname, "../frontend/src/core/urlSecurity.ts");
  const source = fs.readFileSync(sourcePath, "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: sourcePath,
  }).outputText;

  const module = { exports: {} };
  const sandbox = {
    module,
    exports: module.exports,
    require,
    console,
    URL,
    URLSearchParams,
    window: undefined,
    document: undefined,
  };
  vm.runInNewContext(output, sandbox, { filename: sourcePath });
  return { exports: module.exports, sandbox };
}

function setWindow(sandbox, href) {
  const url = new URL(href);
  let replacedHref = null;
  sandbox.document = { title: "GardenOps" };
  sandbox.window = {
    location: {
      href: url.toString(),
      origin: url.origin,
      pathname: url.pathname,
      search: url.search,
      hash: url.hash,
    },
    history: {
      replaceState: (_state, _title, nextHref) => {
        replacedHref = String(nextHref);
        const nextUrl = new URL(nextHref, url.origin);
        sandbox.window.location.href = nextUrl.toString();
        sandbox.window.location.pathname = nextUrl.pathname;
        sandbox.window.location.search = nextUrl.search;
        sandbox.window.location.hash = nextUrl.hash;
      },
    },
  };
  return () => replacedHref;
}

function main() {
  const { exports, sandbox } = loadModule();
  const {
    buildInvitationLink,
    clearPrimedInviteToken,
    peekPrimedInviteToken,
    primeInviteTokenFromLocation,
    readInviteTokenFromLocation,
  } = exports;

  setWindow(sandbox, "https://example.test/invite/accept#step=1");
  const built = new URL(
    buildInvitationLink(
      "hash-secret",
      "https://example.test/invite/accept?invite=query-secret#step=1",
    ),
  );
  assert(
    built.searchParams.get("invite") === null,
    "buildInvitationLink must remove invite from the query string",
  );
  const builtHash = new URLSearchParams(built.hash.slice(1));
  assert(
    builtHash.get("invite") === "hash-secret",
    "buildInvitationLink must store invite tokens in the hash",
  );
  assert(
    builtHash.get("step") === "1",
    "buildInvitationLink must preserve existing hash params",
  );

  assert(
    readInviteTokenFromLocation({
      search: "?invite=query-secret",
      hash: "#",
    }) === "",
    "readInviteTokenFromLocation must ignore query-string invite tokens",
  );
  assert(
    readInviteTokenFromLocation({
      search: "?invite=query-secret",
      hash: "#invite=hash-secret",
    }) === "hash-secret",
    "readInviteTokenFromLocation must still accept hash invite tokens",
  );

  clearPrimedInviteToken();
  let readReplacedHref = setWindow(
    sandbox,
    "https://example.test/invite/accept?invite=query-secret#step=1",
  );
  assert(
    primeInviteTokenFromLocation() === "",
    "primeInviteTokenFromLocation must not prime query-string invite tokens",
  );
  assert(
    readReplacedHref() === "/invite/accept#step=1",
    "primeInviteTokenFromLocation must scrub invite tokens from the URL even when ignored",
  );
  assert(
    peekPrimedInviteToken() === "",
    "peekPrimedInviteToken must remain empty after query-only invite links",
  );

  clearPrimedInviteToken();
  readReplacedHref = setWindow(
    sandbox,
    "https://example.test/invite/accept?invite=query-secret#invite=hash-secret&step=1",
  );
  assert(
    primeInviteTokenFromLocation() === "hash-secret",
    "primeInviteTokenFromLocation must keep accepting hash-based invite tokens",
  );
  assert(
    readReplacedHref() === "/invite/accept#step=1",
    "primeInviteTokenFromLocation must scrub invite tokens from both hash and query",
  );
  assert(
    peekPrimedInviteToken() === "hash-secret",
    "peekPrimedInviteToken must expose the primed hash token",
  );

  console.log("Invite token hash-only checks passed.");
}

main();
