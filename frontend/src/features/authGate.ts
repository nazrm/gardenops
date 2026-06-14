import { t } from "../core/i18n";
import gardenOpsLogoUrl from "../assets/gardenops-logo-auth-hero.webp";
import type { PasswordPolicy } from "../core/models";
import {
  clearPrimedInviteToken,
  peekPrimedInviteToken,
} from "../core/urlSecurity";
import {
  renderPasswordChecklist,
  type ChecklistHandle,
} from "../components/passwordChecklist";
import {
  ApiError,
  acceptInvitationApi,
  beginPasskeyLoginApi,
  bootstrapAuthApi,
  changePasswordApi,
  clearStoredAuthToken,
  finishPasskeyLoginApi,
  getApiErrorMessage,
  getPasswordPolicyApi,
  loginApi,
  logoutApi,
  peekInvitationApi,
  setActiveGardenContext,
} from "../services/api";
import { getPasskey, isConditionalPasskeyLoginSupported, isPasskeySupported } from "./passkeys";

const authGateShells = new WeakMap<HTMLDivElement, HTMLDivElement>();

function createAuthGateCard(
  subtitle: string,
): HTMLDivElement {
  const shell = document.createElement("div");
  shell.className = "auth-gate-shell";

  const brand = document.createElement("div");
  brand.className = "auth-gate-brand";
  brand.appendChild(createAuthGateLogo());

  const card = document.createElement("div");
  card.className = "auth-gate-card";
  appendAuthGateHeader(card, t("auth.app_title"), subtitle);
  shell.append(brand, card);
  authGateShells.set(card, shell);
  return card;
}

function appendAuthGateCard(gate: HTMLDivElement, card: HTMLDivElement): void {
  gate.appendChild(authGateShells.get(card) ?? card);
}

function createAuthGateLogo(): HTMLImageElement {
  const logo = document.createElement("img");
  logo.className = "auth-gate-logo";
  logo.src = gardenOpsLogoUrl;
  logo.alt = t("auth.app_title");
  logo.width = 720;
  logo.height = 310;
  logo.decoding = "async";
  return logo;
}

function appendAuthGateHeader(
  card: HTMLDivElement,
  headingText: string,
  subtitle: string,
): void {
  const heading = headingText === t("auth.app_title")
    ? null
    : document.createElement("h1");
  if (heading) {
    heading.textContent = headingText;
  }

  const subtitleEl = document.createElement("p");
  subtitleEl.className = "auth-gate-subtitle";
  subtitleEl.textContent = subtitle;

  if (heading) {
    card.append(heading);
  }
  card.append(subtitleEl);
}

export function showForcedPasswordChangeGate(
  username: string,
): Promise<void> {
  return new Promise((resolve) => {
    const app = document.getElementById("app");
    if (!app) return;

    const gate = document.createElement("div");
    gate.className = "auth-gate";
    const card = createAuthGateCard(
      t("auth.password_change_required_subtitle", { username }),
    );
    appendAuthGateCard(gate, card);
    document.body.prepend(gate);
    renderForcedPasswordChangeForm(gate, card, username, "", resolve);
  });
}

export function showAuthGate(
  bootstrapRequired: boolean,
  passkeysEnabled = false,
): Promise<void> {
  const inviteToken = peekPrimedInviteToken();

  return new Promise((resolve) => {
    const app = document.getElementById("app");
    if (!app) return;

    const gate = document.createElement("div");
    gate.className = "auth-gate";

    if (inviteToken) {
      renderInviteFlow(
        gate,
        createAuthGateCard,
        inviteToken,
        bootstrapRequired,
        passkeysEnabled,
        resolve,
      );
    } else {
      renderLoginFlow(
        gate,
        createAuthGateCard,
        bootstrapRequired,
        passkeysEnabled,
        resolve,
      );
    }
  });
}

