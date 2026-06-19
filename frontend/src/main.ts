import "./core/trustedTypes"; // Must stay first — documents that no permissive default policy is installed
import { showAuthGate, showForcedPasswordChangeGate } from "./features/authGate";
import { getLocale, setLocale } from "./core/i18n";
import {
  clearPrimedInviteToken,
  primeInviteTokenFromLocation,
} from "./core/urlSecurity";
import {
  ApiError,
  getAuthMeApi,
  getAuthStatusApi,
} from "./services/api";

let authenticatedAppPromise: Promise<unknown> | null = null;

function loadAuthenticatedApp(): Promise<unknown> {
  authenticatedAppPromise ??= import("./app")
    .catch((err) => {
      authenticatedAppPromise = null;
      throw err;
    });
  return authenticatedAppPromise;
}

function showSecurityWarningBanner(message: string): void {
  const existing = document.getElementById("security-warning-banner");
  if (existing) {
    existing.textContent = message;
    existing.hidden = false;
    return;
  }
  const el = document.createElement("div");
  el.id = "security-warning-banner";
  el.className = "security-warning-banner";
  el.textContent = message;
  document.body.prepend(el);
}

async function resolveInitialAuthentication(): Promise<void> {
  let bootstrapRequired = false;
  let passkeysEnabled = false;

  try {
    let initialMe = await getAuthMeApi();
    clearPrimedInviteToken();
    if (initialMe.language && initialMe.language !== getLocale()) {
      setLocale(initialMe.language);
    }
    if (initialMe.must_change_password) {
      await showForcedPasswordChangeGate(initialMe.username);
      initialMe = await getAuthMeApi();
      if (initialMe.language && initialMe.language !== getLocale()) {
        setLocale(initialMe.language);
      }
    }
    return;
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      showSecurityWarningBanner(err.message);
    }
  }

  try {
    const status = await getAuthStatusApi();
    bootstrapRequired = status.bootstrap_required;
    passkeysEnabled = status.passkeys_enabled;
  } catch {
    // Can't reach status either — the gate will show the real error on submit.
  }
  await showAuthGate(bootstrapRequired, passkeysEnabled);
}

async function bootstrapEntry(): Promise<void> {
  primeInviteTokenFromLocation();
  await resolveInitialAuthentication();
  await loadAuthenticatedApp();
}

void bootstrapEntry();
