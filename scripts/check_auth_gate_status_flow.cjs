#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const ts = require(path.resolve(__dirname, "../frontend/node_modules/typescript"));

function fail(message) {
  console.error(`Auth gate status flow check failed: ${message}`);
  process.exit(1);
}

function expressionText(source, node) {
  return node.getText(source).replace(/\s+/g, "");
}

function isIdentifier(node, name) {
  return ts.isIdentifier(node) && node.text === name;
}

function isShowAuthGateFalseFalseCall(source, node) {
  return (
    ts.isCallExpression(node) &&
    isIdentifier(node.expression, "showAuthGate") &&
    node.arguments.length === 2 &&
    expressionText(source, node.arguments[0]) === "false" &&
    expressionText(source, node.arguments[1]) === "false"
  );
}

function isShowCurrentStatusCall(node) {
  return (
    ts.isCallExpression(node) &&
    isIdentifier(node.expression, "showAuthGateFromCurrentStatus") &&
    node.arguments.length === 0
  );
}

function findFunction(source, name) {
  let found = null;
  function visit(node) {
    if (
      ts.isFunctionDeclaration(node) &&
      node.name &&
      node.name.text === name
    ) {
      found = node;
      return;
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
  return found;
}

function collectCalls(node, predicate) {
  const calls = [];
  function visit(child) {
    if (predicate(child)) {
      calls.push(child);
    }
    ts.forEachChild(child, visit);
  }
  visit(node);
  return calls;
}

function main() {
  const sourcePath = path.resolve(__dirname, "../frontend/src/main.ts");
  const sourceText = fs.readFileSync(sourcePath, "utf8");
  const source = ts.createSourceFile(sourcePath, sourceText, ts.ScriptTarget.Latest, true);

  const statusHelper = findFunction(source, "showAuthGateFromCurrentStatus");
  if (!statusHelper) {
    fail("missing showAuthGateFromCurrentStatus helper");
  }

  const helperText = statusHelper.getText(source).replace(/\s+/g, "");
  if (
    !helperText.includes("getAuthStatusApi()") ||
    !helperText.includes("showAuthGate(status.bootstrap_required,status.passkeys_enabled)") ||
    !helperText.includes("showAuthGate(false,false)")
  ) {
    fail("showAuthGateFromCurrentStatus must fetch auth status, pass through passkeys_enabled, and keep a fallback gate");
  }

  const authButton = findFunction(source, "handleAuthButton");
  if (!authButton) {
    fail("missing handleAuthButton");
  }

  const staleGateCalls = collectCalls(authButton, (node) =>
    isShowAuthGateFalseFalseCall(source, node),
  );
  if (staleGateCalls.length > 0) {
    fail("handleAuthButton must not render showAuthGate(false, false) directly");
  }

  const statusGateCalls = collectCalls(authButton, isShowCurrentStatusCall);
  if (statusGateCalls.length !== 2) {
    fail("handleAuthButton must show the login gate from fresh auth status in both auth branches");
  }

  console.log("Auth gate status flow check passed.");
}

main();