function renderForcedPasswordChangeForm(
  gate: HTMLDivElement,
  card: HTMLDivElement,
  username: string,
  knownCurrentPassword: string,
  resolve: () => void,
): void {
  card.replaceChildren();
  appendAuthGateHeader(
    card,
    t("auth.password_change_required"),
    t("auth.password_change_required_subtitle", { username }),
  );

  const form = document.createElement("form");
  form.id = "auth-gate-change-password-form";

  const currentPasswordLabel = document.createElement("label");
  currentPasswordLabel.append(document.createTextNode(t("auth.current_password")));
  const currentPasswordInput = document.createElement("input");
  currentPasswordInput.type = "password";
  currentPasswordInput.name = "current_password";
  currentPasswordInput.autocomplete = "current-password";
  currentPasswordInput.required = true;
  currentPasswordInput.value = knownCurrentPassword;
  currentPasswordLabel.appendChild(currentPasswordInput);

  const newPasswordLabel = document.createElement("label");
  newPasswordLabel.append(document.createTextNode(t("auth.new_password")));
  const newPasswordInput = document.createElement("input");
  newPasswordInput.type = "password";
  newPasswordInput.name = "new_password";
  newPasswordInput.autocomplete = "new-password";
  newPasswordInput.required = true;
  newPasswordLabel.appendChild(newPasswordInput);

  const confirmPasswordLabel = document.createElement("label");
  confirmPasswordLabel.append(document.createTextNode(t("auth.confirm_new_password")));
  const confirmPasswordInput = document.createElement("input");
  confirmPasswordInput.type = "password";
  confirmPasswordInput.name = "confirm_new_password";
  confirmPasswordInput.autocomplete = "new-password";
  confirmPasswordInput.required = true;
  confirmPasswordLabel.appendChild(confirmPasswordInput);

  const checklistContainer = document.createElement("div");
  const submitBtn = document.createElement("button");
  submitBtn.type = "submit";
  submitBtn.textContent = t("auth.change_password");
  submitBtn.classList.add("gated");

  let checklist: ChecklistHandle | null = null;
  const updateGate = (): void => {
    const passwordsMatch =
      newPasswordInput.value.length > 0
      && newPasswordInput.value === confirmPasswordInput.value;
    submitBtn.classList.toggle(
      "gated",
      !checklist?.allPassed() || !passwordsMatch,
    );
  };

  void getPasswordPolicyApi().then((policy) => {
    checklist = renderPasswordChecklist(
      checklistContainer,
      newPasswordInput,
      policy,
      updateGate,
    );
    updateGate();
  });
  newPasswordInput.addEventListener("input", updateGate);
  confirmPasswordInput.addEventListener("input", updateGate);

  form.append(
    currentPasswordLabel,
    newPasswordLabel,
    confirmPasswordLabel,
    checklistContainer,
    submitBtn,
  );
  card.appendChild(form);

  if (knownCurrentPassword) {
    newPasswordInput.focus();
  } else {
    currentPasswordInput.focus();
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const currentPassword = currentPasswordInput.value;
    const newPassword = newPasswordInput.value;
    if (!currentPassword || !newPassword) return;
    if (!checklist?.allPassed()) return;
    if (newPassword !== confirmPasswordInput.value) return;

    submitBtn.disabled = true;
    submitBtn.textContent = t("auth.changing_password");
    gate.querySelector(".auth-gate-error")?.remove();

    try {
      await changePasswordApi(currentPassword, newPassword);
      clearStoredAuthToken();
      gate.remove();
      resolve();
    } catch (err) {
      submitBtn.disabled = false;
      submitBtn.textContent = t("auth.change_password");
      const errDiv = document.createElement("div");
      errDiv.className = "auth-gate-error";
      errDiv.textContent = getApiErrorMessage(err);
      form.appendChild(errDiv);
    }
  });
}

