import { escapeHtml, sanitizeUrl, setReviewedDynamicHtml } from "../core/sanitize";
import { getLocaleTag, t } from "../core/i18n";
import { buildInvitationLink } from "../core/urlSecurity";
import { queryInput, querySelect, queryTextArea } from "../core/dom";
import { getApiErrorMessage } from "../services/api";
import { featuresLostOnDowngrade } from "../core/featureGates";
import { confirmDialog, promptDialog } from "./dialogCore";
import { showToast } from "./toast";
import { clearOfflineQueue } from "../services/offlineQueue";
import type {
  ActiveSession,
  AdminSystemHealth,
  AuditEvent,
  AuditEventPage,
  EmergencyReadOnlyStatus,
  AuthMfaState,
  AuthManagedUser,
  AuthUserProfile,
  GardenMembership,
  GardenSettings,
  GardenInvitation,
  GardenSummary,
  MeSettings,
  MissingPlantCoverReportItem,
  PopulatePlantCoverResultItem,
  UserInvitation,
  SecurityAlert,
  SecurityAlertsResponse,
  SecurityMetrics,
} from "../services/api";
import {
  confirmAuthTotpEnrollmentApi,
  createAuthUserApi,
  deleteAuthUserApi,
  disableAuthMfaApi,
  getAuthAuditEventsApi,
  getAuthMeApi,
  getAuthMeSettingsApi,
  getSecurityAlertsApi,
  getAuthSessionsApi,
  getSecurityMetricsApi,
  getAuthUsersApi,
  getEmergencyReadOnlyApi,
  getGardenMembershipsApi,
  getGardenInvitationsApi,
  createGardenInvitationApi,
  deleteGardenApi,
  deleteGardenMembershipApi,
  getAdminSystemHealthApi,
  getUserInvitationsApi,
  issueUserResetTokenApi,
  logoutApi,
  clearStoredAuthToken,
  regenerateAuthMfaRecoveryCodesApi,
  reauthenticateApi,
  restartUserOnboardingApi,
  revokeAllSessionsApi,
  revokeGardenInvitationApi,
  revokeUserInvitationApi,
  revokeUserSessionsByIdApi,
  setEmergencyReadOnlyApi,
  startAuthTotpEnrollmentApi,
  getGardenSettingsApi,
  getMissingPlantCoversApi,
  populateMissingPlantCoversApi,
  updateAuthMeSettingsApi,
  updateGardenSettingsApi,
  updateAuthUserApi,
  updateUserTierApi,
  createUserInvitationApi,
} from "../services/api";

const esc = escapeHtml;

type AdminSection = "settings" | "garden" | "users" | "sessions" | "audit" | "invitations" | "system";

interface AdminState {
  section: AdminSection;
  users: AuthManagedUser[];
  sessions: ActiveSession[];
  audit: AuditEventPage | null;
  auditOffset: number;
  invitations: GardenInvitation[];
  userInvitations: UserInvitation[];
  gardenMemberships: GardenMembership[];
  lastInviteLink: string;
  me: AuthUserProfile | null;
  meSettings: MeSettings | null;
  gardenSettings: GardenSettings | null;
  mfaEnrollment: {
    secret: string;
    provisioning_uri: string;
    expires_at_ms: number;
  } | null;
  latestRecoveryCodes: string[];
  emergencyReadOnly: EmergencyReadOnlyStatus;
  systemHealth: AdminSystemHealth | null;
  securityMetrics: SecurityMetrics | null;
  securityAlerts: SecurityAlertsResponse | null;
  plantCoverImport: {
    running: boolean;
    total: number;
    processed: number;
    remaining: number;
    adoptedExisting: number;
    importedRemote: number;
    skipped: number;
    lastItems: PopulatePlantCoverResultItem[];
  };
  missingPlantCovers: MissingPlantCoverReportItem[];
  missingPlantCoversTotal: number;
}

const AUDIT_PAGE_SIZE = 40;

const state: AdminState = {
  section: "settings",
  users: [],
  sessions: [],
  audit: null,
  auditOffset: 0,
  invitations: [],
  userInvitations: [],
  gardenMemberships: [],
  lastInviteLink: "",
  me: null,
  meSettings: null,
  gardenSettings: null,
  mfaEnrollment: null,
  latestRecoveryCodes: [],
  emergencyReadOnly: { enabled: false, expires_at_ms: null },
  systemHealth: null,
  securityMetrics: null,
  securityAlerts: null,
  plantCoverImport: {
    running: false,
    total: 0,
    processed: 0,
    remaining: 0,
    adoptedExisting: 0,
    importedRemote: 0,
    skipped: 0,
    lastItems: [],
  },
  missingPlantCovers: [],
  missingPlantCoversTotal: 0,
};

let onSignOut: (() => void) | null = null;
let onAuthStateChanged: (() => void) | null = null;
let onGardenStateChanged: (() => Promise<void>) | null = null;
let onRestartOnboarding: (() => Promise<void>) | null = null;
let gardenContextFn: (() => { gardens: GardenSummary[]; activeGardenId: number | null }) | null = null;
let adminPanelInitialized = false;

export function setAdminCallbacks(cbs: {
  onSignOut: () => void;
  onAuthStateChanged: () => void;
  onGardenStateChanged: () => Promise<void>;
  onRestartOnboarding: () => Promise<void>;
  getGardenContext: () => { gardens: GardenSummary[]; activeGardenId: number | null };
}): void {
  onSignOut = cbs.onSignOut;
  onAuthStateChanged = cbs.onAuthStateChanged;
  onGardenStateChanged = cbs.onGardenStateChanged;
  onRestartOnboarding = cbs.onRestartOnboarding;
  gardenContextFn = cbs.getGardenContext;
}

// ── Rendering helpers ──────────────────────────────────────

