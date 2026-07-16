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
  const appSourcePath = path.resolve(__dirname, "../frontend/src/app.ts");
  const indexPath = path.resolve(__dirname, "../frontend/index.html");
  const authGatePath = path.resolve(__dirname, "../frontend/src/features/authGate.ts");
  const savedViewsPath = path.resolve(__dirname, "../frontend/src/features/savedViewsFeature.ts");
  const quickActionsPath = path.resolve(__dirname, "../frontend/src/features/quickActionsFeature.ts");
  const authStylePath = path.resolve(__dirname, "../frontend/src/auth.css");
  const passwordChecklistPath = path.resolve(__dirname, "../frontend/src/components/passwordChecklist.ts");
  const passkeysPath = path.resolve(__dirname, "../frontend/src/features/passkeys.ts");
  const apiPath = path.resolve(__dirname, "../frontend/src/services/api.ts");
  const i18nPath = path.resolve(__dirname, "../frontend/src/core/i18n.ts");
  const stylePath = path.resolve(__dirname, "../frontend/src/style.css");
  if (!fs.existsSync(appSourcePath)) {
    fail("missing lazy authenticated app module at frontend/src/app.ts");
  }
  const sourceText = fs.readFileSync(sourcePath, "utf8");
  const appSourceText = fs.readFileSync(appSourcePath, "utf8");
  const indexText = fs.readFileSync(indexPath, "utf8");
  const authGateText = fs.readFileSync(authGatePath, "utf8");
  const savedViewsText = fs.readFileSync(savedViewsPath, "utf8");
  const quickActionsText = fs.readFileSync(quickActionsPath, "utf8");
  const passwordChecklistText = fs.readFileSync(passwordChecklistPath, "utf8");
  const passkeysText = fs.readFileSync(passkeysPath, "utf8");
  const apiText = fs.readFileSync(apiPath, "utf8");
  const i18nText = fs.readFileSync(i18nPath, "utf8");
  const styleText = fs.readFileSync(stylePath, "utf8");
  const authStyleText = fs.existsSync(authStylePath)
    ? fs.readFileSync(authStylePath, "utf8")
    : "";
  const appSource = ts.createSourceFile(appSourcePath, appSourceText, ts.ScriptTarget.Latest, true);

  if (!indexText.includes("/src/auth.css") || indexText.includes("/src/style.css")) {
    fail("pre-login HTML must load auth.css instead of the full app stylesheet");
  }
  if (!appSourceText.includes("import \"./style.css\"")) {
    fail("authenticated app module must lazy-load the full app stylesheet");
  }
  if (appSourceText.includes("from \"./tabs/careTab\"") || appSourceText.includes("from './tabs/careTab'")) {
    fail("authenticated app module must lazy-load the Care tab instead of importing it during app startup");
  }
  if (!appSourceText.includes("import(\"./tabs/careTab\")")) {
    fail("authenticated app module must keep a lazy Care tab import boundary");
  }
  if (appSourceText.includes("from \"./tabs/tasksTab\"") || appSourceText.includes("from './tabs/tasksTab'")) {
    fail("authenticated app module must lazy-load the Tasks tab instead of importing it during app startup");
  }
  if (!appSourceText.includes("import(\"./tabs/tasksTab\")")) {
    fail("authenticated app module must keep a lazy Tasks tab import boundary");
  }
  if (appSourceText.includes("from \"./tabs/harvestTab\"") || appSourceText.includes("from './tabs/harvestTab'")) {
    fail("authenticated app module must lazy-load the Harvest tab instead of importing it during app startup");
  }
  if (!appSourceText.includes("import(\"./tabs/harvestTab\")")) {
    fail("authenticated app module must keep a lazy Harvest tab import boundary");
  }
  if (savedViewsText.includes("from \"../tabs/tasksTab\"") || savedViewsText.includes("from '../tabs/tasksTab'")) {
    fail("saved views must not import the Tasks tab during authenticated app startup");
  }
  if (savedViewsText.includes("from \"../tabs/harvestTab\"") || savedViewsText.includes("from '../tabs/harvestTab'")) {
    fail("saved views must not import the Harvest tab during authenticated app startup");
  }
  if (quickActionsText.includes("from \"../tabs/harvestTab\"") || quickActionsText.includes("from '../tabs/harvestTab'")) {
    fail("quick actions must not import the Harvest tab during authenticated app startup");
  }
  if (appSourceText.includes("from \"./components/onboarding\"") || appSourceText.includes("from './components/onboarding'")) {
    fail("authenticated app module must lazy-load onboarding instead of importing it during app startup");
  }
  if (!appSourceText.includes("import(\"./components/onboarding\")")) {
    fail("authenticated app module must keep a lazy onboarding import boundary");
  }
  if (appSourceText.includes("from \"./features/plantSearchFeature\"") || appSourceText.includes("from './features/plantSearchFeature'")) {
    fail("authenticated app module must lazy-load plant search instead of importing it during app startup");
  }
  if (!appSourceText.includes("import(\"./features/plantSearchFeature\")")) {
    fail("authenticated app module must keep a lazy plant search import boundary");
  }
  if (appSourceText.includes("from \"./components/diagnosePlant\"") || appSourceText.includes("from './components/diagnosePlant'")) {
    fail("authenticated app module must lazy-load the Diagnose Plant modal instead of importing it during app startup");
  }
  if (!appSourceText.includes("import(\"./components/diagnosePlant\")")) {
    fail("authenticated app module must keep a lazy Diagnose Plant modal import boundary");
  }
  if (appSourceText.includes("from \"./components/identifyPlant\"") || appSourceText.includes("from './components/identifyPlant'")) {
    fail("authenticated app module must lazy-load the Identify Plant modal instead of importing it during app startup");
  }
  if (!appSourceText.includes("import(\"./components/identifyPlant\")")) {
    fail("authenticated app module must keep a lazy Identify Plant modal import boundary");
  }
  if (quickActionsText.includes("from \"../components/identifyPlant\"") || quickActionsText.includes("from '../components/identifyPlant'")) {
    fail("quick actions must lazy-load the Identify Plant modal instead of importing it during authenticated app startup");
  }
  if (!quickActionsText.includes("import(\"../components/identifyPlant\")")) {
    fail("quick actions must keep a lazy Identify Plant modal import boundary");
  }
  if (!authStyleText.includes(".auth-gate") || authStyleText.includes("@font-face")) {
    fail("auth.css must contain login gate styles without preloading app fonts");
  }
  if (!authStyleText.includes("[hidden]") || !authStyleText.includes("display: none !important")) {
    fail("auth.css must preserve hidden rendering for auth gate step transitions");
  }
  if (!sourceText.includes("import(\"./app\")")) {
    fail("main entry must lazy-load the authenticated app after the auth gate resolves");
  }
  if (!sourceText.includes("__gardenopsInitialAuthProfile")) {
    fail("main entry must prime the authenticated app with the auth profile it already fetched");
  }
  if (!appSourceText.includes("__gardenopsInitialAuthProfile")) {
    fail("authenticated app startup must consume the primed auth profile before refetching /api/auth/me");
  }
  if (!appSourceText.includes("refreshGardenContext({ profile: initialMe })")) {
    fail("authenticated app startup must reuse the initial auth profile while loading garden context");
  }
  [
    "./components/dataTables",
    "./components/mapView",
    "./components/plotInteractions",
    "./tabs/calendarTab",
    "./tabs/careTab",
    "./components/shadePanel",
  ].forEach((heavyImport) => {
    if (sourceText.includes(`from "${heavyImport}"`) || sourceText.includes(`from '${heavyImport}'`)) {
      fail(`main entry must not statically import authenticated app module ${heavyImport}`);
    }
  });
  [
    ["main entry", sourceText],
    ["auth gate", authGateText],
    ["password checklist", passwordChecklistText],
  ].forEach(([label, text]) => {
    if (text.includes("/core/i18n") || text.includes("../core/i18n") || text.includes("./core/i18n")) {
      fail(`${label} must use auth-only i18n instead of the full app translation table`);
    }
    if (text.includes("/services/api") || text.includes("../services/api") || text.includes("./services/api")) {
      fail(`${label} must use auth-only API helpers instead of the full app API module`);
    }
  });

  const statusHelper = findFunction(appSource, "showAuthGateFromCurrentStatus");
  if (!statusHelper) {
    fail("missing showAuthGateFromCurrentStatus helper");
  }

  const helperText = statusHelper.getText(appSource).replace(/\s+/g, "");
  if (
    !helperText.includes("getAuthStatusApi()") ||
    !helperText.includes("showAuthGate(status.bootstrap_required,status.passkeys_enabled)") ||
    !helperText.includes("showAuthGate(false,false)")
  ) {
    fail("showAuthGateFromCurrentStatus must fetch auth status, pass through passkeys_enabled, and keep a fallback gate");
  }

  const authButton = findFunction(appSource, "handleAuthButton");
  if (!authButton) {
    fail("missing handleAuthButton");
  }

  const staleGateCalls = collectCalls(authButton, (node) =>
    isShowAuthGateFalseFalseCall(appSource, node),
  );
  if (staleGateCalls.length > 0) {
    fail("handleAuthButton must not render showAuthGate(false, false) directly");
  }

  const statusGateCalls = collectCalls(authButton, isShowCurrentStatusCall);
  const signedOutState = findFunction(appSource, "completeSignedOutState");
  const delegatedStatusGateCalls = signedOutState
    ? collectCalls(signedOutState, isShowCurrentStatusCall)
    : [];
  const completionCalls = collectCalls(authButton, (node) => (
    ts.isCallExpression(node) && isIdentifier(node.expression, "completeSignedOutState")
  ));
  const directBothBranches = statusGateCalls.length === 2;
  const delegatedSignedOutBranch = statusGateCalls.length === 1
    && completionCalls.length === 1
    && delegatedStatusGateCalls.length === 1;
  if (!directBothBranches && !delegatedSignedOutBranch) {
    fail("handleAuthButton must show the login gate from fresh auth status in both auth branches");
  }

  if (authGateText.includes("\"username webauthn\"")) {
    fail("passkey login must not enable username-less WebAuthn autofill");
  }
  if (passkeysText.includes("isConditionalPasskeyLoginSupported")) {
    fail("passkeys feature must not expose conditional username-less login for this app");
  }
  if (passkeysText.includes("mediation")) {
    fail("passkey login must not request conditional mediation");
  }
  if (!passkeysText.includes("signal?: AbortSignal") || !passkeysText.includes("request.signal = signal")) {
    fail("passkey login must accept an abort signal for the visible password fallback");
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
  if (!authGateText.includes("passwordLabel.hidden = true")) {
    fail("non-bootstrap login must hide the password field on the initial username step");
  }
  if (!authGateText.includes("passwordInput.required = false")) {
    fail("hidden password field must not remain required on the initial username step");
  }
  if (!authGateText.includes("passwordInput.placeholder = t(\"auth.password\")")) {
    fail("non-bootstrap login password field must use an in-field Password placeholder");
  }
  if (!authGateText.includes("submitBtn.textContent = t(\"auth.enter_action\")")) {
    fail("non-bootstrap login must start with a username-only Enter action");
  }
  if (authGateText.includes("allowCredentials?.length")) {
    fail("auth gate must not branch on public allowCredentials because that leaks passkey enrollment");
  }
  if (!authGateText.includes("passwordFallbackBtn.hidden = step !== \"passkey-ready\"")) {
    fail("passkey-first login must expose password fallback before the blocking browser ceremony");
  }
  if (!authGateText.includes("passkeyAttempt += 1") || !authGateText.includes("attempt !== passkeyAttempt")) {
    fail("password fallback must invalidate any pending passkey ceremony result");
  }
  if (!authGateText.includes("passkeyAbortController?.abort()") || !authGateText.includes("abortController.signal")) {
    fail("password fallback must abort the pending passkey ceremony");
  }
  if (!authGateText.includes("auth.login_action")) {
    fail("password fallback after username resolution must expose a Login action");
  }
  if (!authGateText.includes("submitBtn.textContent = t(\"auth.use_passkey\")")) {
    fail("passkey-first login must expose an explicit passkey action after username resolution");
  }
  if (
    !authGateText.includes("pendingPasskeyOptions = await beginPasskeyLoginApi(username)")
    || !authGateText.includes("setLoginStep(\"passkey-ready\")")
    || !authGateText.includes("await startPasskeyLogin(options, passkeyUsername)")
  ) {
    fail("username-resolved passkey options must wait for the explicit passkey action");
  }
  if (!authGateText.includes("auth-gate-identity-label auth-gate-username-label")) {
    fail("username-first login must render the username control as an identity row");
  }
  if (!authGateText.includes("auth-gate-identity-field")) {
    fail("username-first login must wrap the username input in an identity field");
  }
  if (authGateText.includes("usernameIcon") || authGateText.includes("auth-gate-identity-icon")) {
    fail("username-first login must not show a username icon or email-like symbol");
  }
  if (!authGateText.includes("usernameInput.placeholder = t(\"auth.username\")")) {
    fail("username-first login must show Username as faded placeholder text");
  }
  if (!authStyleText.includes(".auth-gate-identity-label .auth-gate-field-label")) {
    fail("username-first login must keep the username label accessible without visible field text");
  }
  if (!styleText.includes(".auth-gate-identity-field:focus-within")) {
    fail("username-first login identity row must expose an obvious focus state");
  }
  if (styleText.includes(".auth-gate-identity-field input:placeholder-shown")) {
    fail("username-first login placeholder must stay left-aligned");
  }
  if (styleText.includes(".auth-gate-identity-field input:not(:placeholder-shown)")) {
    fail("username-first login must not need separate alignment after typing");
  }
  if (!authGateText.includes("if (subtitle)")) {
    fail("auth gate header must skip empty subtitles instead of rendering blank space");
  }
  if (!authGateText.includes("auth-gate-active")) {
    fail("auth gate must mark the body while pre-login gates are active");
  }
  if (!appSourceText.includes("document.body.classList.add(\"app-font-active\")")) {
    fail("main app shell must opt into the app font after authentication");
  }
  if (!styleText.includes("body.auth-gate-active")) {
    fail("pre-login auth gate must avoid loading the app font before sign-in");
  }
  if (!styleText.includes("body.app-font-active")) {
    fail("app font must be scoped to the authenticated app shell");
  }
  if (!i18nText.includes("\"auth.signin_subtitle\": \"\"")) {
    fail("English sign-in subtitle must be removed");
  }
  if (!i18nText.includes("\"auth.enter_action\": \"Enter\"")) {
    fail("English username-step Enter action must be translated");
  }
  if (!i18nText.includes("\"auth.enter_action\": \"Gå inn\"")) {
    fail("Norwegian username-step Enter action must be translated");
  }
  if (
    !i18nText.includes("\"auth.login_action\": \"Login\"") ||
    !i18nText.includes("\"auth.login_action\": \"Logg inn\"")
  ) {
    fail("auth gate password fallback Login action must be translated for English and Norwegian");
  }

  console.log("Auth gate status flow check passed.");
}

main();