function renderInviteFlow(
  gate: HTMLDivElement,
  createGateCard: (subtitle: string) => HTMLDivElement,
  inviteToken: string,
  bootstrapRequired: boolean,
  passkeysEnabled: boolean,
  resolve: () => void,
): void {
  // Show loading state
  const loadingCard = createGateCard(
    t("auth.invite_loading"),
  );
  const loadingDiv = document.createElement("div");
  loadingDiv.className = "auth-gate-loading";
  const spinner = document.createElement("div");
  spinner.className = "auth-gate-spinner";
  loadingDiv.appendChild(spinner);
  loadingCard.appendChild(loadingDiv);
  appendAuthGateCard(gate, loadingCard);
  document.body.prepend(gate);

  // Fetch peek + policy in parallel
  void Promise.allSettled([
    peekInvitationApi(inviteToken),
    getPasswordPolicyApi(),
  ]).then(([peekResult, policyResult]) => {
    if (peekResult.status === "rejected") {
      const err = peekResult.reason;
      if (
        err instanceof ApiError &&
        [404, 410, 422].includes(err.status)
      ) {
        renderInviteInvalidCard(
          gate,
          loadingCard,
          bootstrapRequired,
          passkeysEnabled,
          resolve,
        );
        return;
      }
      renderInviteLoadErrorCard(
        gate,
        loadingCard,
        bootstrapRequired,
        passkeysEnabled,
        resolve,
        getApiErrorMessage(err),
      );
      return;
    }
    if (policyResult.status === "rejected") {
      renderInviteLoadErrorCard(
        gate,
        loadingCard,
        bootstrapRequired,
        passkeysEnabled,
        resolve,
        getApiErrorMessage(policyResult.reason),
      );
      return;
    }
    renderInviteForm(
      gate,
      loadingCard,
      inviteToken,
      peekResult.value.username,
      policyResult.value,
      bootstrapRequired,
      passkeysEnabled,
      resolve,
    );
  });
}

function renderInviteInvalidCard(
  gate: HTMLDivElement,
  loadingCard: HTMLDivElement,
  bootstrapRequired: boolean,
  passkeysEnabled: boolean,
  resolve: () => void,
): void {
  clearPrimedInviteToken();
  loadingCard.replaceChildren();
  appendAuthGateHeader(
    loadingCard,
    t("auth.app_title"),
    t("auth.invite_invalid"),
  );
  const signInBtn = document.createElement("button");
  signInBtn.type = "button";
  signInBtn.className = "auth-gate-link-btn";
  signInBtn.textContent = t("auth.sign_in_instead");
  signInBtn.addEventListener("click", () => {
    clearPrimedInviteToken();
    gate.remove();
    void showAuthGate(bootstrapRequired, passkeysEnabled).then(resolve);
  });
  loadingCard.append(signInBtn);
}

function renderInviteLoadErrorCard(
  gate: HTMLDivElement,
  loadingCard: HTMLDivElement,
  bootstrapRequired: boolean,
  passkeysEnabled: boolean,
  resolve: () => void,
  message: string,
): void {
  loadingCard.replaceChildren();
  appendAuthGateHeader(loadingCard, t("auth.app_title"), message);
  const retryBtn = document.createElement("button");
  retryBtn.type = "button";
  retryBtn.textContent = t("common.refresh");
  retryBtn.addEventListener("click", () => {
    gate.remove();
    void showAuthGate(bootstrapRequired, passkeysEnabled).then(resolve);
  });
  const signInBtn = document.createElement("button");
  signInBtn.type = "button";
  signInBtn.className = "auth-gate-link-btn";
  signInBtn.textContent = t("auth.sign_in_instead");
  signInBtn.addEventListener("click", () => {
    clearPrimedInviteToken();
    gate.remove();
    void showAuthGate(bootstrapRequired, passkeysEnabled).then(resolve);
  });
  loadingCard.append(retryBtn, signInBtn);
}

