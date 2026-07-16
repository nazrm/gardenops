import { t } from "../core/authI18n";
import gardenOpsLogoUrl from "../assets/gardenops-logo-auth-hero.webp";
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
  beginInvitationPasskeyRegistrationApi,
  beginPasskeyLoginApi,
  bootstrapAuthApi,
  changePasswordApi,
  clearStoredAuthToken,
  finishInvitationPasskeyRegistrationApi,
  finishPasskeyLoginApi,
  getApiErrorMessage,
  getPasswordPolicyApi,
  loginApi,
  logoutApi,
  peekInvitationApi,
  setActiveGardenContext,
  type PasswordPolicy,
  type PasskeyOptionsResponse,
} from "../services/authApi";
import { createPasskey, getPasskey, isPasskeySupported } from "./passkeys";

const authGateShells = new WeakMap<HTMLDivElement, HTMLDivElement>();
const AUTH_GATE_ACTIVE_CLASS = "auth-gate-active";

function isPasskeyUserCancelled(err: unknown): boolean {
  return err instanceof DOMException && err.name === "NotAllowedError";
}

function createAuthGateError(message: string): HTMLDivElement {
  const error = document.createElement("div");
  error.className = "auth-gate-error";
  error.setAttribute("role", "alert");
  error.setAttribute("aria-live", "assertive");
  error.textContent = message;
  return error;
}

function activateAuthGate(resolve: () => void): () => void {
  document.body.classList.add(AUTH_GATE_ACTIVE_CLASS);
  let settled = false;
  return () => {
    if (settled) return;
    settled = true;
    document.body.classList.remove(AUTH_GATE_ACTIVE_CLASS);
    resolve();
  };
}

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
  logo.width = 600;
  logo.height = 258;
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

  if (heading) {
    card.append(heading);
  }
  if (subtitle) {
    const subtitleEl = document.createElement("p");
    subtitleEl.className = "auth-gate-subtitle";
    subtitleEl.textContent = subtitle;
    card.append(subtitleEl);
  }
}

