import { t } from "../core/authI18n";
import { checkHibpApi, type PasswordPolicy } from "../services/authApi";

export interface ChecklistHandle {
  destroy(): void;
  allPassed(): boolean;
}

interface RuleResult {
  key: string;
  label: string;
  passed: boolean;
}

function evaluateRules(
  password: string,
  policy: PasswordPolicy,
): RuleResult[] {
  const rules: RuleResult[] = [];
  rules.push({
    key: "min_length",
    label: t("auth.password_checklist.min_length", {
      count: policy.min_length,
    }),
    passed: password.length >= policy.min_length,
  });
  if (policy.require_lower) {
    rules.push({
      key: "lowercase",
      label: t("auth.password_checklist.lowercase"),
      passed: /[a-z]/.test(password),
    });
  }
  if (policy.require_upper) {
    rules.push({
      key: "uppercase",
      label: t("auth.password_checklist.uppercase"),
      passed: /[A-Z]/.test(password),
    });
  }
  if (policy.require_digit) {
    rules.push({
      key: "digit",
      label: t("auth.password_checklist.digit"),
      passed: /\d/.test(password),
    });
  }
  if (policy.require_symbol) {
    rules.push({
      key: "symbol",
      label: t("auth.password_checklist.symbol"),
      passed: /[!@#$%^&*()\-_=+\[\]{};:,.?/|~`]/.test(password),
    });
  }
  return rules;
}

export function renderPasswordChecklist(
  container: HTMLElement,
  passwordInput: HTMLInputElement,
  policy: PasswordPolicy,
  onChange?: () => void,
): ChecklistHandle {
  const root = document.createElement("div");
  root.className = "password-checklist";
  root.hidden = true;
  container.appendChild(root);

  let localPassed = false;
  let hibpStatus: "idle" | "checking" | "ok" | "breached" | "error" =
    "idle";
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;

  function render(rules: RuleResult[]): void {
    root.replaceChildren();

    // Progress bar
    const bar = document.createElement("div");
    bar.className = "password-progress-bar";
    const passedCount = rules.filter((r) => r.passed).length;
    const totalSegments =
      rules.length + (policy.check_hibp ? 1 : 0);
    for (let i = 0; i < totalSegments; i++) {
      const seg = document.createElement("div");
      seg.className = "password-progress-segment";
      if (i < passedCount) {
        seg.classList.add("filled");
      } else if (
        i === rules.length &&
        policy.check_hibp &&
        (hibpStatus === "ok" || hibpStatus === "error")
      ) {
        seg.classList.add("filled");
      }
      bar.appendChild(seg);
    }
    root.appendChild(bar);

    // Checklist items
    for (const rule of rules) {
      const item = document.createElement("div");
      item.className = `password-checklist-item${rule.passed ? " met" : ""}`;
      const icon = document.createElement("span");
      icon.className = "password-checklist-icon";
      icon.textContent = rule.passed ? "\u2713" : "\u25CB";
      const label = document.createElement("span");
      label.textContent = rule.label;
      item.append(icon, label);
      root.appendChild(item);
    }

    // HIBP line (only when all local rules pass)
    if (policy.check_hibp && localPassed) {
      const hibpItem = document.createElement("div");
      hibpItem.className =
        "password-checklist-item hibp-appear";
      const hibpIcon = document.createElement("span");
      hibpIcon.className = "password-checklist-icon";
      const hibpLabel = document.createElement("span");

      if (hibpStatus === "checking") {
        hibpIcon.textContent = "\u23F3";
        hibpLabel.textContent = t(
          "auth.password_checklist.hibp_checking",
        );
      } else if (hibpStatus === "ok") {
        hibpItem.classList.add("met");
        hibpIcon.textContent = "\u2713";
        hibpLabel.textContent = t(
          "auth.password_checklist.hibp_ok",
        );
      } else if (hibpStatus === "breached") {
        hibpItem.classList.add("breached");
        hibpIcon.textContent = "\u2717";
        hibpLabel.textContent = t(
          "auth.password_checklist.hibp_breached",
        );
      } else if (hibpStatus === "error") {
        hibpItem.classList.add("error");
        hibpIcon.textContent = "\u2014";
        hibpLabel.textContent = t(
          "auth.password_checklist.hibp_error",
        );
      }

      hibpItem.append(hibpIcon, hibpLabel);
      root.appendChild(hibpItem);
    }

    // Static informational notes
    if (policy.reject_common || policy.disallow_username) {
      const notes = document.createElement("div");
      notes.className = "password-checklist-notes";
      if (policy.reject_common) {
        const note = document.createElement("div");
        note.className = "password-checklist-note";
        note.textContent = t(
          "auth.password_checklist.reject_common",
        );
        notes.appendChild(note);
      }
      if (policy.disallow_username) {
        const note = document.createElement("div");
        note.className = "password-checklist-note";
        note.textContent = t(
          "auth.password_checklist.disallow_username",
        );
        notes.appendChild(note);
      }
      root.appendChild(notes);
    }
  }

  function update(): void {
    const password = passwordInput.value;
    root.hidden = password.length === 0;
    if (password.length === 0) {
      hibpStatus = "idle";
      localPassed = false;
      return;
    }

    const rules = evaluateRules(password, policy);
    const allLocalPassed = rules.every((r) => r.passed);

    if (allLocalPassed && !localPassed) {
      // Local rules just became satisfied — trigger HIBP
      localPassed = true;
      if (policy.check_hibp) {
        triggerHibpCheck(password);
      }
    } else if (!allLocalPassed) {
      localPassed = false;
      hibpStatus = "idle";
      if (debounceTimer !== null) {
        clearTimeout(debounceTimer);
        debounceTimer = null;
      }
    } else if (allLocalPassed && localPassed && policy.check_hibp) {
      // Password changed while local rules still pass — re-check HIBP
      triggerHibpCheck(password);
    }

    render(rules);
  }

  function triggerHibpCheck(password: string): void {
    if (debounceTimer !== null) {
      clearTimeout(debounceTimer);
    }
    hibpStatus = "checking";
    const capturedPassword = password;
    debounceTimer = setTimeout(() => {
      checkHibpApi(capturedPassword)
        .then((result) => {
          // Only update if password hasn't changed
          if (passwordInput.value === capturedPassword) {
            hibpStatus = result.breached ? "breached" : "ok";
            render(
              evaluateRules(passwordInput.value, policy),
            );
            onChange?.();
          }
        })
        .catch(() => {
          if (passwordInput.value === capturedPassword) {
            hibpStatus = "error";
            render(
              evaluateRules(passwordInput.value, policy),
            );
            onChange?.();
          }
        });
    }, 500);
  }

  passwordInput.addEventListener("input", update);

  return {
    destroy(): void {
      passwordInput.removeEventListener("input", update);
      if (debounceTimer !== null) {
        clearTimeout(debounceTimer);
      }
      root.remove();
    },
    allPassed(): boolean {
      if (passwordInput.value.length === 0) return false;
      const rules = evaluateRules(passwordInput.value, policy);
      if (!rules.every((r) => r.passed)) return false;
      if (policy.check_hibp) {
        return hibpStatus === "ok" || hibpStatus === "error";
      }
      return true;
    },
  };
}
