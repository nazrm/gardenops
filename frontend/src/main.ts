import "./core/trustedTypes"; // Must stay first — documents that no permissive default policy is installed
import { showAuthGate, showForcedPasswordChangeGate } from "./features/authGate";
import { getLocale, setLocale, t } from "./core/authI18n";
import {
  clearPrimedInviteToken,
  primeInviteTokenFromLocation,
} from "./core/urlSecurity";
import {
  ApiError,
  getAuthMeApi,
  getAuthStatusApi,
  getApiErrorMessage,
} from "./services/authApi";
import type { AuthUserProfile } from "./services/authApi";

type InitialAuthProfileWindow = Window & {
  __gardenopsInitialAuthProfile?: AuthUserProfile | null;
};

let authenticatedAppPromise: Promise<unknown> | null = null;

function primeAuthenticatedApp(profile: AuthUserProfile | null): void {
  (window as InitialAuthProfileWindow).__gardenopsInitialAuthProfile = profile;
}

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

async function resolveInitialAuthentication(): Promise<AuthUserProfile | null> {
  let bootstrapRequired = false;
  let passkeysEnabled = false;

  while (true) {
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
      return initialMe;
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) break;
      if (err instanceof ApiError && err.status === 503) {
        showSecurityWarningBanner(err.message);
      }
      // Unverified is not signed out: do not expose login or touch private work.
      await waitForInitialAuthRetry(getApiErrorMessage(err));
    }
  }

  await clearPrivateWorkBeforeLogin();
  try {
    const status = await getAuthStatusApi();
    bootstrapRequired = status.bootstrap_required;
    passkeysEnabled = status.passkeys_enabled;
  } catch {
    // Can't reach status either — the gate will show the real error on submit.
  }
  await showAuthGate(bootstrapRequired, passkeysEnabled);
  return null;
}

async function clearPrivateWorkBeforeLogin(): Promise<void> {
  while (true) {
    try {
      const queue = await import("./services/offlineQueue");
      queue.setOfflineQueueIdentity(null);
      const drafts = await import("./services/journalDraft");
      drafts.clearJournalDrafts();
      await queue.clearOfflineQueue();
      return;
    } catch (err) {
      await waitForInitialAuthRetry(getApiErrorMessage(err));
    }
  }
}

function waitForInitialAuthRetry(message: string): Promise<void> {
  return new Promise((resolve) => {
    const app = document.getElementById("app");
    app?.setAttribute("inert", "");
    document.body.classList.add("auth-gate-active");
    const gate = document.createElement("div");
    gate.className = "auth-gate";
    gate.id = "auth-verification-retry";
    const card = document.createElement("form");
    card.className = "auth-gate-card";
    const error = document.createElement("p");
    error.setAttribute("role", "alert");
    error.textContent = message;
    const retry = document.createElement("button");
    retry.type = "submit";
    retry.textContent = t("common.refresh");
    card.addEventListener("submit", (event) => {
      event.preventDefault();
      gate.remove();
      app?.removeAttribute("inert");
      document.body.classList.remove("auth-gate-active");
      resolve();
    }, { once: true });
    card.append(error, retry);
    gate.append(card);
    document.body.prepend(gate);
    retry.focus();
  });
}

async function bootstrapEntry(): Promise<void> {
  primeInviteTokenFromLocation();
  primeAuthenticatedApp(await resolveInitialAuthentication());
  await loadAuthenticatedApp();
}

void bootstrapEntry();
