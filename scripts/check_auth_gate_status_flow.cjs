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
  const authGatePath = path.resolve(__dirname, "../frontend/src/features/authGate.ts");
  const passkeysPath = path.resolve(__dirname, "../frontend/src/features/passkeys.ts");
  const apiPath = path.resolve(__dirname, "../frontend/src/services/api.ts");
  const i18nPath = path.resolve(__dirname, "../frontend/src/core/i18n.ts");
  const sourceText = fs.readFileSync(sourcePath, "utf8");
  const authGateText = fs.readFileSync(authGatePath, "utf8");
  const passkeysText = fs.readFileSync(passkeysPath, "utf8");
  const apiText = fs.readFileSync(apiPath, "utf8");
  const i18nText = fs.readFileSync(i18nPath, "utf8");
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

  if (authGateText.includes("\"username webauthn\"")) {
    fail("passkey login must not enable username-less WebAuthn autofill");
  }
  if (passkeysText.includes("isConditionalPasskeyLoginSupported")) {
    fail("passkeys feature must not expose conditional username-less login for this app");
  }
  if (passkeysText.includes("mediation") || passkeysText.includes("signal")) {
    fail("passkey login must not request conditional mediation or keep abort-only conditional plumbing");
  }
  if (authGateText.includes("beginPasskeyLoginApi(\"\")")) {
    fail("auth gate must not start passkey login options with an empty username");
  }
  if (authGateText.includes("beginPasskeyLoginApi()")) {
    fail("auth gate must not start passkey login options without a username");
  }
  if (apiText.includes("username = \"\"")) {
    fail("passkey login API helper must not default to an empty username");
  }
  if (!apiText.includes("username: string")) {
    fail("passkey login API helper must require an explicit username");
  }
  if (!apiText.includes("username.trim()")) {
    fail("passkey login API helper must reject blank usernames before making a request");
  }
  if (authGateText.includes("startConditionalPasskeyLogin")) {
    fail("auth gate must not start conditional passkey login before username entry");
  }
  if (!authGateText.includes("auth.passkey_username_required")) {
    fail("auth gate must show a username-required error before passkey login");
  }
  if (
    !i18nText.includes("\"auth.passkey_username_required\": \"Enter your username before using a passkey.\"") ||
    !i18nText.includes("\"auth.passkey_username_required\": \"Skriv inn brukernavnet ditt før du bruker passnøkkel.\"")
  ) {
    fail("auth gate username-required passkey message must be translated for English and Norwegian");
  }

  console.log("Auth gate status flow check passed.");
}

main();