function fmtDate(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return "\u2014";
  const d = typeof value === "number" ? new Date(value) : new Date(String(value));
  if (Number.isNaN(d.getTime())) return "\u2014";
  return d.toLocaleString(getLocaleTag(), {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function badge(text: string, variant: "green" | "red" | "muted" | "amber" | "blue" | "purple" = "muted"): string {
  return `<span class="adm-badge adm-badge--${variant}">${esc(text)}</span>`;
}

function tierBadge(tier: string): string {
  const map: Record<string, [string, "muted" | "blue" | "purple"]> = {
    home: [t("admin_panel.tier_home"), "muted"],
    enthusiast: [t("admin_panel.tier_enthusiast"), "blue"],
    pro: [t("admin_panel.tier_pro"), "purple"],
  };
  const [label, variant] = map[tier] ?? [t("admin_panel.tier_home"), "muted"];
  return badge(label, variant);
}

function roleSelect(id: string, current: string): string {
  return `<select class="${id}" data-prev="${esc(current)}">
    ${(["viewer", "editor", "admin"] as const).map(r =>
    `<option value="${r}"${r === current ? " selected" : ""}>${r}</option>`
  ).join("")}
  </select>`;
}

function renderManagedGardenMeta(user: AuthManagedUser): string {
  if (!user.managed_garden_id || !user.managed_garden_name) {
    return user.managed_garden_count > 0
      ? `<span class="adm-meta">${esc(t("admin.managed_gardens_count", { count: user.managed_garden_count }))}</span>`
      : "";
  }
  const onboardingBadge = user.managed_garden_onboarding_complete === false
    ? badge(t("admin.badge.onboarding_open"), "red")
    : badge(t("admin.badge.setup_complete"), "green");
  const suffix = user.managed_garden_count > 1
    ? `<span class="adm-meta">${esc(t("admin.managed_gardens_count", { count: user.managed_garden_count }))}</span>`
    : "";
  return `
    <span class="adm-meta">${esc(t("admin.garden_meta_prefix"))}: ${esc(user.managed_garden_name)}</span>
    ${onboardingBadge}
    ${suffix}
  `;
}

async function promptRequired(promptText: string, defaultValue = ""): Promise<string | null> {
  const value = (await promptDialog(promptText, defaultValue))?.trim() ?? "";
  return value || null;
}

function formatGardenScope(gardenId: number | null | undefined): string {
  return gardenId === null || gardenId === undefined ? t("common.na") : t("garden.scope_label", { id: gardenId });
}

function fmtRate(value: number | undefined): string {
  return Number.isFinite(value) ? String(value ?? 0) : "0";
}

function fmtDurationSeconds(value: number | null | undefined): string {
  if (!Number.isFinite(value)) return "\u2014";
  const totalSeconds = Math.max(0, Math.round(Number(value)));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function fmtMaybeDate(value: string | null | undefined): string {
  if (!value) return "\u2014";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return fmtDate(value);
}

function renderSystemHealthStatusBadge(status: AdminSystemHealth["status"] | null): string {
  if (!status) return badge(t("admin_panel.health_status_unavailable"), "muted");
  if (status === "ok") return badge(t("admin_panel.health_status_ok"), "green");
  if (status === "degraded") return badge(t("admin_panel.health_status_degraded"), "amber");
  return badge(t("admin_panel.health_status_corrupt"), "red");
}

function renderProviderScopeUsage(
  label: string,
  usage: { scope_id: number; request_count: number; limit: number } | null,
): string {
  if (!usage) return `<span class="adm-meta">${esc(label)}: ${esc(t("admin_panel.no_usage_today"))}</span>`;
  const limit = usage.limit > 0 ? usage.limit : 0;
  const percent = limit > 0 ? Math.min(999, Math.round((usage.request_count / limit) * 100)) : 0;
  return `<span class="adm-meta">${esc(label)} #${usage.scope_id}: ${usage.request_count}/${limit} (${percent}%)</span>`;
}

function formatMissingCoverReason(item: MissingPlantCoverReportItem): string {
  return t(`admin.garden.cover_reason_${item.reason_code}`);
}

function formatSecurityAlertName(name: string): string {
  switch (name) {
    case "shademap_features_cache_miss_spike_5m":
      return t("admin_panel.alert_shademap_feature_miss");
    case "shademap_terrain_remote_miss_spike_5m":
      return t("admin_panel.alert_shademap_terrain_miss");
    case "destructive_admin_actions_per_5m":
      return t("admin_panel.alert_destructive_admin");
    case "auth_failures_per_minute":
      return t("admin_panel.alert_auth_failures");
    case "auth_login_failures_admin_per_minute":
      return t("admin_panel.alert_admin_login_failures");
    case "rate_limit_hits_per_minute":
      return t("admin_panel.alert_rate_limit_hits");
    case "invalid_reset_password_attempts_per_5m":
      return t("admin_panel.alert_invalid_reset");
    case "invalid_invitation_attempts_per_5m":
      return t("admin_panel.alert_invalid_invitation");
    case "provider_budget_hits_per_5m":
      return t("admin_panel.alert_provider_budget");
    case "concurrency_limit_hits_per_5m":
      return t("admin_panel.alert_concurrency_limit");
    case "ai_provider_failures_per_5m":
      return t("admin_panel.alert_ai_provider_failures");
    case "shademap_upstream_failures_per_5m":
      return t("admin_panel.alert_shademap_upstream");
    default:
      return name.replaceAll("_", " ");
  }
}

function renderSecurityAlert(alert: SecurityAlert): string {
  const parts = [`${formatSecurityAlertName(alert.name)}: ${alert.value}/${alert.threshold}`];
  if (alert.ratio_pct !== undefined && alert.ratio_threshold_pct !== undefined) {
    parts.push(`${t("admin_panel.alert_ratio")} ${alert.ratio_pct}%/${alert.ratio_threshold_pct}%`);
  }
  if (alert.request_count !== undefined && alert.miss_count !== undefined) {
    parts.push(`${t("admin_panel.alert_requests")} ${alert.request_count}, ${t("admin_panel.alert_misses")} ${alert.miss_count}`);
  }
  if (Array.isArray(alert.garden_ids) && alert.garden_ids.length > 0) {
    parts.push(`${t("admin_panel.alert_gardens")} ${alert.garden_ids.join(", ")}`);
  }
  return `<li>${esc(parts.join(" · "))}</li>`;
}

function renderMfaStatusBadge(mfa: AuthMfaState | null, me: AuthUserProfile | null): string {
  if (!mfa || me?.role !== "admin") return badge(t("admin.mfa.badge_not_required"), "muted");
  if (mfa.enabled) return badge(t("admin.mfa.badge_enabled"), "green");
  if (mfa.setup_required) return badge(t("admin.mfa.badge_required"), "red");
  return badge(t("admin.mfa.badge_disabled"), "muted");
}

function isPlatformAdmin(): boolean {
  return state.me?.role === "admin";
}

function canEditActiveGarden(): boolean {
  return Boolean(state.me?.write_access);
}

function canManageActiveGardenInvitations(): boolean {
  const ctx = gardenContextFn?.();
  if (!ctx?.activeGardenId) return false;
  return isPlatformAdmin();
}

function getVisibleSections(): AdminSection[] {
  const sections: AdminSection[] = ["settings"];
  if (canEditActiveGarden()) sections.push("garden");
  if (state.me?.mfa_setup_required) return sections;
  if (canManageActiveGardenInvitations()) sections.push("invitations");
  if (isPlatformAdmin()) {
    sections.push("users", "sessions", "audit", "system");
  }
  return sections;
}

function defaultSection(): AdminSection {
  if (canEditActiveGarden()) return "garden";
  if (isPlatformAdmin() && !state.me?.mfa_setup_required) return "users";
  return "settings";
}

async function authorizeSensitiveAdminAction(
  actionLabel: string,
  defaultReason: string,
): Promise<string | null> {
  const actionReason = await promptRequired(`${actionLabel} reason:`, defaultReason);
  if (!actionReason) return null;
  if (state.me?.auth_type === "session") {
    const currentPassword = await promptRequired(
      t("admin_panel.confirm_password_prompt", { action: actionLabel.toLowerCase() }),
    );
    if (!currentPassword) return null;
    let reauthOptions: { mfaCode?: string; recoveryCode?: string } = {};
    if (state.me?.mfa_enabled) {
      const mfaCode = await promptDialog(
        t("admin_panel.enter_authenticator_code"),
        "",
      );
      if (mfaCode === null) return null;
      const normalizedCode = mfaCode.trim();
      if (normalizedCode) {
        reauthOptions = { mfaCode: normalizedCode };
      } else {
        const recoveryCode = await promptRequired(t("admin_panel.enter_recovery_code"));
        if (!recoveryCode) return null;
        reauthOptions = { recoveryCode };
      }
    }
    await reauthenticateApi(currentPassword, reauthOptions);
  }
  return actionReason;
}

// ── Section: My Settings ───────────────────────────────────

function renderSettingsSection(): string {
  const me = state.me;
  const settings = state.meSettings;
  const mfa = settings?.mfa ?? null;
  const enrollment = state.mfaEnrollment;
  const recoveryCodes = state.latestRecoveryCodes;
  const plotAssignmentMeanings = settings?.plot_assignment_meanings ?? [];
  return `
    <div class="adm-section-header">
      <div>
        <h2 class="adm-section-title">${t("admin.settings.title")}</h2>
        <p class="adm-section-desc">${t("admin.settings.signed_in_as")} <strong>${esc(me?.username ?? "unknown")}</strong> (${esc(me?.role ?? "unknown")})</p>
      </div>
    </div>
    <div class="adm-card adm-card--form">
      <h3 class="adm-card-title">${t("admin.settings.shademap_title")}</h3>
      <p class="adm-section-desc">${t("admin.settings.shademap_desc")}</p>
      <div class="adm-form-row">
        <input type="password" id="adm-my-shademap-key" placeholder="${t("admin.settings.shademap_placeholder")}" class="adm-input" autocomplete="off" />
        <button type="button" id="adm-my-shademap-save" class="adm-btn adm-btn--primary">${t("admin.settings.shademap_save")}</button>
        <button type="button" id="adm-my-shademap-clear" class="adm-btn adm-btn--ghost">${t("admin.settings.shademap_clear")}</button>
      </div>
    </div>
    <div class="adm-card adm-card--form">
      <h3 class="adm-card-title">${t("admin.settings.plot_meanings_title")}</h3>
      <p class="adm-section-desc">${t("admin.settings.plot_meanings_desc")}</p>
      <div class="adm-form-stack adm-plot-meaning-list" id="adm-plot-meaning-list">
        ${plotAssignmentMeanings.length > 0
          ? plotAssignmentMeanings.map((meaning, index) => `
            <div class="adm-plot-meaning-row" data-index="${index}">
              <input type="text" class="adm-input adm-plot-meaning-pattern" placeholder="${t("admin.settings.plot_meanings_pattern")}" value="${esc(meaning.pattern)}" />
              <input type="text" class="adm-input adm-plot-meaning-label" placeholder="${t("admin.settings.plot_meanings_label")}" value="${esc(meaning.label)}" />
              <input type="text" class="adm-input adm-plot-meaning-description" placeholder="${t("admin.settings.plot_meanings_description")}" value="${esc(meaning.description)}" />
              <button type="button" class="adm-btn adm-btn--ghost adm-plot-meaning-delete">${t("admin.settings.plot_meanings_remove")}</button>
            </div>
          `).join("")
          : `<p class="adm-section-desc adm-plot-meaning-empty">${t("admin.settings.plot_meanings_empty")}</p>`}
      </div>
      <div class="adm-btn-group">
        <button type="button" id="adm-plot-meaning-add" class="adm-btn">${t("admin.settings.plot_meanings_add")}</button>
        <button type="button" id="adm-plot-meaning-save" class="adm-btn adm-btn--primary">${t("admin.settings.plot_meanings_save")}</button>
      </div>
    </div>
    <div class="adm-card adm-card--form">
      <h3 class="adm-card-title">${t("admin.mfa.title")}</h3>
      <p class="adm-section-desc">
        ${t("common.status")}: ${renderMfaStatusBadge(mfa, me)}
        ${mfa?.enrolled_at ? ` · ${esc(t("admin.mfa.enrolled_at", { date: fmtDate(mfa.enrolled_at) }))}` : ""}
      </p>
      ${me?.role === "admin" ? `
        <p class="adm-section-desc">
          ${mfa?.enabled
            ? t("admin.mfa.recovery_remaining", { count: mfa.recovery_codes_remaining })
            : (mfa?.setup_required
              ? t("admin.mfa.setup_required_desc")
              : t("admin.mfa.available_desc")
            )}
        </p>
        <div class="adm-btn-group">
          <button type="button" id="adm-mfa-start" class="adm-btn adm-btn--primary">${mfa?.enabled ? t("admin.mfa.restart_setup") : t("admin.mfa.start_setup")}</button>
          <button type="button" id="adm-mfa-regenerate" class="adm-btn" ${mfa?.enabled ? "" : "disabled"}>${t("admin.mfa.regenerate_recovery")}</button>
          <button type="button" id="adm-mfa-disable" class="adm-btn adm-btn--ghost" ${mfa?.enabled ? "" : "disabled"}>${t("admin.mfa.disable")}</button>
        </div>
        ${enrollment ? `
          <div class="adm-form-stack" id="adm-mfa-enrollment">
            <label>${t("admin.mfa.secret")}
              <input type="text" id="adm-mfa-secret" class="adm-input" readonly value="${esc(enrollment.secret)}" />
            </label>
            <label>${t("admin.mfa.uri")}
              <input type="text" id="adm-mfa-uri" class="adm-input" readonly value="${esc(enrollment.provisioning_uri)}" />
            </label>
            <label>${t("admin.mfa.code")}
              <input type="text" id="adm-mfa-code" class="adm-input" inputmode="numeric" placeholder="${t("admin.mfa.code_placeholder")}" />
            </label>
            <p class="adm-section-desc">${t("admin.mfa.pending_expires", { date: fmtDate(enrollment.expires_at_ms) })}</p>
            <div class="adm-btn-group">
              <button type="button" id="adm-mfa-confirm" class="adm-btn adm-btn--primary">${t("admin.mfa.enable")}</button>
            </div>
          </div>
        ` : ""}
        ${recoveryCodes.length > 0 ? `
          <div class="adm-form-stack" id="adm-mfa-recovery-codes">
            <p class="adm-section-desc">${t("admin.mfa.recovery_store")}</p>
            <textarea id="adm-mfa-recovery-output" class="adm-input" rows="6" readonly>${esc(recoveryCodes.join("\n"))}</textarea>
            <div class="adm-btn-group">
              <button type="button" id="adm-mfa-copy-recovery" class="adm-btn">${t("admin.mfa.copy_codes")}</button>
              <button type="button" id="adm-mfa-clear-recovery" class="adm-btn adm-btn--ghost">${t("admin.mfa.clear_codes")}</button>
            </div>
          </div>
        ` : ""}
      ` : `
        <p class="adm-section-desc">${t("admin.mfa.non_admin")}</p>
      `}
    </div>
  `;
}

function renderGardenSection(): string {
  const ctx = gardenContextFn?.();
  const activeGardenId = ctx?.activeGardenId ?? null;
  const activeGarden = ctx?.gardens.find((garden) => garden.id === activeGardenId) ?? null;
  const settings = state.gardenSettings;
  if (!activeGardenId || !settings) {
    return `
      <div class="adm-section-header">
        <div>
          <h2 class="adm-section-title">${t("admin.garden.title")}</h2>
          <p class="adm-section-desc">${t("admin.garden.none_active")}</p>
        </div>
      </div>
      <div class="adm-card adm-card--form">
        <p class="adm-section-desc">${t("admin.garden.none_active_help")}</p>
      </div>
    `;
  }
  const onboardingStatus = settings.onboarding_complete
    ? badge(t("admin.garden.status_complete"), "green")
    : badge(t("admin.garden.status_pending"), "amber");
  const membershipsCard = isPlatformAdmin()
    ? `
      <div class="adm-card adm-card--form">
        <h3 class="adm-card-title">${t("admin.garden.members_title")}</h3>
        <p class="adm-section-desc">${esc(t("admin.garden.members_desc", { name: settings.name }))}</p>
        <div class="adm-table-wrap">
          <table class="adm-table">
            <thead>
              <tr>
                <th>${t("common.user")}</th>
                <th>${t("common.role")}</th>
                <th>${t("common.added")}</th>
                <th>${t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              ${state.gardenMemberships.length > 0
                ? state.gardenMemberships.map((membership) => `
                  <tr class="adm-row" data-garden-member-id="${membership.user_id}">
                    <td>
                      <div class="adm-cell-user">
                        <span class="adm-username">${esc(membership.username)}</span>
                      </div>
                    </td>
                    <td>${badge(membership.role, membership.role === "admin" ? "green" : "muted")}</td>
                    <td class="adm-cell-date">${fmtDate(membership.created_at)}</td>
                    <td>
                      <div class="adm-cell-actions">
                        <button type="button" class="adm-btn adm-btn--sm adm-btn--danger adm-act-remove-garden-member">${t("common.remove")}</button>
                      </div>
                    </td>
                  </tr>
                `).join("")
                : `<tr><td colspan="4" class="adm-empty">${t("admin.garden.members_empty")}</td></tr>`}
            </tbody>
          </table>
        </div>
      </div>
    `
    : "";
  const canDeleteGarden = isPlatformAdmin() && activeGarden?.slug !== "default";
  const deleteGardenCard = isPlatformAdmin()
    ? `
      <div class="adm-card adm-card--form">
        <h3 class="adm-card-title">${t("admin.garden.delete_title")}</h3>
        <p class="adm-section-desc">${t("admin.garden.delete_desc")}</p>
        <p class="adm-section-desc">${activeGarden?.slug === "default"
          ? t("admin.garden.delete_default_blocked")
          : t("admin.garden.delete_warning")}</p>
        <div class="adm-btn-group">
          <button type="button" id="adm-garden-delete" class="adm-btn adm-btn--danger"${canDeleteGarden ? "" : " disabled"}>${t("admin.garden.delete_button")}</button>
        </div>
      </div>
    `
    : "";
  const coverImport = state.plantCoverImport;
  const coverProgressPct = coverImport.total > 0
    ? Math.max(0, Math.min(100, Math.round((coverImport.processed / coverImport.total) * 100)))
    : 0;
  const coverImportItems = coverImport.lastItems.map((item) => `
    <li>
      <strong>${esc(item.plant_id)}</strong>
      <span>${esc(t(`admin.garden.cover_status_${item.status}`))}</span>
      ${item.detail ? `<span class="adm-meta">${esc(item.detail)}</span>` : ""}
    </li>
  `).join("");
  const missingCoverRows = state.missingPlantCovers.map((item) => {
    const sourceUrl = sanitizeUrl(item.link);
    const sourceLabel = sourceUrl
      ? `<a class="adm-link" href="${esc(sourceUrl)}" target="_blank" rel="noreferrer noopener">${esc(t("admin.garden.cover_source_link"))}</a>`
      : `<span class="adm-meta">${esc(t("admin.garden.cover_source_missing"))}</span>`;
    const statusDetail = item.status_detail
      ? `<div class="adm-meta">${esc(item.status_detail)}</div>`
      : "";
    const existingMediaNote = item.has_existing_media
      ? `<div class="adm-meta">${esc(t("admin.garden.cover_existing_media_hint"))}</div>`
      : "";
    return `
      <tr>
        <td>
          <strong>${esc(item.name || item.plant_id)}</strong>
          <div class="adm-meta">${esc(item.plant_id)}</div>
        </td>
        <td>${item.latin ? esc(item.latin) : `<span class="adm-meta">${esc(t("common.na"))}</span>`}</td>
        <td>
          <span>${esc(formatMissingCoverReason(item))}</span>
          ${statusDetail}
          ${existingMediaNote}
        </td>
        <td>${item.attempted_at_ms ? esc(fmtDate(item.attempted_at_ms)) : `<span class="adm-meta">${esc(t("admin.garden.cover_not_checked"))}</span>`}</td>
        <td>${sourceLabel}</td>
      </tr>
    `;
  }).join("");
  const missingCoverReport = `
    <div class="adm-table-wrap">
      <table class="adm-table">
        <thead>
          <tr>
            <th>${t("plants.column_name")}</th>
            <th>${t("plants.column_latin")}</th>
            <th>${t("common.status")}</th>
            <th>${t("admin.garden.cover_last_checked")}</th>
            <th>${t("admin.garden.cover_source")}</th>
          </tr>
        </thead>
        <tbody>
          ${missingCoverRows}
        </tbody>
      </table>
    </div>
  `;
  const coverImportCard = isPlatformAdmin()
    ? `
      <div class="adm-card adm-card--form">
        <h3 class="adm-card-title">${t("admin.garden.cover_title")}</h3>
        <p class="adm-section-desc">${t("admin.garden.cover_desc")}</p>
        ${coverImport.total > 0 ? `
          <div class="adm-progress-track" aria-hidden="true">
            <div class="adm-progress-fill" style="width:${coverProgressPct}%"></div>
          </div>
          <p class="adm-section-desc">${t("admin.garden.cover_progress", {
            processed: coverImport.processed,
            total: coverImport.total,
            remaining: coverImport.remaining,
          })}</p>
          <p class="adm-section-desc">${t("admin.garden.cover_result", {
            adopted: coverImport.adoptedExisting,
            imported: coverImport.importedRemote,
            skipped: coverImport.skipped,
          })}</p>
          ${coverImportItems ? `<ul class="adm-compact-list">${coverImportItems}</ul>` : ""}
        ` : `<p class="adm-section-desc">${t("admin.garden.cover_idle")}</p>`}
        <p class="adm-section-desc">${t("admin.garden.cover_report_summary", {
          visible: state.missingPlantCovers.length,
          total: state.missingPlantCoversTotal,
        })}</p>
        ${state.missingPlantCoversTotal > 0 ? missingCoverReport : `<p class="adm-section-desc">${t("admin.garden.cover_report_empty")}</p>`}
        <div class="adm-btn-group">
          <button type="button" id="adm-garden-cover-import" class="adm-btn"${coverImport.running ? " disabled" : ""}>${coverImport.running ? t("admin.garden.cover_running_button") : t("admin.garden.cover_button")}</button>
          <button type="button" id="adm-garden-cover-refresh" class="adm-btn adm-btn--ghost"${coverImport.running ? " disabled" : ""}>${t("admin.garden.cover_refresh_button")}</button>
        </div>
      </div>
    `
    : "";
  return `
    <div class="adm-section-header">
      <div>
        <h2 class="adm-section-title">${t("admin.garden.title")}</h2>
        <p class="adm-section-desc">${esc(t("admin.garden.shared_desc", { name: settings.name, scope: formatGardenScope(activeGardenId) }))}</p>
      </div>
    </div>
    <div class="adm-card adm-card--form">
      <h3 class="adm-card-title">${t("admin.garden.settings_title")}</h3>
      <div class="adm-form-stack">
        <label>${t("admin.garden.name_label")}
          <input type="text" id="adm-garden-name" class="adm-input" maxlength="120" value="${esc(settings.name)}" />
        </label>
        <div class="adm-form-row">
          <label>${t("onboarding.width")}
            <input type="number" id="adm-garden-grid-cols" class="adm-input" min="5" max="100" step="1" value="${settings.grid_cols}" />
          </label>
          <label>${t("onboarding.depth")}
            <input type="number" id="adm-garden-grid-rows" class="adm-input" min="5" max="100" step="1" value="${settings.grid_rows}" />
          </label>
        </div>
        <label>${t("admin.garden.address_label")}
          <input type="text" id="adm-garden-address" class="adm-input" maxlength="500" value="${esc(settings.address)}" />
        </label>
        <div class="adm-form-row">
          <label>${t("onboarding.latitude")}
            <input type="number" id="adm-garden-latitude" class="adm-input" min="-90" max="90" step="0.0001" value="${settings.latitude ?? ""}" />
          </label>
          <label>${t("onboarding.longitude")}
            <input type="number" id="adm-garden-longitude" class="adm-input" min="-180" max="180" step="0.0001" value="${settings.longitude ?? ""}" />
          </label>
        </div>
      </div>
      <div class="adm-btn-group">
        <button type="button" id="adm-garden-save" class="adm-btn adm-btn--primary">${t("admin.garden.save_settings")}</button>
      </div>
    </div>
    <div class="adm-card adm-card--form">
      <h3 class="adm-card-title">${t("admin.garden.onboarding_title")}</h3>
      <p class="adm-section-desc">${t("common.status")}: ${onboardingStatus}</p>
      <p class="adm-section-desc">${t("admin.garden.onboarding_desc")}</p>
      <div class="adm-btn-group">
        <button type="button" id="adm-garden-onboarding" class="adm-btn">${settings.onboarding_complete ? t("admin.garden.reopen_onboarding") : t("admin.garden.resume_onboarding")}</button>
      </div>
    </div>
    ${membershipsCard}
    ${coverImportCard}
    ${deleteGardenCard}
  `;
}

// ── Section: Users ─────────────────────────────────────────

function renderUserCard(u: AuthManagedUser): string {
  const managedGardenMeta = renderManagedGardenMeta(u);
  const restartOnboardingBtn = u.managed_garden_id !== null
    ? `<button class="adm-btn adm-btn--sm adm-btn--ghost adm-act-restart-onboarding" title="${esc(t("admin_panel.redo_setup_title_full", { username: u.username, garden: u.managed_garden_name ?? `garden ${u.managed_garden_id}` }))}">${t("admin_panel.redo_setup")}</button>`
    : "";
  return `
    <div class="adm-user-card" data-uid="${u.id}">
      <div class="adm-user-card-header">
        <div class="adm-user-card-name">
          <span class="adm-username">${esc(u.username)}</span>
          <div class="adm-user-card-badges">
            ${u.is_active ? badge(t("admin_panel.badge_active"), "green") : badge(t("admin_panel.badge_inactive"), "red")}
            ${u.must_change_password ? badge(t("admin_panel.badge_must_change_pw"), "amber") : ""}
            ${u.has_shademap_key ? badge("ShadeMap", "green") : ""}
            ${u.role === "admin" ? (u.mfa_enabled ? badge(t("admin_panel.badge_mfa"), "green") : badge(t("admin_panel.badge_mfa_missing"), "red")) : ""}
            ${tierBadge(u.subscription_tier)}
          </div>
        </div>
        <select class="adm-role-sel adm-select adm-select--sm" data-prev="${esc(u.role)}">
          ${(["viewer", "editor", "admin"] as const).map(r =>
            `<option value="${r}"${r === u.role ? " selected" : ""}>${r}</option>`
          ).join("")}
        </select>
        <select class="adm-tier-sel adm-select adm-select--sm" data-prev="${esc(u.subscription_tier)}">
          ${(["home", "enthusiast", "pro"] as const).map(ti =>
            `<option value="${ti}"${ti === u.subscription_tier ? " selected" : ""}>${ti.charAt(0).toUpperCase() + ti.slice(1)}</option>`
          ).join("")}
        </select>
      </div>
      <div class="adm-user-card-meta">
        <div class="adm-user-card-meta-item">
          <span class="adm-user-card-meta-label">${t("admin_panel.label_last_login")}</span>
          <span class="adm-user-card-meta-value">${fmtDate(u.last_login_at)}</span>
        </div>
        <div class="adm-user-card-meta-item">
          <span class="adm-user-card-meta-label">${t("admin_panel.label_created")}</span>
          <span class="adm-user-card-meta-value">${fmtDate(u.created_at)}</span>
        </div>
        ${managedGardenMeta ? `<div class="adm-user-card-meta-item adm-user-card-meta-item--full">
          <span class="adm-user-card-meta-label">${t("admin_panel.label_garden")}</span>
          <span class="adm-user-card-meta-value">${managedGardenMeta}</span>
        </div>` : ""}
      </div>
      <div class="adm-user-card-actions">
        <button class="adm-btn adm-btn--sm adm-act-save">${t("common.save")}</button>
        <button class="adm-btn adm-btn--sm adm-btn--ghost adm-act-toggle">${u.is_active ? t("admin_panel.btn_deactivate") : t("admin_panel.btn_activate")}</button>
        <button class="adm-btn adm-btn--sm adm-btn--ghost adm-act-revoke-sessions">${t("admin_panel.btn_revoke")}</button>
        ${restartOnboardingBtn}
        <button class="adm-btn adm-btn--sm adm-btn--ghost adm-act-reset">${t("admin_panel.btn_reset_pw")}</button>
        <button class="adm-btn adm-btn--sm adm-btn--ghost adm-act-shademap-key">${t("admin_panel.btn_shademap_key")}</button>
        <button class="adm-btn adm-btn--sm adm-btn--danger adm-act-delete-user">${t("common.delete")}</button>
      </div>
    </div>`;
}

function renderUsersSection(): string {
  const invitationRows = state.userInvitations.map((invitation) => {
    const statusVariant = invitation.status === "pending"
      ? "amber"
      : invitation.status === "accepted"
        ? "green"
        : "red";
    return `
      <tr class="adm-row" data-user-inv-id="${invitation.id}">
        <td class="adm-cell-user"><span class="adm-username">${esc(invitation.invitee_username)}</span></td>
        <td>${badge(invitation.role, invitation.role === "admin" ? "amber" : "muted")}</td>
        <td>${badge(invitation.status, statusVariant)}</td>
        <td class="adm-cell-date">${fmtDate(invitation.created_at_ms)}</td>
        <td class="adm-cell-date">${fmtDate(invitation.expires_at_ms)}</td>
        <td class="adm-cell-actions">
          ${invitation.status === "pending" ? `<button class="adm-btn adm-btn--sm adm-btn--danger adm-act-revoke-user-inv">${t("admin_panel.btn_revoke")}</button>` : "\u2014"}
        </td>
      </tr>
    `;
  }).join("");

  const rows = state.users.map(u => `
    <tr class="adm-row" data-uid="${u.id}">
      <td class="adm-cell-user">
        <span class="adm-username">${esc(u.username)}</span>
        ${u.created_by_user_id !== null ? `<span class="adm-meta">${esc(t("admin_panel.created_by", { id: u.created_by_user_id }))}</span>` : ""}
        ${renderManagedGardenMeta(u)}
      </td>
      <td>${roleSelect("adm-role-sel", u.role)}</td>
      <td>
        <select class="adm-tier-sel adm-select adm-select--sm" data-prev="${esc(u.subscription_tier)}">
          ${(["home", "enthusiast", "pro"] as const).map(ti =>
            `<option value="${ti}"${ti === u.subscription_tier ? " selected" : ""}>${ti.charAt(0).toUpperCase() + ti.slice(1)}</option>`
          ).join("")}
        </select>
      </td>
      <td>${u.is_active ? badge(t("admin_panel.badge_active"), "green") : badge(t("admin_panel.badge_inactive"), "red")}</td>
      <td>${u.must_change_password ? badge(t("admin_panel.badge_yes"), "amber") : badge(t("admin_panel.badge_no"), "muted")}</td>
      <td>${u.role === "admin" ? (u.mfa_enabled ? badge(t("admin_panel.badge_enabled"), "green") : badge(t("admin_panel.badge_missing"), "red")) : badge(t("admin_panel.badge_na"), "muted")}</td>
      <td>${u.has_shademap_key ? badge(t("admin_panel.badge_set"), "green") : badge(t("admin_panel.badge_none"), "muted")}</td>
      <td class="adm-cell-date">${fmtDate(u.last_login_at)}</td>
      <td class="adm-cell-date">${fmtDate(u.created_at)}</td>
      <td class="adm-cell-actions">
        <button class="adm-btn adm-btn--sm adm-act-save" title="${t("admin.save_changes")}">${t("common.save")}</button>
        <button class="adm-btn adm-btn--sm adm-btn--ghost adm-act-toggle" title="${u.is_active ? t("admin.deactivate") : t("admin.activate")}">${u.is_active ? t("admin.deactivate") : t("admin.activate")}</button>
        <button class="adm-btn adm-btn--sm adm-btn--ghost adm-act-revoke-sessions" title="${t("admin.revoke_sessions")}">${t("admin.revoke")}</button>
        ${u.managed_garden_id !== null ? `<button class="adm-btn adm-btn--sm adm-btn--ghost adm-act-restart-onboarding" title="${esc(t("admin.redo_setup_title", { username: u.username, garden: u.managed_garden_name ?? `garden ${u.managed_garden_id}` }))}">${t("admin.redo_setup")}</button>` : ""}
        <button class="adm-btn adm-btn--sm adm-btn--ghost adm-act-reset" title="${t("admin.issue_reset_token")}">${t("admin.reset_pw")}</button>
        <button class="adm-btn adm-btn--sm adm-btn--ghost adm-act-shademap-key" title="${t("admin.set_shademap_key")}">${t("admin.shademap_key")}</button>
        <button class="adm-btn adm-btn--sm adm-btn--danger adm-act-delete-user" title="${t("common.delete")}">${t("common.delete")}</button>
      </td>
    </tr>
  `).join("");

  const cards = state.users.map(u => renderUserCard(u)).join("");
  const userInvitationsCard = isPlatformAdmin()
    ? `
      <div class="adm-card adm-card--form">
        <h3 class="adm-card-title">${t("admin_panel.invite_editor_or_admin")}</h3>
        <p class="adm-section-desc">${t("admin_panel.invite_editor_desc")}</p>
        <form id="adm-create-user-inv-form" class="adm-form-row">
          <input type="text" id="adm-user-inv-username" placeholder="${t("admin_panel.placeholder_username")}" required class="adm-input" />
          <select id="adm-user-inv-role" class="adm-select">
            <option value="editor">${t("admin_panel.option_editor_own_garden")}</option>
            <option value="admin">${t("admin_panel.option_admin")}</option>
          </select>
          <input type="number" id="adm-user-inv-ttl" placeholder="${t("admin_panel.placeholder_ttl")}" min="5" class="adm-input adm-input--sm" />
          <button type="submit" class="adm-btn adm-btn--primary">${t("admin_panel.btn_create_invite")}</button>
        </form>
        <div id="adm-user-inv-link-box" class="adm-inv-link-box"${state.lastInviteLink ? "" : " hidden"}>
          <label class="adm-inv-link-label">${t("admin_panel.invitation_link")}
            <div class="adm-inv-link-row">
              <input type="text" id="adm-user-inv-link-input" readonly class="adm-input adm-inv-link-input" value="${esc(state.lastInviteLink)}" />
              <button type="button" id="adm-user-inv-link-copy" class="adm-btn adm-btn--sm">${t("admin_panel.btn_copy")}</button>
            </div>
          </label>
          <p class="adm-inv-link-hint">${t("admin_panel.invite_link_hint")}</p>
        </div>
        <div class="adm-table-wrap">
          <table class="adm-table">
            <thead>
              <tr>
                <th>${t("admin_panel.th_invitee")}</th>
                <th style="width:90px">${t("common.role")}</th>
                <th style="width:100px">${t("common.status")}</th>
                <th style="width:160px">${t("admin_panel.label_created")}</th>
                <th style="width:160px">${t("admin_panel.label_expires")}</th>
                <th style="width:100px">${t("admin_panel.th_action")}</th>
              </tr>
            </thead>
            <tbody>${invitationRows || `<tr><td colspan="6" class="adm-empty">${t("admin_panel.no_personal_invites")}</td></tr>`}</tbody>
          </table>
        </div>
      </div>
    `
    : "";

  return `
    <div class="adm-section-header">
      <div>
        <h2 class="adm-section-title">${t("admin_panel.section_users")}</h2>
        <p class="adm-section-desc">${t("admin_panel.registered_accounts", { count: state.users.length })}</p>
      </div>
      <button class="adm-btn adm-btn--primary" id="adm-refresh-users">${t("admin_panel.btn_refresh")}</button>
    </div>
    <div class="adm-card adm-card--form">
      <h3 class="adm-card-title">${t("admin_panel.create_user")}</h3>
      <form id="adm-create-user-form" class="adm-form-row">
        <input type="text" id="adm-new-username" placeholder="${t("admin_panel.placeholder_username")}" required class="adm-input" />
        <input type="password" id="adm-new-password" placeholder="${t("admin_panel.placeholder_password")}" required class="adm-input" />
        <select id="adm-new-role" class="adm-select">
          <option value="viewer">viewer</option>
          <option value="editor">editor</option>
          <option value="admin">admin</option>
        </select>
        <label class="adm-check-label">
          <input type="checkbox" id="adm-new-must-change" />
          <span>${t("admin_panel.badge_must_change_pw")}</span>
        </label>
        <button type="submit" class="adm-btn adm-btn--primary">${t("admin_panel.btn_create")}</button>
      </form>
    </div>
    ${userInvitationsCard}
    <div class="adm-users-mobile" id="adm-users-cards">${cards}</div>
    <div class="adm-users-desktop">
      <div class="adm-table-wrap">
        <table class="adm-table">
          <thead>
            <tr>
              <th>${t("common.user")}</th>
              <th style="width:110px">${t("common.role")}</th>
              <th style="width:120px">${t("admin_panel.th_tier")}</th>
              <th style="width:90px">${t("common.status")}</th>
              <th style="width:100px">${t("admin_panel.th_must_change")}</th>
              <th style="width:100px">${t("admin_panel.th_mfa")}</th>
              <th style="width:90px">ShadeMap</th>
              <th style="width:150px">${t("admin_panel.label_last_login")}</th>
              <th style="width:150px">${t("admin_panel.label_created")}</th>
              <th style="width:340px">${t("common.actions")}</th>
            </tr>
          </thead>
          <tbody id="adm-users-body">${rows}</tbody>
        </table>
      </div>
    </div>
  `;
}

// ── Section: Sessions ──────────────────────────────────────

function renderSessionsSection(): string {
  const sorted = [...state.sessions].sort((a, b) => b.last_seen_at_ms - a.last_seen_at_ms);
  const rows = sorted.map(s => `
    <tr class="adm-row">
      <td class="adm-cell-user">
        <span class="adm-username">${esc(s.username)}</span>
        <span class="adm-meta">uid ${s.user_id}</span>
      </td>
      <td>${badge(s.role, s.role === "admin" ? "amber" : "muted")}</td>
      <td>
        ${s.mfa_setup_required ? badge(t("admin_panel.badge_setup_required"), "red") : (s.mfa_authenticated_at_ms > 0 ? badge(t("admin_panel.badge_mfa"), "green") : badge(t("admin_panel.badge_no_mfa"), "muted"))}
      </td>
      <td class="adm-cell-date">${fmtDate(s.created_at_ms)}</td>
      <td class="adm-cell-date">${fmtDate(s.last_seen_at_ms)}</td>
      <td class="adm-cell-date">${fmtDate(s.expires_at_ms)}</td>
      <td class="adm-cell-mono">${esc(s.token_hash.slice(0, 16))}\u2026</td>
    </tr>
  `).join("");

  const cards = sorted.map(s => `
    <div class="adm-user-card">
      <div class="adm-user-card-header">
        <div class="adm-user-card-name">
          <span class="adm-username">${esc(s.username)}</span>
          <span class="adm-meta">uid ${s.user_id}</span>
        </div>
        <div class="adm-user-card-badges">
          ${badge(s.role, s.role === "admin" ? "amber" : "muted")}
          ${s.mfa_setup_required ? badge(t("admin_panel.badge_setup_required"), "red") : (s.mfa_authenticated_at_ms > 0 ? badge(t("admin_panel.badge_mfa"), "green") : badge(t("admin_panel.badge_no_mfa"), "muted"))}
        </div>
      </div>
      <div class="adm-user-card-meta">
        <div class="adm-user-card-meta-item">
          <span class="adm-user-card-meta-label">${t("admin_panel.label_created")}</span>
          <span class="adm-user-card-meta-value">${fmtDate(s.created_at_ms)}</span>
        </div>
        <div class="adm-user-card-meta-item">
          <span class="adm-user-card-meta-label">${t("admin_panel.label_last_seen")}</span>
          <span class="adm-user-card-meta-value">${fmtDate(s.last_seen_at_ms)}</span>
        </div>
        <div class="adm-user-card-meta-item">
          <span class="adm-user-card-meta-label">${t("admin_panel.label_expires")}</span>
          <span class="adm-user-card-meta-value">${fmtDate(s.expires_at_ms)}</span>
        </div>
      </div>
    </div>
  `).join("");

  const emptyMsg = sorted.length === 0 ? `<p class="adm-empty">${t("admin_panel.no_active_sessions")}</p>` : "";

  return `
    <div class="adm-section-header">
      <div>
        <h2 class="adm-section-title">${t("admin_panel.section_sessions")}</h2>
        <p class="adm-section-desc">${t("admin_panel.active_sessions", { count: state.sessions.length })}</p>
      </div>
      <div class="adm-btn-group">
        <button class="adm-btn" id="adm-refresh-sessions">${t("admin_panel.btn_refresh")}</button>
        <button class="adm-btn adm-btn--danger" id="adm-revoke-all">${t("admin_panel.btn_revoke_all_others")}</button>
      </div>
    </div>
    <div class="adm-users-mobile">${cards || emptyMsg}</div>
    <div class="adm-users-desktop">
      <div class="adm-table-wrap">
        <table class="adm-table">
          <thead>
            <tr>
              <th>${t("common.user")}</th>
              <th style="width:90px">${t("common.role")}</th>
              <th style="width:120px">${t("admin_panel.th_mfa")}</th>
              <th style="width:160px">${t("admin_panel.label_created")}</th>
              <th style="width:160px">${t("admin_panel.label_last_seen")}</th>
              <th style="width:160px">${t("admin_panel.label_expires")}</th>
              <th style="width:180px">${t("admin_panel.th_token_hash")}</th>
            </tr>
          </thead>
          <tbody>${rows || `<tr><td colspan="7" class="adm-empty">${t("admin_panel.no_active_sessions")}</td></tr>`}</tbody>
        </table>
      </div>
    </div>
  `;
}

// ── Section: Audit ─────────────────────────────────────────

function renderAuditSection(): string {
  const page = state.audit;
  const events = page?.events ?? [];
  const total = page?.total ?? 0;
  const offset = state.auditOffset;
  const showing = events.length > 0
    ? `${offset + 1}\u2013${offset + events.length} ${t("admin_panel.of")} ${total}`
    : t("admin_panel.no_events");

  const rows = events.map(e => {
    const statusClass = e.status_code >= 400 ? "adm-status-err" : "";
    return `
      <tr class="adm-row">
        <td class="adm-cell-date">${fmtDate(e.occurred_at_ms)}</td>
        <td>${esc(e.actor_username)}</td>
        <td>${badge(e.actor_role, e.actor_role === "admin" ? "amber" : "muted")}</td>
        <td>${esc(formatGardenScope(e.garden_id))}</td>
        <td><code class="adm-method">${esc(e.method)}</code></td>
        <td class="adm-cell-mono">${esc(e.path)}</td>
        <td class="${statusClass}">${e.status_code}</td>
        <td>${esc(e.remote_host || "\u2014")}</td>
        <td class="adm-cell-detail" title="${esc(e.detail || "")}">${esc(e.detail || "\u2014")}</td>
      </tr>
    `;
  }).join("");

  const cards = events.map(e => {
    const statusClass = e.status_code >= 400 ? "adm-status-err" : "";
    return `
    <div class="adm-user-card">
      <div class="adm-user-card-header">
        <div class="adm-user-card-name">
          <span class="adm-username">${esc(e.actor_username)}</span>
          <span class="adm-meta">${fmtDate(e.occurred_at_ms)}</span>
        </div>
        <div class="adm-user-card-badges">
          ${badge(e.actor_role, e.actor_role === "admin" ? "amber" : "muted")}
          <span class="adm-badge ${statusClass ? "adm-badge--red" : "adm-badge--muted"}">${e.status_code}</span>
        </div>
      </div>
      <div class="adm-user-card-meta">
        <div class="adm-user-card-meta-item">
          <span class="adm-user-card-meta-label">${t("admin_panel.label_garden")}</span>
          <span class="adm-user-card-meta-value">${esc(formatGardenScope(e.garden_id))}</span>
        </div>
        <div class="adm-user-card-meta-item">
          <span class="adm-user-card-meta-label">${t("admin_panel.th_method")}</span>
          <span class="adm-user-card-meta-value"><code class="adm-method">${esc(e.method)}</code></span>
        </div>
        <div class="adm-user-card-meta-item">
          <span class="adm-user-card-meta-label">${t("admin_panel.th_path")}</span>
          <span class="adm-user-card-meta-value" style="word-break:break-all">${esc(e.path)}</span>
        </div>
        ${e.detail ? `<div class="adm-user-card-meta-item adm-user-card-meta-item--full">
          <span class="adm-user-card-meta-label">${t("admin_panel.th_detail")}</span>
          <span class="adm-user-card-meta-value">${esc(e.detail)}</span>
        </div>` : ""}
      </div>
    </div>`;
  }).join("");

  return `
    <div class="adm-section-header">
      <div>
        <h2 class="adm-section-title">${t("admin_panel.section_audit_log")}</h2>
        <p class="adm-section-desc">${showing}</p>
      </div>
    </div>
    <div class="adm-card adm-card--form">
      <form id="adm-audit-filter" class="adm-form-row">
        <input type="number" id="adm-audit-garden" placeholder="${t("admin_panel.placeholder_garden_id")}" min="1" class="adm-input adm-input--xs" />
        <input type="text" id="adm-audit-actor" placeholder="${t("admin_panel.placeholder_actor")}" class="adm-input adm-input--sm" />
        <input type="text" id="adm-audit-path" placeholder="${t("admin_panel.placeholder_path_prefix")}" class="adm-input adm-input--sm" />
        <select id="adm-audit-method" class="adm-select adm-select--sm">
          <option value="">${t("admin_panel.option_any_method")}</option>
          <option value="POST">POST</option>
          <option value="PATCH">PATCH</option>
          <option value="DELETE">DELETE</option>
        </select>
        <input type="number" id="adm-audit-status" placeholder="${t("common.status")}" min="100" max="599" class="adm-input adm-input--xs" />
        <button type="submit" class="adm-btn">${t("admin_panel.btn_filter")}</button>
      </form>
    </div>
    <div class="adm-users-mobile">${cards || `<p class="adm-empty">${t("admin_panel.no_audit_events")}</p>`}</div>
    <div class="adm-users-desktop">
      <div class="adm-table-wrap">
        <table class="adm-table adm-table--audit">
          <thead>
            <tr>
              <th style="width:160px">${t("admin_panel.th_when")}</th>
              <th style="width:110px">${t("admin_panel.placeholder_actor")}</th>
              <th style="width:80px">${t("common.role")}</th>
              <th style="width:100px">${t("admin_panel.label_garden")}</th>
              <th style="width:70px">${t("admin_panel.th_method")}</th>
              <th>${t("admin_panel.th_path")}</th>
              <th style="width:60px">${t("common.status")}</th>
              <th style="width:110px">${t("admin_panel.th_host")}</th>
              <th>${t("admin_panel.th_detail")}</th>
            </tr>
          </thead>
          <tbody>${rows || `<tr><td colspan="9" class="adm-empty">${t("admin_panel.no_audit_events")}</td></tr>`}</tbody>
        </table>
      </div>
    </div>
    <div class="adm-pagination">
      <button class="adm-btn adm-btn--sm" id="adm-audit-prev" ${offset === 0 ? "disabled" : ""}>${t("admin_panel.btn_prev")}</button>
      <span class="adm-page-info">${showing}</span>
      <button class="adm-btn adm-btn--sm" id="adm-audit-next" ${offset + events.length >= total ? "disabled" : ""}>${t("admin_panel.btn_next")}</button>
    </div>
  `;
}

// ── Section: Invitations ───────────────────────────────────

function renderInvitationsSection(): string {
  const ctx = gardenContextFn?.() ?? { gardens: [], activeGardenId: null };
  const gardenName = ctx.gardens.find(g => g.id === ctx.activeGardenId)?.name ?? `Garden ${ctx.activeGardenId ?? "?"}`;

  const rows = state.invitations.map(inv => {
    const statusVariant = inv.status === "pending" ? "amber"
      : inv.status === "accepted" ? "green"
      : "red";
    return `
      <tr class="adm-row" data-inv-id="${inv.id}">
        <td class="adm-cell-user"><span class="adm-username">${esc(inv.invitee_username)}</span></td>
        <td>${badge(inv.role, inv.role === "admin" ? "amber" : "muted")}</td>
        <td>${badge(inv.status, statusVariant)}</td>
        <td class="adm-cell-date">${fmtDate(inv.created_at_ms)}</td>
        <td class="adm-cell-date">${fmtDate(inv.expires_at_ms)}</td>
        <td class="adm-cell-actions">
          ${inv.status === "pending" ? `<button class="adm-btn adm-btn--sm adm-btn--danger adm-act-revoke-inv">${t("admin_panel.btn_revoke")}</button>` : "\u2014"}
        </td>
      </tr>
    `;
  }).join("");

  const cards = state.invitations.map(inv => {
    const statusVariant = inv.status === "pending" ? "amber"
      : inv.status === "accepted" ? "green"
      : "red";
    return `
    <div class="adm-user-card" data-inv-id="${inv.id}">
      <div class="adm-user-card-header">
        <div class="adm-user-card-name">
          <span class="adm-username">${esc(inv.invitee_username)}</span>
          <div class="adm-user-card-badges">
            ${badge(inv.role, inv.role === "admin" ? "amber" : "muted")}
            ${badge(inv.status, statusVariant)}
          </div>
        </div>
        ${inv.status === "pending" ? `<button class="adm-btn adm-btn--sm adm-btn--danger adm-act-revoke-inv">${t("admin_panel.btn_revoke")}</button>` : ""}
      </div>
      <div class="adm-user-card-meta">
        <div class="adm-user-card-meta-item">
          <span class="adm-user-card-meta-label">${t("admin_panel.label_created")}</span>
          <span class="adm-user-card-meta-value">${fmtDate(inv.created_at_ms)}</span>
        </div>
        <div class="adm-user-card-meta-item">
          <span class="adm-user-card-meta-label">${t("admin_panel.label_expires")}</span>
          <span class="adm-user-card-meta-value">${fmtDate(inv.expires_at_ms)}</span>
        </div>
      </div>
    </div>`;
  }).join("");

  return `
    <div class="adm-section-header">
      <div>
        <h2 class="adm-section-title">${t("admin_panel.section_invitations")}</h2>
        <p class="adm-section-desc">${esc(gardenName)} \u00b7 ${t("admin_panel.invitation_count", { count: state.invitations.length })}</p>
      </div>
      <button class="adm-btn" id="adm-refresh-inv">${t("admin_panel.btn_refresh")}</button>
    </div>
    <div class="adm-card adm-card--form">
      <h3 class="adm-card-title">${t("admin_panel.invite_viewer_title")}</h3>
      <p class="adm-section-desc">${t("admin_panel.invite_viewer_desc")}</p>
      <form id="adm-create-inv-form" class="adm-form-row">
        <input type="text" id="adm-inv-username" placeholder="${t("admin_panel.placeholder_username")}" required class="adm-input" />
        <select id="adm-inv-role" class="adm-select" hidden>
          <option value="viewer">viewer</option>
        </select>
        <input type="number" id="adm-inv-ttl" placeholder="${t("admin_panel.placeholder_ttl")}" min="5" class="adm-input adm-input--sm" />
        <button type="submit" class="adm-btn adm-btn--primary">${t("admin_panel.btn_create")}</button>
      </form>
      <div id="adm-inv-link-box" class="adm-inv-link-box"${state.lastInviteLink ? "" : " hidden"}>
        <label class="adm-inv-link-label">${t("admin_panel.invitation_link")}
          <div class="adm-inv-link-row">
            <input type="text" id="adm-inv-link-input" readonly class="adm-input adm-inv-link-input" value="${esc(state.lastInviteLink)}" />
            <button type="button" id="adm-inv-link-copy" class="adm-btn adm-btn--sm">${t("admin_panel.btn_copy")}</button>
          </div>
        </label>
        <p class="adm-inv-link-hint">${t("admin_panel.invite_link_hint")}</p>
      </div>
    </div>
    <div class="adm-users-mobile" id="adm-inv-cards">${cards || `<p class="adm-empty">${t("admin_panel.no_invitations")}</p>`}</div>
    <div class="adm-users-desktop">
      <div class="adm-table-wrap">
        <table class="adm-table">
          <thead>
            <tr>
              <th>${t("admin_panel.th_invitee")}</th>
              <th style="width:90px">${t("common.role")}</th>
              <th style="width:100px">${t("common.status")}</th>
              <th style="width:160px">${t("admin_panel.label_created")}</th>
              <th style="width:160px">${t("admin_panel.label_expires")}</th>
              <th style="width:100px">${t("admin_panel.th_action")}</th>
            </tr>
          </thead>
          <tbody>${rows || `<tr><td colspan="6" class="adm-empty">${t("admin_panel.no_invitations")}</td></tr>`}</tbody>
        </table>
      </div>
    </div>
  `;
}

// ── Section: System ────────────────────────────────────────

function renderSystemSection(): string {
  const me = state.me;
  const emergency = state.emergencyReadOnly;
  const systemHealth = state.systemHealth;
  const metrics = state.securityMetrics;
  const alerts = state.securityAlerts?.alerts ?? [];
  const rates = metrics?.rates ?? {};
  const exporter = metrics?.exporter;
  const providerRows = (metrics?.provider_limits.features ?? []).map(feature => `
    <tr>
      <td>${esc(feature.label)}</td>
      <td>${feature.active_concurrency}/${feature.concurrency_limit}</td>
      <td>${feature.user_total_requests}</td>
      <td>${feature.garden_total_requests}</td>
      <td>${renderProviderScopeUsage(t("common.user"), feature.top_user_scope)}</td>
      <td>${renderProviderScopeUsage(t("admin_panel.scope_garden"), feature.top_garden_scope)}</td>
    </tr>
  `).join("");
  return `
    <div class="adm-section-header">
      <div>
        <h2 class="adm-section-title">${t("admin_panel.section_system")}</h2>
        <p class="adm-section-desc">${t("admin_panel.system_desc")}</p>
      </div>
      <button class="adm-btn" id="adm-refresh-system">${t("admin_panel.btn_refresh")}</button>
    </div>
    <div class="adm-system-grid">
      <div class="adm-card">
        <h3 class="adm-card-title">${t("admin_panel.system_health")}</h3>
        <p class="adm-card-desc">${t("admin_panel.system_health_desc")}</p>
        ${systemHealth
          ? `<dl class="adm-dl">
              <dt>${t("common.status")}</dt><dd>${renderSystemHealthStatusBadge(systemHealth.status)}</dd>
              <dt>${t("admin_panel.dt_db_quick_check")}</dt><dd><code>${esc(systemHealth.db_quick_check)}</code></dd>
              <dt>${t("admin_panel.dt_fk_violations")}</dt><dd>${String(systemHealth.fk_violations)}</dd>
              <dt>${t("admin_panel.dt_table_count")}</dt><dd>${String(systemHealth.table_count)}</dd>
              <dt>${t("admin_panel.dt_last_backup")}</dt><dd>${esc(fmtMaybeDate(systemHealth.last_backup))}</dd>
              <dt>${t("admin_panel.dt_uptime")}</dt><dd>${esc(fmtDurationSeconds(systemHealth.uptime_seconds))}</dd>
              ${systemHealth.taillight
                ? `<dt>${t("admin_panel.dt_taillight_dropped")}</dt><dd>${String(systemHealth.taillight.dropped)}</dd>
                   <dt>${t("admin_panel.dt_taillight_send_failed")}</dt><dd>${String(systemHealth.taillight.send_failed)}</dd>`
                : ""}
            </dl>`
          : `<p class="adm-empty">${t("admin_panel.system_health_unavailable")}</p>`}
      </div>
      <div class="adm-card">
        <h3 class="adm-card-title">${t("admin_panel.current_session")}</h3>
        <dl class="adm-dl">
          <dt>${t("admin_panel.placeholder_username")}</dt><dd>${esc(me?.username ?? "\u2014")}</dd>
          <dt>${t("common.role")}</dt><dd>${badge(me?.role ?? "\u2014", me?.role === "admin" ? "amber" : "muted")}</dd>
          <dt>${t("admin_panel.dt_garden_role")}</dt><dd>${badge(me?.garden_role ?? "\u2014", me?.garden_role === "admin" ? "amber" : "muted")}</dd>
          <dt>${t("admin_panel.dt_auth_type")}</dt><dd>${esc(me?.auth_type ?? "\u2014")}</dd>
          <dt>${t("admin_panel.th_mfa")}</dt><dd>${me?.mfa_setup_required ? badge(t("admin_panel.badge_setup_required"), "red") : (me?.mfa_enabled ? badge(me?.mfa_authenticated ? t("admin_panel.badge_enabled") : t("admin_panel.badge_enrolled"), "green") : badge(t("admin_panel.badge_not_enabled"), "muted"))}</dd>
          <dt>${t("admin_panel.dt_write_access")}</dt><dd>${me?.write_access ? badge(t("admin_panel.badge_yes"), "green") : badge(t("admin_panel.badge_no"), "red")}</dd>
        </dl>
        <div class="adm-card-actions">
          <button class="adm-btn adm-btn--danger" id="adm-sign-out">${t("admin_panel.btn_sign_out")}</button>
        </div>
      </div>
      <div class="adm-card">
        <h3 class="adm-card-title">${t("admin_panel.emergency_controls")}</h3>
        <p class="adm-card-desc">${t("admin_panel.emergency_controls_desc")}</p>
        ${emergency.enabled && emergency.expires_at_ms
          ? `<p class="adm-section-desc">${t("admin_panel.auto_expires", { date: fmtDate(emergency.expires_at_ms) })}</p>`
          : ""}
        <div class="adm-toggle-row">
          <span class="adm-toggle-label">${t("admin_panel.read_only_mode")}</span>
          <button class="adm-toggle ${emergency.enabled ? "adm-toggle--on" : ""}" id="adm-ero-toggle" aria-pressed="${emergency.enabled}">
            <span class="adm-toggle-track">
              <span class="adm-toggle-thumb"></span>
            </span>
            <span class="adm-toggle-text">${emergency.enabled ? t("admin_panel.badge_enabled") : t("admin_panel.badge_disabled")}</span>
          </button>
        </div>
      </div>
      <div class="adm-card">
        <h3 class="adm-card-title">${t("admin_panel.security_telemetry")}</h3>
        <dl class="adm-dl">
          <dt>${t("admin_panel.rate_auth_failures")}</dt><dd>${fmtRate(rates["auth_failures_per_minute"])}</dd>
          <dt>${t("admin_panel.rate_admin_login_failures")}</dt><dd>${fmtRate(rates["auth_login_failures_admin_per_minute"])}</dd>
          <dt>${t("admin_panel.rate_rate_limit_hits")}</dt><dd>${fmtRate(rates["rate_limit_hits_per_minute"])}</dd>
          <dt>${t("admin_panel.rate_invalid_resets")}</dt><dd>${fmtRate(rates["invalid_reset_password_attempts_per_5m"])}</dd>
          <dt>${t("admin_panel.rate_invalid_invites")}</dt><dd>${fmtRate(rates["invalid_invitation_attempts_per_5m"])}</dd>
          <dt>${t("admin_panel.rate_budget_hits")}</dt><dd>${fmtRate(rates["provider_budget_hits_per_5m"])}</dd>
          <dt>${t("admin_panel.rate_concurrency_hits")}</dt><dd>${fmtRate(rates["concurrency_limit_hits_per_5m"])}</dd>
          <dt>${t("admin_panel.rate_feature_misses")}</dt><dd>${fmtRate(rates["shademap_features_cache_misses_per_5m"])}</dd>
          <dt>${t("admin_panel.rate_feature_miss_ratio")}</dt><dd>${fmtRate(rates["shademap_features_cache_miss_ratio_pct_5m"])}%</dd>
          <dt>${t("admin_panel.rate_terrain_misses")}</dt><dd>${fmtRate(rates["shademap_terrain_remote_misses_per_5m"])}</dd>
          <dt>${t("admin_panel.rate_terrain_miss_ratio")}</dt><dd>${fmtRate(rates["shademap_terrain_remote_miss_ratio_pct_5m"])}%</dd>
          <dt>${t("admin_panel.rate_ai_provider_failures")}</dt><dd>${fmtRate(rates["ai_provider_failures_per_5m"])}</dd>
          <dt>${t("admin_panel.rate_shademap_failures")}</dt><dd>${fmtRate(rates["shademap_upstream_failures_per_5m"])}</dd>
        </dl>
      </div>
      <div class="adm-card">
        <h3 class="adm-card-title">${t("admin_panel.active_alerts")}</h3>
        <p class="adm-card-desc">${t("admin_panel.active_alerts_desc")}</p>
        ${alerts.length
          ? `<ul class="adm-list">${alerts.map(renderSecurityAlert).join("")}</ul>`
          : `<p class="adm-empty">${t("admin_panel.no_active_alerts")}</p>`}
      </div>
      <div class="adm-card">
        <h3 class="adm-card-title">${t("admin_panel.provider_budgets")}</h3>
        <p class="adm-card-desc">
          ${metrics?.provider_limits.day
            ? t("admin_panel.budget_day", { day: metrics.provider_limits.day })
            : t("admin_panel.no_provider_usage_yet")}
        </p>
        <div class="adm-table-wrap">
          <table class="adm-table">
            <thead>
              <tr>
                <th>${t("admin_panel.th_feature")}</th>
                <th style="width:110px">${t("admin_panel.th_active")}</th>
                <th style="width:90px">${t("admin_panel.th_user_req")}</th>
                <th style="width:100px">${t("admin_panel.th_garden_req")}</th>
                <th>${t("admin_panel.th_top_user")}</th>
                <th>${t("admin_panel.th_top_garden")}</th>
              </tr>
            </thead>
            <tbody>${providerRows || `<tr><td colspan="6" class="adm-empty">${t("admin_panel.no_provider_usage")}</td></tr>`}</tbody>
          </table>
        </div>
      </div>
      <div class="adm-card">
        <h3 class="adm-card-title">${t("admin_panel.telemetry_export")}</h3>
        <p class="adm-card-desc">${t("admin_panel.telemetry_export_desc")}</p>
        <dl class="adm-dl">
          <dt>${t("admin_panel.badge_enabled")}</dt><dd>${exporter?.enabled ? badge(t("admin_panel.badge_yes"), "green") : badge(t("admin_panel.badge_no"), "muted")}</dd>
          <dt>${t("admin_panel.dt_destination")}</dt><dd>${esc(exporter?.destination || "\u2014")}</dd>
          <dt>${t("admin_panel.dt_pending")}</dt><dd>${String(exporter?.pending_count ?? 0)}</dd>
          <dt>${t("admin_panel.dt_oldest_pending")}</dt><dd>${fmtDate(exporter?.oldest_pending_at_ms)}</dd>
          <dt>${t("admin_panel.dt_last_attempt")}</dt><dd>${fmtDate(exporter?.last_attempt_at_ms)}</dd>
          <dt>${t("admin_panel.dt_last_success")}</dt><dd>${fmtDate(exporter?.last_success_at_ms)}</dd>
          <dt>${t("admin_panel.dt_poll_interval")}</dt><dd>${exporter?.poll_interval_seconds ? `${exporter.poll_interval_seconds}s` : "\u2014"}</dd>
          <dt>${t("admin_panel.dt_snapshot_interval")}</dt><dd>${exporter?.snapshot_interval_seconds ? `${exporter.snapshot_interval_seconds}s` : "\u2014"}</dd>
          <dt>${t("admin_panel.dt_last_error")}</dt><dd>${esc(exporter?.last_error || "\u2014")}</dd>
        </dl>
      </div>
    </div>
  `;
}

// ── Main render ────────────────────────────────────────────

function sectionBtn(id: AdminSection, label: string, icon: string): string {
  const active = state.section === id;
  return `<button class="adm-nav-btn${active ? " adm-nav-btn--active" : ""}" data-section="${id}">
    <span class="adm-nav-icon">${icon}</span>
    <span>${label}</span>
  </button>`;
}

function renderContent(): string {
  switch (state.section) {
    case "settings": return renderSettingsSection();
    case "garden": return renderGardenSection();
    case "users": return renderUsersSection();
    case "sessions": return renderSessionsSection();
    case "audit": return renderAuditSection();
    case "invitations": return renderInvitationsSection();
    case "system": return renderSystemSection();
  }
}

function renderAdmin(): string {
  const setupRequired = Boolean(state.me?.mfa_setup_required);
  const visibleSections = getVisibleSections();
  const consoleLabel = isPlatformAdmin() ? t("admin.console_title_admin") : t("admin.console_title_user");
  if (!visibleSections.includes(state.section)) {
    state.section = defaultSection();
  }
  return `
    <div class="adm-layout">
      <nav class="adm-sidebar">
        <div class="adm-sidebar-head">
          <span class="adm-sidebar-title">${consoleLabel}</span>
        </div>
        ${setupRequired ? `<p class="adm-section-desc">${t("admin.sidebar_mfa_required")}</p>` : ""}
        <div class="adm-nav">
          ${visibleSections.includes("settings") ? sectionBtn("settings", t("admin.section.settings"), "\u2699") : ""}
          ${visibleSections.includes("garden") ? sectionBtn("garden", t("admin.section.garden"), "\u{1F33F}") : ""}
          ${visibleSections.includes("invitations") ? sectionBtn("invitations", t("admin.section.invitations"), "\u{1F4E8}") : ""}
          ${visibleSections.includes("users") ? sectionBtn("users", t("admin.section.users"), "\u{1F464}") : ""}
          ${visibleSections.includes("sessions") ? sectionBtn("sessions", t("admin.section.sessions"), "\u{1F511}") : ""}
          ${visibleSections.includes("audit") ? sectionBtn("audit", t("admin.section.audit"), "\u{1F4CB}") : ""}
          ${visibleSections.includes("system") ? sectionBtn("system", t("admin.section.system"), "\u{1F6E0}\uFE0F") : ""}
        </div>
      </nav>
      <div class="adm-main" id="adm-main">
        ${renderContent()}
      </div>
    </div>
  `;
}

// ── Data loading ───────────────────────────────────────────

async function loadUsers(): Promise<void> {
  try {
    state.users = await getAuthUsersApi();
    state.userInvitations = isPlatformAdmin()
      ? await getUserInvitationsApi()
      : [];
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
  }
}

async function loadSessions(): Promise<void> {
  try {
    state.sessions = await getAuthSessionsApi();
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
  }
}

async function loadAudit(offset = 0, filters?: {
  garden_id?: number; actor?: string; path_prefix?: string; method?: string; status_code?: number;
}): Promise<void> {
  try {
    state.auditOffset = offset;
    state.audit = await getAuthAuditEventsApi({
      limit: AUDIT_PAGE_SIZE,
      offset,
      ...filters,
    });
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
  }
}

async function loadInvitations(): Promise<void> {
  const ctx = gardenContextFn?.();
  if (!ctx?.activeGardenId) return;
  try {
    state.invitations = await getGardenInvitationsApi(ctx.activeGardenId);
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
  }
}

async function loadSettings(): Promise<void> {
  try {
    state.me = await getAuthMeApi();
  } catch { /* non-fatal */ }
  if (state.me?.mfa_setup_required) {
    state.section = "settings";
  }
  try {
    state.meSettings = await getAuthMeSettingsApi();
  } catch { /* non-fatal */ }
  if (!state.meSettings?.mfa.pending_enrollment) {
    state.mfaEnrollment = null;
  }
}

async function loadGardenSettings(): Promise<void> {
  const ctx = gardenContextFn?.();
  if (!ctx?.activeGardenId || !canEditActiveGarden()) {
    state.gardenSettings = null;
    state.gardenMemberships = [];
    state.missingPlantCovers = [];
    state.missingPlantCoversTotal = 0;
    return;
  }
  try {
    state.gardenSettings = await getGardenSettingsApi(ctx.activeGardenId);
  } catch (err) {
    state.gardenSettings = null;
    showToast(getApiErrorMessage(err), "error");
  }
  if (isPlatformAdmin()) {
    try {
      state.gardenMemberships = await getGardenMembershipsApi(ctx.activeGardenId);
    } catch (err) {
      state.gardenMemberships = [];
      showToast(getApiErrorMessage(err), "error");
    }
    try {
      const report = await getMissingPlantCoversApi({ limit: 25, offset: 0 });
      state.missingPlantCovers = report.items;
      state.missingPlantCoversTotal = report.total;
    } catch (err) {
      state.missingPlantCovers = [];
      state.missingPlantCoversTotal = 0;
      showToast(getApiErrorMessage(err), "error");
    }
  } else {
    state.gardenMemberships = [];
    state.missingPlantCovers = [];
    state.missingPlantCoversTotal = 0;
  }
}

async function loadSystem(): Promise<void> {
  try {
    state.me = await getAuthMeApi();
  } catch { /* non-fatal */ }
  try {
    state.systemHealth = await getAdminSystemHealthApi();
  } catch {
    state.systemHealth = null;
  }
  try {
    state.emergencyReadOnly = await getEmergencyReadOnlyApi();
  } catch { /* non-fatal */ }
  try {
    state.securityMetrics = await getSecurityMetricsApi();
  } catch { /* non-fatal */ }
  try {
    state.securityAlerts = await getSecurityAlertsApi();
  } catch { /* non-fatal */ }
}

// ── Wiring ─────────────────────────────────────────────────

function getContainer(): HTMLElement | null {
  return document.getElementById("admin-view");
}

function repaint(): void {
  const main = document.getElementById("adm-main");
  if (main) {
    setReviewedDynamicHtml(main, renderContent());
    wireSection();
  }
}

function repaintFull(): void {
  const container = getContainer();
  if (!container) return;
  setReviewedDynamicHtml(container, renderAdmin());
  wireSidebar();
  wireSection();
}

export function resetAdminPanelSensitiveState(): void {
  state.users = [];
  state.sessions = [];
  state.audit = null;
  state.auditOffset = 0;
  state.invitations = [];
  state.userInvitations = [];
  state.gardenMemberships = [];
  state.lastInviteLink = "";
  state.me = null;
  state.meSettings = null;
  state.gardenSettings = null;
  state.mfaEnrollment = null;
  state.latestRecoveryCodes = [];
  state.systemHealth = null;
  state.securityMetrics = null;
  state.securityAlerts = null;
  state.missingPlantCovers = [];
  state.missingPlantCoversTotal = 0;
  adminPanelInitialized = false;
  const container = getContainer();
  if (container) {
    setReviewedDynamicHtml(container, "");
  }
}

function wireSidebar(): void {
  const container = getContainer();
  if (!container) return;
  container.querySelectorAll<HTMLButtonElement>(".adm-nav-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const section = btn.dataset["section"] as AdminSection | undefined;
      if (!section || section === state.section) return;
      state.section = section;
      // Update nav active states immediately
      container.querySelectorAll<HTMLButtonElement>(".adm-nav-btn").forEach(b =>
        b.classList.toggle("adm-nav-btn--active", b.dataset["section"] === section)
      );
      void loadAndRepaintSection();
    });
  });
}

async function loadAndRepaintSection(): Promise<void> {
  switch (state.section) {
    case "settings": await loadSettings(); break;
    case "garden": await loadGardenSettings(); break;
    case "users": await loadUsers(); break;
    case "sessions": await loadSessions(); break;
    case "audit": await loadAudit(); break;
    case "invitations": await loadInvitations(); break;
    case "system": await loadSystem(); break;
  }
  repaint();
}

function readAuditFilters(): {
  garden_id?: number;
  actor?: string;
  path_prefix?: string;
  method?: string;
  status_code?: number;
} {
  const gardenRaw = queryInput("adm-audit-garden")?.value.trim();
  const actor = queryInput("adm-audit-actor")?.value.trim();
  const path_prefix = queryInput("adm-audit-path")?.value.trim();
  const method = querySelect("adm-audit-method")?.value;
  const statusRaw = queryInput("adm-audit-status")?.value.trim();
  const filters: {
    garden_id?: number;
    actor?: string;
    path_prefix?: string;
    method?: string;
    status_code?: number;
  } = {};
  if (gardenRaw) filters.garden_id = Number(gardenRaw);
  if (actor) filters.actor = actor;
  if (path_prefix) filters.path_prefix = path_prefix;
  if (method) filters.method = method;
  if (statusRaw) filters.status_code = Number(statusRaw);
  return filters;
}

function wireSection(): void {
  const container = getContainer();
  if (!container) return;

  container.querySelector("#adm-garden-save")?.addEventListener("click", async () => {
    const ctx = gardenContextFn?.();
    if (!ctx?.activeGardenId) {
      showToast(t("admin.toast.no_active_garden"), "error");
      return;
    }
    const name = queryInput("adm-garden-name")?.value.trim() ?? "";
    const gridColsRaw = queryInput("adm-garden-grid-cols")?.value.trim() ?? "";
    const gridRowsRaw = queryInput("adm-garden-grid-rows")?.value.trim() ?? "";
    const address = queryInput("adm-garden-address")?.value ?? "";
    const latRaw = queryInput("adm-garden-latitude")?.value.trim() ?? "";
    const lonRaw = queryInput("adm-garden-longitude")?.value.trim() ?? "";
    const gridCols = Number.parseInt(gridColsRaw, 10);
    const gridRows = Number.parseInt(gridRowsRaw, 10);
    const latitude = latRaw ? Number(latRaw) : null;
    const longitude = lonRaw ? Number(lonRaw) : null;
    if (!name) {
      showToast(t("admin.toast.garden_name_required"), "error");
      return;
    }
    if (!Number.isFinite(gridCols) || !Number.isFinite(gridRows) || gridCols < 5 || gridCols > 100 || gridRows < 5 || gridRows > 100) {
      showToast(t("admin.toast.grid_invalid"), "error");
      return;
    }
    if (latRaw && !Number.isFinite(latitude)) {
      showToast(t("admin.toast.latitude_invalid"), "error");
      return;
    }
    if (lonRaw && !Number.isFinite(longitude)) {
      showToast(t("admin.toast.longitude_invalid"), "error");
      return;
    }
    try {
      state.gardenSettings = await updateGardenSettingsApi(ctx.activeGardenId, {
        name,
        grid_cols: gridCols,
        grid_rows: gridRows,
        address,
        latitude,
        longitude,
      });
      await onGardenStateChanged?.();
      await loadGardenSettings();
      showToast(t("admin.toast.garden_saved"), "success");
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });

  container.querySelector("#adm-garden-onboarding")?.addEventListener("click", async () => {
    const ctx = gardenContextFn?.();
    if (!ctx?.activeGardenId || !state.gardenSettings) {
      showToast(t("admin.toast.no_active_garden"), "error");
      return;
    }
    if (state.gardenSettings.onboarding_complete && !(await confirmDialog(t("admin.confirm.reopen_onboarding")))) {
      return;
    }
    try {
      state.gardenSettings = await updateGardenSettingsApi(ctx.activeGardenId, {
        onboarding_complete: false,
      });
      showToast(t("admin.toast.onboarding_reopened"), "success");
      repaint();
      await onRestartOnboarding?.();
      await loadGardenSettings();
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });

  container.querySelector("#adm-garden-delete")?.addEventListener("click", async () => {
    const ctx = gardenContextFn?.();
    if (!ctx?.activeGardenId) {
      showToast(t("admin.toast.no_active_garden"), "error");
      return;
    }
    const activeGarden = ctx.gardens.find((garden) => garden.id === ctx.activeGardenId) ?? null;
    if (!activeGarden) {
      showToast(t("admin.toast.active_garden_missing"), "error");
      return;
    }
    if (activeGarden.slug === "default") {
      showToast(t("admin.toast.default_garden_protected"), "error");
      return;
    }
    if (!(await confirmDialog(t("admin.confirm.delete_garden", { name: activeGarden.name })))) {
      return;
    }
    const actionReason = await authorizeSensitiveAdminAction(
      t("admin_panel.action_delete_garden"),
      `garden-delete:${ctx.activeGardenId}`,
    );
    if (!actionReason) return;
    try {
      const result = await deleteGardenApi(ctx.activeGardenId, actionReason);
      showToast(t("admin.toast.deleted_garden", { name: result.garden_name }), "success");
      state.gardenSettings = null;
      await onGardenStateChanged?.();
      await loadGardenSettings();
      repaint();
    } catch (err) {
      showToast(getApiErrorMessage(err), "error");
    }
  });

  container.querySelectorAll<HTMLButtonElement>(".adm-act-remove-garden-member").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const ctx = gardenContextFn?.();
      if (!ctx?.activeGardenId) {
        showToast(t("admin.toast.no_active_garden"), "error");
        return;
      }
      const row = btn.closest<HTMLElement>("[data-garden-member-id]");
      const userId = Number(row?.dataset["gardenMemberId"]);
      if (!Number.isFinite(userId)) {
        showToast(t("admin.toast.unknown_garden_member"), "error");
        return;
      }
      const membership = state.gardenMemberships.find((item) => item.user_id === userId);
      const username = membership?.username ?? `user ${userId}`;
      if (!(await confirmDialog(t("admin.confirm.remove_garden_member", { username })))) {
        return;
      }
      const actionReason = await authorizeSensitiveAdminAction(
        t("admin_panel.action_remove_garden_member"),
        `garden-membership-remove:${ctx.activeGardenId}:${userId}`,
      );
      if (!actionReason) return;
      try {
        await deleteGardenMembershipApi(ctx.activeGardenId, userId, actionReason);
        showToast(t("admin.toast.removed_garden_member", { username }), "success");
        await loadGardenSettings();
        repaint();
      } catch (err) {
        showToast(getApiErrorMessage(err), "error");
      }
    });
  });

  container.querySelector("#adm-garden-cover-import")?.addEventListener("click", async () => {
    if (state.plantCoverImport.running) return;
    state.plantCoverImport = {
      running: true,
      total: 0,
      processed: 0,
      remaining: 0,
      adoptedExisting: 0,
      importedRemote: 0,
      skipped: 0,
      lastItems: [],
    };
    repaint();
    try {
      let cursor: string | null = null;
      let batchResult = await populateMissingPlantCoversApi({ maxPlants: 25 });
      state.plantCoverImport.total = batchResult.total_without_cover_before;
      while (true) {
        state.plantCoverImport.processed += batchResult.processed;
        state.plantCoverImport.remaining = batchResult.remaining_without_cover;
        state.plantCoverImport.adoptedExisting += batchResult.adopted_existing;
        state.plantCoverImport.importedRemote += batchResult.imported_remote;
        state.plantCoverImport.skipped += batchResult.skipped;
        state.plantCoverImport.lastItems = batchResult.items.slice(-6);
        repaint();
        if (!batchResult.has_more || !batchResult.cursor) {
          break;
        }
        cursor = batchResult.cursor;
        batchResult = await populateMissingPlantCoversApi({
          cursor,
          maxPlants: 25,
        });
      }
      state.plantCoverImport.running = false;
      await loadGardenSettings();
      repaint();
      if (state.plantCoverImport.total === 0) {
        showToast(t("admin.toast.cover_import_none"), "success");
      } else {
        showToast(t("admin.toast.cover_import_complete", {
          imported: state.plantCoverImport.importedRemote,
          adopted: state.plantCoverImport.adoptedExisting,
          remaining: state.plantCoverImport.remaining,
        }), "success");
      }
    } catch (err) {
      state.plantCoverImport.running = false;
      repaint();
      showToast(getApiErrorMessage(err), "error");
    }
  });

  container.querySelector("#adm-garden-cover-refresh")?.addEventListener("click", async () => {
    try {
      await loadGardenSettings();
      repaint();
      showToast(t("admin.toast.cover_report_refreshed"), "success");
    } catch (err) {
      showToast(getApiErrorMessage(err), "error");
    }
  });

  // My Settings section
  const shademapKeyEl = container.querySelector("#adm-my-shademap-key");
  const shademapKeyInput = shademapKeyEl instanceof HTMLInputElement ? shademapKeyEl : null;
  if (shademapKeyInput) {
    void getAuthMeSettingsApi().then(s => {
      shademapKeyInput.placeholder = s.has_shademap_key
        ? t("admin.settings.shademap_placeholder_set")
        : t("admin.settings.shademap_placeholder");
      shademapKeyInput.value = "";
    }).catch(() => {});
  }
  container.querySelector("#adm-my-shademap-save")?.addEventListener("click", async () => {
    const key = queryInput("adm-my-shademap-key")?.value.trim() ?? "";
    try {
      await updateAuthMeSettingsApi({ shademap_api_key: key || null });
      showToast(key ? t("admin.toast.shademap_key_saved") : t("admin.toast.shademap_key_cleared"), "success");
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });
  container.querySelector("#adm-my-shademap-clear")?.addEventListener("click", async () => {
    try {
      await updateAuthMeSettingsApi({ shademap_api_key: null });
      const input = queryInput("adm-my-shademap-key");
      if (input) input.value = "";
      showToast(t("admin.toast.shademap_key_cleared"), "success");
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });
  container.querySelector("#adm-plot-meaning-add")?.addEventListener("click", () => {
    if (!state.meSettings) return;
    state.meSettings.plot_assignment_meanings = [
      ...state.meSettings.plot_assignment_meanings,
      { pattern: "", label: "", description: "" },
    ];
    repaint();
  });
  container.querySelectorAll(".adm-plot-meaning-delete").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = (btn as HTMLElement).closest<HTMLElement>(".adm-plot-meaning-row");
      const index = Number(row?.dataset["index"]);
      if (!state.meSettings || !Number.isFinite(index)) return;
      state.meSettings.plot_assignment_meanings = state.meSettings.plot_assignment_meanings
        .filter((_, currentIndex) => currentIndex !== index);
      repaint();
    });
  });
  container.querySelector("#adm-plot-meaning-save")?.addEventListener("click", async () => {
    const rows = Array.from(container.querySelectorAll<HTMLElement>(".adm-plot-meaning-row"));
    const meanings = rows.map((row) => ({
      pattern: (row.querySelector<HTMLInputElement>(".adm-plot-meaning-pattern")?.value ?? "").trim(),
      label: (row.querySelector<HTMLInputElement>(".adm-plot-meaning-label")?.value ?? "").trim(),
      description: (row.querySelector<HTMLInputElement>(".adm-plot-meaning-description")?.value ?? "").trim(),
    })).filter((meaning) => meaning.pattern || meaning.label || meaning.description);
    try {
      await updateAuthMeSettingsApi({ plot_assignment_meanings: meanings });
      await loadSettings();
      onAuthStateChanged?.();
      showToast(t("admin.toast.plot_meanings_saved"), "success");
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });
  container.querySelector("#adm-mfa-start")?.addEventListener("click", async () => {
    try {
      state.mfaEnrollment = await startAuthTotpEnrollmentApi();
      state.latestRecoveryCodes = [];
      await loadSettings();
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });
  container.querySelector("#adm-mfa-confirm")?.addEventListener("click", async () => {
    const code = queryInput("adm-mfa-code")?.value.trim() ?? "";
    if (!code) {
      showToast(t("admin.toast.enter_authenticator_code"), "error");
      return;
    }
    try {
      const result = await confirmAuthTotpEnrollmentApi(code);
      state.latestRecoveryCodes = result.recovery_codes;
      state.mfaEnrollment = null;
      await loadSettings();
      state.me = await getAuthMeApi();
      onAuthStateChanged?.();
      showToast(t("admin.toast.mfa_enabled"), "success");
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });
  container.querySelector("#adm-mfa-disable")?.addEventListener("click", async () => {
    const actionReason = await authorizeSensitiveAdminAction(
      t("admin_panel.action_disable_mfa"),
      "disable-platform-admin-mfa",
    );
    if (!actionReason) return;
    try {
      await disableAuthMfaApi(actionReason);
      state.latestRecoveryCodes = [];
      state.mfaEnrollment = null;
      await loadSettings();
      state.me = await getAuthMeApi();
      onAuthStateChanged?.();
      showToast(t("admin.toast.mfa_disabled"), "success");
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });
  container.querySelector("#adm-mfa-regenerate")?.addEventListener("click", async () => {
    const actionReason = await authorizeSensitiveAdminAction(
      t("admin_panel.action_regenerate_mfa"),
      "regenerate-platform-admin-recovery-codes",
    );
    if (!actionReason) return;
    try {
      const result = await regenerateAuthMfaRecoveryCodesApi(actionReason);
      state.latestRecoveryCodes = result.recovery_codes;
      await loadSettings();
      showToast(t("admin.toast.recovery_regenerated"), "success");
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });
  container.querySelector("#adm-mfa-copy-recovery")?.addEventListener("click", () => {
    const output = queryTextArea("adm-mfa-recovery-output");
    if (!output?.value) return;
    output.select();
    void navigator.clipboard?.writeText(output.value).then(() => {
      showToast(t("admin.toast.recovery_copied"), "success");
    }).catch(() => {});
  });
  container.querySelector("#adm-mfa-clear-recovery")?.addEventListener("click", () => {
    state.latestRecoveryCodes = [];
    repaint();
  });

  // Users section
  container.querySelector("#adm-refresh-users")?.addEventListener("click", () => {
    void loadUsers().then(repaint);
  });
  container.querySelector("#adm-create-user-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = queryInput("adm-new-username")?.value.trim() ?? "";
    const password = queryInput("adm-new-password")?.value ?? "";
    const role = (querySelect("adm-new-role")?.value ?? "viewer") as "viewer" | "editor" | "admin";
    const mustChange = queryInput("adm-new-must-change")?.checked ?? false;
    if (!username || !password) return;
    const actionReason = await authorizeSensitiveAdminAction(
      "Create user",
      `user-create:${username}`,
    );
    if (!actionReason) return;
    try {
      await createAuthUserApi(username, password, role, mustChange, actionReason);
      showToast(t("admin.user_created", { username }), "success");
      const pwdInput = queryInput("adm-new-password");
      if (pwdInput) pwdInput.value = "";
      await loadUsers();
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });
  container.querySelector("#adm-create-user-inv-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = queryInput("adm-user-inv-username")?.value.trim() ?? "";
    const role = (querySelect("adm-user-inv-role")?.value ?? "editor") as "editor" | "admin";
    const ttlRaw = queryInput("adm-user-inv-ttl")?.value.trim() ?? "";
    if (!username) return;
    const ttl = ttlRaw ? Number(ttlRaw) : undefined;
    const actionReason = await authorizeSensitiveAdminAction(
      "Create user invitation",
      `user-invitation-create:${username}`,
    );
    if (!actionReason) return;
    try {
      const result = await createUserInvitationApi(username, role, ttl, actionReason);
      const inviteLink = buildInvitationLink(result.invite_token);
      void navigator.clipboard?.writeText(inviteLink).catch(() => {});
      state.lastInviteLink = inviteLink;
      showToast(t("admin.invite_link_copied", { expires: fmtDate(result.invitation.expires_at_ms) }), "success");
      await loadUsers();
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });
  container.querySelector("#adm-user-inv-link-copy")?.addEventListener("click", () => {
    const input = queryInput("adm-user-inv-link-input");
    if (!input?.value) return;
    input.select();
    void navigator.clipboard?.writeText(input.value).then(() => {
      showToast(t("admin.link_copied"), "success");
    }).catch(() => {});
  });
  async function handleUserAction(e: Event): Promise<void> {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
    if (!btn) return;
    const uidEl = btn.closest<HTMLElement>("[data-uid]");
    if (!uidEl) return;
    const uid = Number(uidEl.dataset["uid"]);
    if (!Number.isFinite(uid)) return;

    if (btn.classList.contains("adm-act-save")) {
      const roleEl = uidEl.querySelector<HTMLSelectElement>(".adm-role-sel");
      if (!roleEl) return;
      const actionReason = await authorizeSensitiveAdminAction(
        "Update user role",
        `user-role-update:${uid}`,
      );
      if (!actionReason) return;
      try {
        await updateAuthUserApi(uid, {
          role: roleEl.value as "viewer" | "editor" | "admin",
          action_reason: actionReason,
        });
        showToast(t("admin.user_updated"), "success");
        await loadUsers();
        repaint();
      } catch (err) { showToast(getApiErrorMessage(err), "error"); }
    }

    if (btn.classList.contains("adm-act-toggle")) {
      const user = state.users.find(u => u.id === uid);
      if (!user) return;
      const nextActive = !user.is_active;
      const patch: { is_active: boolean; deactivated_reason?: string } = { is_active: nextActive };
      if (!nextActive) {
        patch.deactivated_reason = (await promptDialog(t("admin.deactivation_reason_prompt"), "deactivated-by-admin"))?.trim() || "deactivated-by-admin";
      }
      const actionReason = await authorizeSensitiveAdminAction(
        nextActive ? "Activate user" : "Deactivate user",
        `user-active-update:${uid}`,
      );
      if (!actionReason) return;
      try {
        await updateAuthUserApi(uid, { ...patch, action_reason: actionReason });
        showToast(nextActive ? t("admin.user_activated") : t("admin.user_deactivated"), "success");
        await loadUsers();
        repaint();
      } catch (err) { showToast(getApiErrorMessage(err), "error"); }
    }

    if (btn.classList.contains("adm-act-revoke-sessions")) {
      const actionReason = await authorizeSensitiveAdminAction(
        t("admin.action_revoke_user_sessions"),
        `user-session-revoke:${uid}`,
      );
      if (!actionReason) return;
      try {
        const n = await revokeUserSessionsByIdApi(uid, actionReason);
        showToast(t("admin.sessions_revoked", { count: n }), "success");
      } catch (err) { showToast(getApiErrorMessage(err), "error"); }
    }

    if (btn.classList.contains("adm-act-restart-onboarding")) {
      const user = state.users.find(u => u.id === uid);
      const gardenLabel = user?.managed_garden_name ?? "this user's managed garden";
      const actionReason = await authorizeSensitiveAdminAction(
        t("admin.action_force_onboarding", { username: user?.username ?? "this user" }),
        `user-onboarding-restart:${uid}`,
      );
      if (!actionReason) return;
      try {
        const result = await restartUserOnboardingApi(uid, actionReason);
        showToast(
          t("admin.toast.onboarding_reopened_for", {
            username: result.username,
            garden: result.garden_name,
          }),
          "success",
        );
        await loadUsers();
        const ctx = gardenContextFn?.();
        if (ctx?.activeGardenId === result.garden_id) {
          await onRestartOnboarding?.();
        } else {
          await loadGardenSettings();
        }
        repaint();
      } catch (err) {
        showToast(
          `${getApiErrorMessage(err)}${gardenLabel ? ` (${gardenLabel})` : ""}`,
          "error",
        );
      }
    }

    if (btn.classList.contains("adm-act-shademap-key")) {
      const user = state.users.find(u => u.id === uid);
      const action = user?.has_shademap_key ? t("admin_panel.action_replace") : t("admin_panel.action_set");
      const key = (await promptDialog(t("admin.shademap_key_prompt", { action, username: user?.username ?? "user" }), ""))?.trim();
      if (key === undefined) return; // cancelled
      const actionReason = await authorizeSensitiveAdminAction(
        key ? "Set user ShadeMap key" : "Remove user ShadeMap key",
        `user-shademap-key-update:${uid}`,
      );
      if (!actionReason) return;
      try {
        await updateAuthUserApi(uid, {
          shademap_api_key: key || "",
          action_reason: actionReason,
        });
        showToast(key ? t("admin.toast.shademap_key_saved") : t("admin.toast.shademap_key_removed"), "success");
        await loadUsers();
        repaint();
      } catch (err) { showToast(getApiErrorMessage(err), "error"); }
    }

    if (btn.classList.contains("adm-act-reset")) {
      const ttlRaw = (await promptDialog(t("admin.reset_ttl_prompt"), "60"))?.trim();
      if (!ttlRaw) return;
      const ttl = Number(ttlRaw);
      if (!Number.isFinite(ttl) || ttl < 5) { showToast(t("admin.ttl_invalid"), "error"); return; }
      const mustChange = await confirmDialog(t("admin.confirm_must_change"));
      const actionReason = await authorizeSensitiveAdminAction(
        "Issue password reset token",
        `user-reset-token-issue:${uid}`,
      );
      if (!actionReason) return;
      try {
        const result = await issueUserResetTokenApi(uid, {
          expires_in_minutes: ttl,
          must_change_password: mustChange,
          action_reason: actionReason,
        });
        void navigator.clipboard?.writeText(result.reset_token).catch(() => {});
        showToast(t("admin.reset_token_copied", { expires: fmtDate(result.expires_at_ms) }), "success");
      } catch (err) { showToast(getApiErrorMessage(err), "error"); }
    }

    if (btn.classList.contains("adm-act-delete-user")) {
      if (!(await confirmDialog(t("admin.confirm_delete_user")))) return;
      const actionReason = await authorizeSensitiveAdminAction(
        "Delete user",
        `user-delete:${uid}`,
      );
      if (!actionReason) return;
      try {
        const result = await deleteAuthUserApi(uid, actionReason);
        showToast(
          result.operation === "deactivated"
            ? t("admin.user_delete_deactivated")
            : t("admin.user_deleted"),
          "success",
        );
        await loadUsers();
        repaint();
      } catch (err) { showToast(getApiErrorMessage(err), "error"); }
    }
  }
  container.querySelector("#adm-users-body")?.addEventListener("click", (e) => void handleUserAction(e));
  container.querySelector("#adm-users-cards")?.addEventListener("click", (e) => void handleUserAction(e));

  // Tier change — immediate save with downgrade confirmation
  async function handleTierChange(e: Event): Promise<void> {
    if (!(e.target instanceof HTMLSelectElement)) return;
    const select = e.target;
    if (!select.classList.contains("adm-tier-sel")) return;
    const uidEl = select.closest<HTMLElement>("[data-uid]");
    if (!uidEl) return;
    const uid = Number(uidEl.dataset["uid"]);
    if (!Number.isFinite(uid)) return;

    const newTier = select.value;
    const prevTier = select.dataset["prev"] ?? "home";
    if (newTier === prevTier) return;

    const tierIndex: Record<string, number> = { home: 0, enthusiast: 1, pro: 2 };
    const isDowngrade = (tierIndex[newTier] ?? 0) < (tierIndex[prevTier] ?? 0);

    if (isDowngrade) {
      const user = state.users.find(u => u.id === uid);
      const lost = featuresLostOnDowngrade(prevTier, newTier);
      const ok = await confirmDialog(
        t("admin_panel.confirm_downgrade", { username: user?.username ?? "user", from: prevTier, to: newTier, features: lost.join(", ") }),
      );
      if (!ok) {
        select.value = prevTier;
        return;
      }
    }

    const actionReason = await authorizeSensitiveAdminAction(
      "Update user tier",
      `user-tier-update:${uid}:${newTier}`,
    );
    if (!actionReason) {
      select.value = prevTier;
      return;
    }

    try {
      await updateUserTierApi(uid, newTier, actionReason);
      showToast(t("admin_panel.tier_updated"), "success");
      await loadUsers();
      repaint();
    } catch (err) {
      select.value = prevTier;
      showToast(getApiErrorMessage(err), "error");
    }
  }
  container.querySelector("#adm-users-body")?.addEventListener("change", (e) => void handleTierChange(e));
  container.querySelector("#adm-users-cards")?.addEventListener("change", (e) => void handleTierChange(e));

  container.addEventListener("click", async (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>(".adm-act-revoke-user-inv");
    if (!btn) return;
    const row = btn.closest<HTMLElement>("[data-user-inv-id]");
    if (!row) return;
    const invitationId = Number(row.dataset["userInvId"]);
    if (!Number.isFinite(invitationId)) return;
    if (!(await confirmDialog(t("admin.confirm_revoke_invite")))) return;
    const actionReason = await authorizeSensitiveAdminAction(
      "Revoke user invitation",
      `user-invitation-revoke:${invitationId}`,
    );
    if (!actionReason) return;
    try {
      await revokeUserInvitationApi(invitationId, actionReason);
      showToast(t("admin.invite_revoked"), "success");
      await loadUsers();
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  }, { once: false });

  // Sessions section
  container.querySelector("#adm-refresh-sessions")?.addEventListener("click", () => {
    void loadSessions().then(repaint);
  });
  container.querySelector("#adm-revoke-all")?.addEventListener("click", async () => {
    const actionReason = await authorizeSensitiveAdminAction(
      t("admin.action_revoke_all_sessions"),
      "all-session-revoke",
    );
    if (!actionReason) return;
    try {
      const n = await revokeAllSessionsApi(actionReason);
      showToast(t("admin.sessions_revoked", { count: n }), "success");
      await loadSessions();
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });

  // Audit section
  container.querySelector("#adm-audit-filter")?.addEventListener("submit", (e) => {
    e.preventDefault();
    void loadAudit(0, readAuditFilters()).then(repaint);
  });
  container.querySelector("#adm-audit-prev")?.addEventListener("click", () => {
    void loadAudit(Math.max(0, state.auditOffset - AUDIT_PAGE_SIZE), readAuditFilters()).then(repaint);
  });
  container.querySelector("#adm-audit-next")?.addEventListener("click", () => {
    void loadAudit(state.auditOffset + AUDIT_PAGE_SIZE, readAuditFilters()).then(repaint);
  });

  // Invitations section
  container.querySelector("#adm-refresh-inv")?.addEventListener("click", () => {
    void loadInvitations().then(repaint);
  });
  container.querySelector("#adm-create-inv-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const ctx = gardenContextFn?.();
    if (!ctx?.activeGardenId) { showToast(t("error.missing_garden"), "error"); return; }
    const username = queryInput("adm-inv-username")?.value.trim() ?? "";
    const role = (querySelect("adm-inv-role")?.value ?? "viewer") as "viewer" | "editor" | "admin";
    const ttlRaw = queryInput("adm-inv-ttl")?.value.trim() ?? "";
    if (!username) return;
    const ttl = ttlRaw ? Number(ttlRaw) : undefined;
    try {
      const result = await createGardenInvitationApi(ctx.activeGardenId, username, role, ttl);
      const inviteLink = buildInvitationLink(result.invite_token);
      void navigator.clipboard?.writeText(inviteLink).catch(() => {});
      state.lastInviteLink = inviteLink;
      showToast(t("admin.invite_link_copied", { expires: fmtDate(result.invitation.expires_at_ms) }), "success");
      await loadInvitations();
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });
  container.querySelector("#adm-inv-link-copy")?.addEventListener("click", () => {
    const input = queryInput("adm-inv-link-input");
    if (!input?.value) return;
    input.select();
    void navigator.clipboard?.writeText(input.value).then(() => {
      showToast(t("admin.link_copied"), "success");
    }).catch(() => {});
  });
  container.querySelector("#adm-users-body")?.addEventListener("click", () => {}); // handled above
  const invBody = container.querySelector("tbody");
  // Wire invitation revoke buttons via delegation on the entire admin view
  container.addEventListener("click", async (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>(".adm-act-revoke-inv");
    if (!btn) return;
    const row = btn.closest<HTMLElement>("[data-inv-id]");
    if (!row) return;
    const invId = Number(row.dataset["invId"]);
    const ctx = gardenContextFn?.();
    if (!ctx?.activeGardenId || !Number.isFinite(invId)) return;
    if (!(await confirmDialog(t("admin.confirm_revoke_invite")))) return;
    try {
      await revokeGardenInvitationApi(ctx.activeGardenId, invId);
      showToast(t("admin.invite_revoked"), "success");
      await loadInvitations();
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  }, { once: false });

  // System section
  container.querySelector("#adm-refresh-system")?.addEventListener("click", () => {
    void loadSystem().then(repaint);
  });
  container.querySelector("#adm-sign-out")?.addEventListener("click", async () => {
    try {
      await logoutApi();
    } catch { /* clear local state anyway */ }
    await clearOfflineQueue().catch(() => undefined);
    clearStoredAuthToken();
    resetAdminPanelSensitiveState();
    onSignOut?.();
  });
  container.querySelector("#adm-ero-toggle")?.addEventListener("click", async () => {
    const next = !state.emergencyReadOnly.enabled;
    const action = next ? t("admin_panel.action_enable") : t("admin_panel.action_disable");
    const actionReason = await authorizeSensitiveAdminAction(
      t("admin.action_ero_toggle", { action }),
      `emergency-read-only:${next ? "enable" : "disable"}`,
    );
    if (!actionReason) return;
    let expiresInMinutes: number | undefined;
    if (next) {
      const raw = await promptDialog(
        t("admin.ero_expiry_prompt"),
        "60",
      );
      if (raw === null) return;
      const trimmed = raw.trim();
      if (trimmed) {
        const parsed = Number(trimmed);
        if (!Number.isFinite(parsed) || parsed < 5 || parsed > 24 * 60) {
          showToast(t("admin.expiry_invalid"), "error");
          return;
        }
        expiresInMinutes = parsed;
      }
    }
    try {
      const options = expiresInMinutes === undefined
        ? { actionReason }
        : { actionReason, expiresInMinutes };
      state.emergencyReadOnly = await setEmergencyReadOnlyApi(next, options);
      showToast(
        t("admin.ero_toggled", { state: state.emergencyReadOnly.enabled ? t("admin.enabled") : t("admin.disabled") }),
        "success",
      );
      repaint();
    } catch (err) { showToast(getApiErrorMessage(err), "error"); }
  });
}

// ── Public API ─────────────────────────────────────────────

export async function activateAdminPanel(): Promise<void> {
  await loadSettings();
  const visibleSections = getVisibleSections();
  if (!adminPanelInitialized || !visibleSections.includes(state.section)) {
    state.section = defaultSection();
  }
  adminPanelInitialized = true;
  await loadAndRepaintSection();
  repaintFull();
}

export function refreshAdminPanelLocalization(): void {
  repaintFull();
}

export function getAdminViewMarkup(): string {
  return `<section id="admin-view" class="view adm-view" role="tabpanel" aria-labelledby="top-tab-admin" hidden></section>`;
}