function renderInviteForm(
  gate: HTMLDivElement,
  card: HTMLDivElement,
  inviteToken: string,
  username: string,
  policy: PasswordPolicy,
  bootstrapRequired: boolean,
  passkeysEnabled: boolean,
  resolve: () => void,
): void {
  card.replaceChildren();
  appendAuthGateHeader(
    card,
    t("auth.app_title"),
    t("auth.invite_welcome", { username }),
  );

  const form = document.createElement("form");
  form.id = "auth-gate-invite-form";

  const passwordLabel = document.createElement("label");
  passwordLabel.append(
    document.createTextNode(t("auth.password")),
  );
  const passwordInput = document.createElement("input");
  passwordInput.type = "password";
  passwordInput.name = "password";
  passwordInput.autocomplete = "new-password";
  passwordInput.required = true;
  passwordLabel.appendChild(passwordInput);

  const checklistContainer = document.createElement("div");
  const submitBtn = document.createElement("button");
  submitBtn.type = "submit";
  submitBtn.textContent = t("auth.accept_invitation");
  submitBtn.classList.add("gated");

  const updateGate = (): void => {
    submitBtn.classList.toggle(
      "gated",
      !checklist.allPassed(),
    );
  };

  const checklist = renderPasswordChecklist(
    checklistContainer,
    passwordInput,
    policy,
    updateGate,
  );

  const skipBtn = document.createElement("button");
  skipBtn.type = "button";
  skipBtn.id = "auth-gate-skip-invite";
  skipBtn.className = "auth-gate-link-btn";
  skipBtn.textContent = t("auth.sign_in_instead");

  form.append(
    passwordLabel,
    checklistContainer,
    submitBtn,
    skipBtn,
  );
  card.appendChild(form);

  passwordInput.focus();

  // Gate submit button based on checklist
  passwordInput.addEventListener("input", updateGate);

  skipBtn.addEventListener("click", () => {
    checklist.destroy();
    clearPrimedInviteToken();
    gate.remove();
    void showAuthGate(bootstrapRequired, passkeysEnabled).then(resolve);
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const password = passwordInput.value;
    if (!password || !checklist.allPassed()) return;

    submitBtn.disabled = true;
    submitBtn.textContent = t("auth.accepting");
    gate
      .querySelector(".auth-gate-error")
      ?.remove();

    try {
      const result = await acceptInvitationApi(
        inviteToken,
        password,
      );
      checklist.destroy();
      clearPrimedInviteToken();
      const loginResult = await loginApi(result.username, password);
      clearStoredAuthToken();
      if (
        loginResult.status === "password_change_required"
        || loginResult.user.must_change_password
      ) {
        renderForcedPasswordChangeForm(
          gate,
          card,
          loginResult.user.username,
          password,
          resolve,
        );
        return;
      }
      if (result.garden_id !== null) {
        setActiveGardenContext(result.garden_id);
      }
      card.replaceChildren();
      appendAuthGateHeader(
        card,
        t("auth.welcome"),
        t("auth.signed_in_as", { username: result.username }),
      );
      const continueBtn = document.createElement("button");
      continueBtn.textContent = t("auth.continue");
      continueBtn.addEventListener("click", () => {
        gate.remove();
        resolve();
      });
      card.append(continueBtn);
    } catch (err) {
      submitBtn.disabled = false;
      submitBtn.textContent = t("auth.accept_invitation");
      submitBtn.classList.toggle(
        "gated",
        !checklist.allPassed(),
      );
      const errDiv = document.createElement("div");
      errDiv.className = "auth-gate-error";
      errDiv.textContent = getApiErrorMessage(err);
      form.appendChild(errDiv);
    }
  });
}