export function showForcedPasswordChangeGate(
  username: string,
): Promise<void> {
  return new Promise((resolve) => {
    const app = document.getElementById("app");
    if (!app) return;
    const complete = activateAuthGate(resolve);

    const gate = document.createElement("div");
    gate.className = "auth-gate";
    const card = createAuthGateCard(
      t("auth.password_change_required_subtitle", { username }),
    );
    appendAuthGateCard(gate, card);
    document.body.prepend(gate);
    renderForcedPasswordChangeForm(gate, card, username, "", complete);
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
    const complete = activateAuthGate(resolve);

    const gate = document.createElement("div");
    gate.className = "auth-gate";

    if (inviteToken) {
      renderInviteFlow(
        gate,
        createAuthGateCard,
        inviteToken,
        bootstrapRequired,
        passkeysEnabled,
        complete,
      );
    } else {
      renderLoginFlow(
        gate,
        createAuthGateCard,
        bootstrapRequired,
        passkeysEnabled,
        complete,
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
      form.appendChild(createAuthGateError(getApiErrorMessage(err)));
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
  const passkeyAvailable = passkeysEnabled && isPasskeySupported();
  const passkeyBtn = document.createElement("button");
  passkeyBtn.type = "button";
  passkeyBtn.className = "auth-gate-secondary-action";
  passkeyBtn.textContent = t("auth.use_passkey");

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
  if (passkeyAvailable) {
    form.prepend(passkeyBtn);
  }
  card.appendChild(form);

  passwordInput.focus();

  const renderAccepted = (acceptedUsername: string, gardenId: number | null): void => {
    if (gardenId !== null) {
      setActiveGardenContext(gardenId);
    }
    card.replaceChildren();
    appendAuthGateHeader(
      card,
      t("auth.welcome"),
      t("auth.signed_in_as", { username: acceptedUsername }),
    );
    const continueBtn = document.createElement("button");
    continueBtn.textContent = t("auth.continue");
    continueBtn.addEventListener("click", () => {
      gate.remove();
      resolve();
    });
    card.append(continueBtn);
  };

  // Gate submit button based on checklist
  passwordInput.addEventListener("input", updateGate);

  skipBtn.addEventListener("click", () => {
    checklist.destroy();
    clearPrimedInviteToken();
    gate.remove();
    void showAuthGate(bootstrapRequired, passkeysEnabled).then(resolve);
  });

  passkeyBtn.addEventListener("click", async () => {
    if (!passkeyAvailable) return;
    gate.querySelector(".auth-gate-error")?.remove();
    passkeyBtn.disabled = true;
    submitBtn.disabled = true;
    passkeyBtn.textContent = t("auth.creating_passkey");
    try {
      const options = await beginInvitationPasskeyRegistrationApi(inviteToken, username);
      const credential = await createPasskey(options.publicKey);
      const result = await finishInvitationPasskeyRegistrationApi(
        options.challenge_token,
        t("auth.passkey_default_name"),
        credential,
      );
      checklist.destroy();
      clearPrimedInviteToken();
      clearStoredAuthToken();
      renderAccepted(result.username, result.garden_id);
    } catch (err) {
      passkeyBtn.disabled = false;
      submitBtn.disabled = false;
      submitBtn.classList.toggle(
        "gated",
        !checklist.allPassed(),
      );
      passkeyBtn.textContent = t("auth.use_passkey");
      if (isPasskeyUserCancelled(err)) return;
      form.appendChild(createAuthGateError(getApiErrorMessage(err)));
    }
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
      renderAccepted(result.username, result.garden_id);
    } catch (err) {
      submitBtn.disabled = false;
      submitBtn.textContent = t("auth.accept_invitation");
      submitBtn.classList.toggle(
        "gated",
        !checklist.allPassed(),
      );
      form.appendChild(createAuthGateError(getApiErrorMessage(err)));
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
  const usernameLabelText = document.createElement("span");
  usernameLabelText.className = "auth-gate-field-label";
  usernameLabelText.textContent = t("auth.username");
  usernameLabel.append(usernameLabelText);
  const usernameInput =
    document.createElement("input");
  usernameInput.type = "text";
  usernameInput.name = "username";
  usernameInput.autocomplete = "username";
  usernameInput.required = true;
  if (bootstrapRequired) {
    usernameLabel.appendChild(usernameInput);
  } else {
    usernameLabel.className = "auth-gate-identity-label auth-gate-username-label";
    usernameInput.placeholder = t("auth.username");
    const usernameField = document.createElement("span");
    usernameField.className = "auth-gate-identity-field";
    usernameField.appendChild(usernameInput);
    usernameLabel.appendChild(usernameField);
  }

  const passwordLabel =
    document.createElement("label");
  const passwordLabelText = document.createElement("span");
  passwordLabelText.className = "auth-gate-field-label";
  passwordLabelText.textContent = t("auth.password");
  passwordLabel.append(passwordLabelText);
  const passwordInput =
    document.createElement("input");
  passwordInput.type = "password";
  passwordInput.name = "password";
  passwordInput.autocomplete = "current-password";
  passwordInput.required = true;
  if (bootstrapRequired) {
    passwordLabel.appendChild(passwordInput);
  } else {
    passwordLabel.className = "auth-gate-identity-label auth-gate-password-label";
    passwordInput.placeholder = t("auth.password");
    const passwordField = document.createElement("span");
    passwordField.className = "auth-gate-identity-field";
    passwordField.appendChild(passwordInput);
    passwordLabel.appendChild(passwordField);
  }

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
  const passwordFallbackBtn = document.createElement("button");
  passwordFallbackBtn.type = "button";
  passwordFallbackBtn.id = "auth-gate-use-password";
  passwordFallbackBtn.className = "auth-gate-secondary-action";
  passwordFallbackBtn.textContent = t("auth.use_password_instead");
  passwordFallbackBtn.hidden = true;

  const passkeyAvailable = !bootstrapRequired && passkeysEnabled && isPasskeySupported();
  type LoginStep = "username" | "passkey" | "password";
  let loginStep: LoginStep = bootstrapRequired ? "password" : "username";
  let passkeyAttempt = 0;
  let passkeyAbortController: AbortController | null = null;

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
    passwordFallbackBtn,
  );

  const setLoginStep = (step: LoginStep): void => {
    loginStep = step;
    if (bootstrapRequired) {
      passwordLabel.hidden = false;
      passwordInput.required = true;
      submitBtn.textContent = t("auth.create_account");
      return;
    }

    passwordLabel.hidden = true;
    passwordInput.required = false;
    passwordFallbackBtn.hidden = step !== "passkey";

    if (step === "username") {
      passwordInput.value = "";
      submitBtn.disabled = false;
      submitBtn.textContent = t("auth.enter_action");
      return;
    }

    if (step === "passkey") {
      submitBtn.disabled = true;
      submitBtn.textContent = t("auth.passkey_signing_in");
      return;
    }

    passwordLabel.hidden = false;
    passwordInput.required = true;
    submitBtn.disabled = false;
    submitBtn.textContent = t("auth.login_action");
  };

  setLoginStep(loginStep);
  card.appendChild(form);
  appendAuthGateCard(gate, card);
  document.body.prepend(gate);

  usernameInput.focus();

  const removeAuthGateError = (): void => {
    gate
      .querySelector(".auth-gate-error")
      ?.remove();
  };

  const showPasskeyError = (err: unknown, showCancelled: boolean): void => {
    if (err instanceof DOMException && err.name === "AbortError") {
      return;
    }
    const isCancelled = isPasskeyUserCancelled(err);
    if (isCancelled && !showCancelled) {
      return;
    }
    const errDiv = createAuthGateError(isCancelled
      ? t("auth.passkey_cancelled")
      : getApiErrorMessage(err));
    form.appendChild(errDiv);
  };

  const revealPasswordLogin = (): void => {
    setLoginStep("password");
    submitBtn.disabled = false;
    passwordInput.focus();
  };

  passwordFallbackBtn.addEventListener("click", () => {
    passkeyAttempt += 1;
    passkeyAbortController?.abort();
    passkeyAbortController = null;
    revealPasswordLogin();
  });

  const startPasskeyLogin = async (
    options: PasskeyOptionsResponse,
    username: string,
  ): Promise<void> => {
    const attempt = ++passkeyAttempt;
    const abortController = new AbortController();
    passkeyAbortController?.abort();
    passkeyAbortController = abortController;
    setLoginStep("passkey");
    try {
      const credential = await getPasskey(options.publicKey, abortController.signal);
      if (attempt !== passkeyAttempt || loginStep !== "passkey") return;
      if (usernameInput.value.trim() !== username) {
        revealPasswordLogin();
        return;
      }
      await finishPasskeySignIn(options.challenge_token, credential);
    } catch (err) {
      if (attempt !== passkeyAttempt || loginStep !== "passkey") return;
      showPasskeyError(err, true);
      revealPasswordLogin();
    } finally {
      if (passkeyAbortController === abortController) passkeyAbortController = null;
    }
  };

  const resolveUsernameLoginStep = async (username: string): Promise<void> => {
    submitBtn.disabled = true;
    submitBtn.textContent = t("auth.signing_in");
    removeAuthGateError();

    try {
      if (passkeyAvailable) {
        const options = await beginPasskeyLoginApi(username);
        await startPasskeyLogin(options, username);
        return;
      }
      revealPasswordLogin();
    } catch {
      revealPasswordLogin();
    } finally {
      if (gate.isConnected) {
        submitBtn.disabled = false;
      }
    }
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
      form.appendChild(createAuthGateError(t("auth.passkey_password_change_required")));
      revealPasswordLogin();
      return;
    }
    clearStoredAuthToken();
    gate.remove();
    resolve();
  };

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = usernameInput.value.trim();
    if (!username) {
      usernameInput.focus();
      return;
    }
    if (!bootstrapRequired && loginStep === "username") {
      await resolveUsernameLoginStep(username);
      return;
    }
    if (!bootstrapRequired && loginStep === "passkey") {
      return;
    }
    const password = passwordInput.value;
    if (!password) {
      passwordInput.focus();
      return;
    }
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
      if (awaitingMfa) {
        submitBtn.textContent = t("auth.verify_sign_in");
      } else {
        setLoginStep(loginStep);
      }
      form.appendChild(createAuthGateError(getApiErrorMessage(err)));
    }
  });

  usernameInput.addEventListener("input", () => {
    if (bootstrapRequired || awaitingMfa || loginStep === "username") {
      return;
    }
    removeAuthGateError();
    setLoginStep("username");
  });
}