function renderLoginFlow(
  gate: HTMLDivElement,
  createGateCard: (subtitle: string) => HTMLDivElement,
  bootstrapRequired: boolean,
  passkeysEnabled: boolean,
  resolve: () => void,
): void {
  const card = createGateCard(
    bootstrapRequired
      ? t("auth.bootstrap_subtitle")
      : t("auth.signin_subtitle"),
  );
  const form = document.createElement("form");
  form.id = "auth-gate-form";
  let awaitingMfa = false;

  const usernameLabel =
    document.createElement("label");
  usernameLabel.append(
    document.createTextNode(t("auth.username")),
  );
  const usernameInput =
    document.createElement("input");
  usernameInput.type = "text";
  usernameInput.name = "username";
  usernameInput.autocomplete = "username";
  usernameInput.required = true;
  usernameLabel.appendChild(usernameInput);

  const passwordLabel =
    document.createElement("label");
  passwordLabel.append(
    document.createTextNode(t("auth.password")),
  );
  const passwordInput =
    document.createElement("input");
  passwordInput.type = "password";
  passwordInput.name = "password";
  passwordInput.autocomplete = "current-password";
  passwordInput.required = true;
  passwordLabel.appendChild(passwordInput);

  // Password checklist for bootstrap (new account creation) only
  let bootstrapChecklist: ChecklistHandle | null = null;
  const checklistContainer = document.createElement("div");
  if (bootstrapRequired) {
    passwordInput.autocomplete = "new-password";
  }

  const mfaWrap = document.createElement("div");
  mfaWrap.hidden = true;

  const mfaLabel =
    document.createElement("label");
  mfaLabel.append(
    document.createTextNode(
      t("auth.authenticator_code"),
    ),
  );
  const mfaInput =
    document.createElement("input");
  mfaInput.type = "text";
  mfaInput.name = "mfa_code";
  mfaInput.inputMode = "numeric";
  mfaInput.autocomplete = "one-time-code";
  mfaInput.placeholder = "123456";
  mfaLabel.appendChild(mfaInput);

  const recoveryLabel =
    document.createElement("label");
  recoveryLabel.append(
    document.createTextNode(t("auth.recovery_code")),
  );
  const recoveryInput =
    document.createElement("input");
  recoveryInput.type = "text";
  recoveryInput.name = "recovery_code";
  recoveryInput.autocomplete = "off";
  recoveryInput.placeholder = "ABCD-EFGH";
  recoveryLabel.appendChild(recoveryInput);

  const mfaHint = document.createElement("p");
  mfaHint.className = "auth-gate-subtitle";
  mfaHint.textContent = t("auth.mfa_hint");

  mfaWrap.append(mfaLabel, recoveryLabel, mfaHint);

  const submitBtn =
    document.createElement("button");
  submitBtn.type = "submit";
  submitBtn.textContent = bootstrapRequired
    ? t("auth.create_account")
    : t("auth.sign_in_action");

  const passkeyBtn = document.createElement("button");
  passkeyBtn.type = "button";
  passkeyBtn.className = "auth-gate-link-btn";
  passkeyBtn.textContent = t("auth.sign_in_with_passkey");
  const passkeyAvailable = !bootstrapRequired && passkeysEnabled && isPasskeySupported();
  if (passkeyAvailable) {
    usernameInput.autocomplete = "username webauthn";
  }

  // Load policy and init checklist if bootstrap
  if (bootstrapRequired) {
    const updateBootstrapGate = (): void => {
      submitBtn.classList.toggle(
        "gated",
        !bootstrapChecklist?.allPassed(),
      );
    };
    void getPasswordPolicyApi().then((policy) => {
      bootstrapChecklist = renderPasswordChecklist(
        checklistContainer,
        passwordInput,
        policy,
        updateBootstrapGate,
      );
      submitBtn.classList.add("gated");
      passwordInput.addEventListener(
        "input",
        updateBootstrapGate,
      );
    });
  }

  form.append(
    usernameLabel,
    passwordLabel,
    checklistContainer,
    mfaWrap,
    submitBtn,
  );
  if (passkeyAvailable) {
    form.appendChild(passkeyBtn);
  }
  card.appendChild(form);
  appendAuthGateCard(gate, card);
  document.body.prepend(gate);

  usernameInput.focus();

  let conditionalPasskeyAbort: AbortController | null = null;

  const abortConditionalPasskeyLogin = (): void => {
    conditionalPasskeyAbort?.abort();
    conditionalPasskeyAbort = null;
  };

  const removeAuthGateError = (): void => {
    gate
      .querySelector(".auth-gate-error")
      ?.remove();
  };

  const showPasskeyError = (err: unknown, showCancelled: boolean): void => {
    if (err instanceof DOMException && err.name === "AbortError") {
      return;
    }
    const isCancelled = err instanceof DOMException && err.name === "NotAllowedError";
    if (isCancelled && !showCancelled) {
      return;
    }
    const errDiv = document.createElement("div");
    errDiv.className = "auth-gate-error";
    errDiv.textContent = isCancelled
      ? t("auth.passkey_cancelled")
      : getApiErrorMessage(err);
    form.appendChild(errDiv);
  };

  const finishPasskeySignIn = async (
    challengeToken: string,
    credential: unknown,
  ): Promise<void> => {
    const result = await finishPasskeyLoginApi(
      challengeToken,
      credential,
    );
    if (
      result.status === "password_change_required"
      || result.user.must_change_password
    ) {
      await logoutApi().catch(() => undefined);
      clearStoredAuthToken();
      const errDiv = document.createElement("div");
      errDiv.className = "auth-gate-error";
      errDiv.textContent = t("auth.passkey_password_change_required");
      form.appendChild(errDiv);
      return;
    }
    clearStoredAuthToken();
    gate.remove();
    resolve();
  };

  const startConditionalPasskeyLogin = async (): Promise<void> => {
    if (!passkeyAvailable || !(await isConditionalPasskeyLoginSupported())) {
      return;
    }
    conditionalPasskeyAbort = new AbortController();
    const abortController = conditionalPasskeyAbort;
    try {
      const options = await beginPasskeyLoginApi("");
      const credential = await getPasskey(options.publicKey, {
        mediation: "conditional",
        signal: abortController.signal,
      });
      if (abortController.signal.aborted) {
        return;
      }
      removeAuthGateError();
      await finishPasskeySignIn(options.challenge_token, credential);
    } catch (err) {
      showPasskeyError(err, false);
    } finally {
      if (conditionalPasskeyAbort === abortController) {
        conditionalPasskeyAbort = null;
      }
    }
  };

  void startConditionalPasskeyLogin();

  passkeyBtn.addEventListener("click", async () => {
    if (!passkeyAvailable) return;
    abortConditionalPasskeyLogin();
    submitBtn.disabled = true;
    passkeyBtn.disabled = true;
    passkeyBtn.textContent = t("auth.passkey_signing_in");
    removeAuthGateError();

    try {
      const options = await beginPasskeyLoginApi(usernameInput.value.trim());
      const credential = await getPasskey(options.publicKey);
      await finishPasskeySignIn(options.challenge_token, credential);
    } catch (err) {
      showPasskeyError(err, true);
    } finally {
      if (gate.isConnected) {
        submitBtn.disabled = false;
        passkeyBtn.disabled = false;
        passkeyBtn.textContent = t("auth.sign_in_with_passkey");
      }
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    abortConditionalPasskeyLogin();
    const username = usernameInput.value.trim();
    const password = passwordInput.value;
    if (!username || !password) return;
    if (bootstrapChecklist && !bootstrapChecklist.allPassed()) return;

    submitBtn.disabled = true;
    submitBtn.textContent = awaitingMfa
      ? t("auth.verifying")
      : t("auth.signing_in");

    gate
      .querySelector(".auth-gate-error")
      ?.remove();

    try {
      if (bootstrapRequired) {
        await bootstrapAuthApi(username, password);
      }
      const result = await loginApi(
        username,
        password,
        {
          mfaCode: mfaInput.value.trim(),
          recoveryCode: recoveryInput.value.trim(),
        },
      );
      if (result.status === "mfa_required") {
        awaitingMfa = true;
        mfaWrap.hidden = false;
        submitBtn.disabled = false;
        submitBtn.textContent = t(
          "auth.verify_sign_in",
        );
        mfaInput.focus();
        return;
      }
      if (result.status === "password_change_required" || result.user.must_change_password) {
        renderForcedPasswordChangeForm(
          gate,
          card,
          result.user.username,
          password,
          resolve,
        );
        return;
      }
      clearStoredAuthToken();
      gate.remove();
      resolve();
    } catch (err) {
      submitBtn.disabled = false;
      submitBtn.textContent = awaitingMfa
        ? t("auth.verify_sign_in")
        : bootstrapRequired
          ? t("auth.create_account")
          : t("auth.sign_in_action");
      const errDiv = document.createElement("div");
      errDiv.className = "auth-gate-error";
      errDiv.textContent = getApiErrorMessage(err);
      form.appendChild(errDiv);
    }
  });
}
