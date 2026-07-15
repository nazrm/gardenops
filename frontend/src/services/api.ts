import { t } from "../core/i18n";
import { redactedLocationPath } from "../core/urlSecurity";
import { reportHandledApiError } from "./errorReporter";
import type {
  AttentionTodayResponse,
  AttentionPreferences,
  AttentionPreferencesUpdate,
  CalendarEventsResponse,
  CalendarManualEventInput,
  CalendarPreferences,
  CalendarPreferencesResponse,
  CalendarSubscription,
  CompanionCheck,
  GardenIssue,
  GardenTask,
  GardenProfile,
  HarvestListResponse,
  HarvestSummary,
  IssueHistoryResponse,
  IssueListResponse,
  IssueSummary,
  JournalEntry,
  JournalListResponse,
  MapObject,
  MapObjectInput,
  MapObjectUnit,
  MapObjectUnitInput,
  NotificationListResponse,
  NotificationPreferences,
  PasswordPolicy,
  Plant,
  PlannerResult,
  Plot,
  ProcurementListResponse,
  ProcurementSummary,
  SavedView,
  SavedViewPreset,
  TaskListResponse,
  WeatherAlert,
  WeatherSummary,
} from "../core/models";

const AUTH_CSRF_STORAGE_KEY = "gardenops-csrf-token";
const ACTIVE_GARDEN_STORAGE_KEY = "gardenops-active-garden-id";
const DEFAULT_CSRF_COOKIE_NAMES = ["gardenops_csrf", "XSRF-TOKEN"];
export const OFFLINE_OPERATION_ID_HEADER = "X-Offline-Operation-Id";

export class ApiError extends Error {
  status: number;
  requestId: string;
  path: string;
  reportable: boolean;

  constructor(
    status: number,
    message: string,
    options?: { requestId?: string; path?: string; reportable?: boolean },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = options?.requestId ?? "";
    this.path = options?.path ?? "";
    this.reportable = options?.reportable ?? false;
  }
}

export interface TaskActionRequest {
  action: "complete" | "skip" | "snooze" | "reschedule";
  expected_updated_at_ms?: number;
  confirm_outside_window?: boolean;
  snooze_until?: string;
  reschedule_to?: string;
  notes?: string;
  completed_plant_ids?: string[];
  completion_outcome?: "done" | "not_seen_blooming_this_season";
}

export type RevisionedTaskActionRequest = TaskActionRequest & {
  expected_updated_at_ms: number;
};

export type BatchTaskActionRequest = Omit<
  TaskActionRequest,
  "expected_updated_at_ms"
> & {
  expected_updated_at_ms_by_task_id: Record<string, number>;
};

export function withTaskActionRevision(
  task: Pick<GardenTask, "updated_at_ms">,
  body: TaskActionRequest,
): RevisionedTaskActionRequest {
  return {
    ...body,
    expected_updated_at_ms: task.updated_at_ms,
  };
}

export function withBatchTaskActionRevisions(
  tasks: readonly Pick<GardenTask, "id" | "updated_at_ms">[],
  body: Omit<TaskActionRequest, "expected_updated_at_ms">,
): BatchTaskActionRequest {
  return {
    ...body,
    expected_updated_at_ms_by_task_id: Object.fromEntries(
      tasks.map((task) => [task.id, task.updated_at_ms]),
    ),
  };
}

function normalizeApiPath(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return `${input.pathname}${input.search}`;
  if (typeof Request !== "undefined" && input instanceof Request) {
    try {
      const url = new URL(input.url, location.origin);
      return `${url.pathname}${url.search}`;
    } catch {
      return input.url;
    }
  }
  return "";
}

function encodeApiPathSegment(value: string): string {
  return encodeURIComponent(value);
}

function responseRequestId(response: Response): string {
  return (response.headers.get("X-Request-ID") || "").trim();
}

function shouldReportApiError(status: number, path: string): boolean {
  return status === 0 || status >= 500;
}

function stringifyApiDetail(value: unknown, fallback: string): string {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || fallback;
  }
  if (Array.isArray(value)) {
    const messages = value
      .map((item) => {
        if (typeof item === "string") return item.trim();
        if (item && typeof item === "object") {
          const detail = item as Record<string, unknown>;
          const msg = detail["msg"] ?? detail["message"] ?? detail["detail"];
          if (typeof msg === "string" && msg.trim()) return msg.trim();
        }
        return "";
      })
      .filter((msg) => msg.length > 0);
    return messages.length ? messages.join("; ") : fallback;
  }
  if (value && typeof value === "object") {
    const detail = value as Record<string, unknown>;
    const msg = detail["msg"] ?? detail["message"] ?? detail["detail"];
    if (typeof msg === "string" && msg.trim()) return msg.trim();
  }
  return fallback;
}

function parseApiErrorJson(
  status: number,
  body: unknown,
  fallback: string,
  options?: { requestId?: string; path?: string; reportable?: boolean },
): ApiError {
  if (body && typeof body === "object" && "detail" in body) {
    return new ApiError(
      status,
      stringifyApiDetail((body as Record<string, unknown>)["detail"], fallback),
      options,
    );
  }
  return new ApiError(status, stringifyApiDetail(body, fallback), options);
}

function emitApiErrorReport(error: ApiError): void {
  if (!error.reportable) return;
  reportHandledApiError({
    message: error.message,
    requestId: error.requestId,
    apiPath: error.path,
    statusCode: error.status,
    featureArea: redactedLocationPath(location.href),
  });
}

function readCookieValue(name: string): string {
  if (typeof document === "undefined") return "";
  const needle = `${encodeURIComponent(name)}=`;
  const parts = document.cookie.split("; ");
  for (const part of parts) {
    if (!part.startsWith(needle)) continue;
    const value = part.slice(needle.length).trim();
    if (!value) return "";
    try {
      return decodeURIComponent(value);
    } catch {
      return value;
    }
  }
  return "";
}

function getStoredCsrfToken(): string {
  for (const cookieName of DEFAULT_CSRF_COOKIE_NAMES) {
    const cookieValue = readCookieValue(cookieName).trim();
    if (cookieValue) return cookieValue;
  }
  return "";
}

function readStoredActiveGardenId(): number | null {
  try {
    const raw = sessionStorage.getItem(ACTIVE_GARDEN_STORAGE_KEY)
      ?? localStorage.getItem(ACTIVE_GARDEN_STORAGE_KEY)
      ?? "";
    if (!raw.trim()) return null;
    const parsed = Number.parseInt(raw, 10);
    if (!Number.isFinite(parsed) || parsed <= 0) return null;
    sessionStorage.setItem(ACTIVE_GARDEN_STORAGE_KEY, String(parsed));
    localStorage.removeItem(ACTIVE_GARDEN_STORAGE_KEY);
    return parsed;
  } catch {
    return null;
  }
}

let activeGardenId: number | null = readStoredActiveGardenId();
type ApiRequestOptions = {
  timeoutMs?: number;
  timeoutMessage?: string;
  gardenId?: number | null;
  operationId?: string;
  suppressAuthExpiry?: boolean;
};

export function getActiveGardenContext(): number | null {
  return activeGardenId;
}

export function setActiveGardenContext(gardenId: number | null): void {
  activeGardenId = gardenId && Number.isFinite(gardenId) && gardenId > 0
    ? Math.floor(gardenId)
    : null;
  try {
    if (activeGardenId === null) {
      sessionStorage.removeItem(ACTIVE_GARDEN_STORAGE_KEY);
      localStorage.removeItem(ACTIVE_GARDEN_STORAGE_KEY);
      return;
    }
    const raw = String(activeGardenId);
    sessionStorage.setItem(ACTIVE_GARDEN_STORAGE_KEY, raw);
    localStorage.removeItem(ACTIVE_GARDEN_STORAGE_KEY);
  } catch {
    // ignore storage access issues
  }
}

/** Clear legacy auth token storage from before the cookie-based session migration. */
export function clearLegacyAuthStorage(): void {
  try {
    sessionStorage.removeItem("gardenops-auth-token");
    localStorage.removeItem("gardenops-auth-token");
    sessionStorage.removeItem("gardenops-api-key");
    localStorage.removeItem("gardenops-api-key");
  } catch {
    // ignore storage access issues
  }
}

export function readStoredCsrfToken(): string {
  return getStoredCsrfToken();
}

export function setStoredCsrfToken(_token: string): void {
  try {
    sessionStorage.removeItem(AUTH_CSRF_STORAGE_KEY);
    localStorage.removeItem(AUTH_CSRF_STORAGE_KEY);
  } catch {
    // ignore storage access issues
  }
}

export function clearStoredAuthToken(): void {
  try {
    sessionStorage.removeItem("gardenops-auth-token");
    localStorage.removeItem("gardenops-auth-token");
    sessionStorage.removeItem("gardenops-api-key");
    localStorage.removeItem("gardenops-api-key");
    sessionStorage.removeItem(AUTH_CSRF_STORAGE_KEY);
    localStorage.removeItem(AUTH_CSRF_STORAGE_KEY);
  } catch {
    // ignore storage access issues
  }
}

export function hasStoredAuthToken(): boolean {
  return getStoredCsrfToken().length > 0;
}

function authHeaders(gardenIdOverride?: number | null): HeadersInit {
  const headers: Record<string, string> = {};
  try {
    const gardenId = gardenIdOverride !== undefined ? gardenIdOverride : activeGardenId;
    if (gardenId !== null && gardenId !== undefined) headers["x-garden-id"] = String(gardenId);
  } catch {
    // no-op for environments without localStorage access
  }
  return headers;
}

let _onAuthExpired: (() => void) | null = null;

export function setOnAuthExpired(cb: () => void): void {
  _onAuthExpired = cb;
}

const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_TIMEOUT_MESSAGE = "Request timed out";
const AI_CHAT_TIMEOUT_MS = 90_000;
const AI_CHAT_TIMEOUT_MESSAGE = "AI request timed out";

async function apiFetch(
  input: RequestInfo | URL,
  init?: RequestInit & ApiRequestOptions,
): Promise<Response> {
  const path = normalizeApiPath(input);
  const {
    timeoutMs: requestedTimeoutMs,
    timeoutMessage: requestedTimeoutMessage,
    gardenId,
    operationId,
    ...fetchInit
  } = init ?? {};
  const headers = new Headers(authHeaders(gardenId));
  const requestHeaders = new Headers((fetchInit.headers as HeadersInit | undefined) ?? {});
  requestHeaders.forEach((value, key) => {
    headers.set(key, value);
  });
  if (operationId) {
    headers.set(OFFLINE_OPERATION_ID_HEADER, operationId);
  }
  const method = (fetchInit.method ?? "GET").toUpperCase();
  if (
    (method === "POST" || method === "PUT" || method === "PATCH" || method === "DELETE")
    && !headers.has("x-csrf-token")
    && !headers.has("x-xsrf-token")
  ) {
    const csrfToken = getStoredCsrfToken();
    if (csrfToken) {
      headers.set("x-csrf-token", csrfToken);
    }
  }
  const controller = new AbortController();
  const timeoutMs = requestedTimeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  const signal = fetchInit.signal
    ? AbortSignal.any([fetchInit.signal, controller.signal])
    : controller.signal;
  try {
    return await fetch(input, {
      ...fetchInit,
      headers,
      credentials: fetchInit.credentials ?? "include",
      signal,
    });
  } catch (err) {
    const timedOut = controller.signal.aborted && !(fetchInit.signal?.aborted ?? false);
    const apiError = new ApiError(
      0,
      timedOut ? (requestedTimeoutMessage ?? DEFAULT_TIMEOUT_MESSAGE) : "Network request failed",
      { path, reportable: true },
    );
    emitApiErrorReport(apiError);
    throw apiError;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function checked(
  res: Response,
  path: string,
  options: Pick<ApiRequestOptions, "suppressAuthExpiry"> = {},
): Promise<Response> {
  if (!res.ok) {
    if (res.status === 401 && !options.suppressAuthExpiry && _onAuthExpired) {
      _onAuthExpired();
    }
    const fallback = `Request failed (${res.status})`;
    const body = await res.json().catch(() => ({})) as unknown;
    const detail = body && typeof body === "object" && "detail" in body
      ? stringifyApiDetail((body as Record<string, unknown>)["detail"], fallback)
      : fallback;
    // Stale garden context — clear it so subsequent requests use the default
    if (res.status === 404 && detail === "Garden not found") {
      setActiveGardenContext(null);
    }
    const apiError = parseApiErrorJson(res.status, body, fallback, {
      requestId: responseRequestId(res),
      path,
      reportable: shouldReportApiError(res.status, path),
    });
    emitApiErrorReport(apiError);
    throw apiError;
  }
  return res;
}

async function apiGet<T>(
  path: string,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<T> {
  const response = await checked(await apiFetch(path, options), path);
  return (await response.json()) as T;
}

async function apiPost<T>(
  path: string,
  body: unknown,
  options?: ApiRequestOptions,
): Promise<T> {
  const request: RequestInit & ApiRequestOptions = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  if (options?.timeoutMs !== undefined) {
    request.timeoutMs = options.timeoutMs;
  }
  if (options?.timeoutMessage !== undefined) {
    request.timeoutMessage = options.timeoutMessage;
  }
  if (options?.gardenId !== undefined) {
    request.gardenId = options.gardenId;
  }
  if (options?.operationId !== undefined) {
    request.operationId = options.operationId;
  }
  const response = await checked(
    await apiFetch(path, request),
    path,
    options ?? {},
  );
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function apiPatch<T>(
  path: string,
  body: unknown,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<T> {
  const request: RequestInit & Pick<ApiRequestOptions, "gardenId"> = {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  if (options?.gardenId !== undefined) {
    request.gardenId = options.gardenId;
  }
  const response = await checked(await apiFetch(path, request), path);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function apiDelete<T>(
  path: string,
  options: { headers?: HeadersInit; gardenId?: number | null } = {},
): Promise<T> {
  const request: RequestInit = { method: "DELETE" };
  if (options.headers !== undefined) {
    request.headers = options.headers;
  }
  const apiRequest = request as RequestInit & Pick<ApiRequestOptions, "gardenId">;
  if ("gardenId" in options) {
    apiRequest.gardenId = options.gardenId ?? null;
  }
  const response = await checked(
    await apiFetch(path, apiRequest),
    path,
  );
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function parseApiErrorPayload(
  status: number,
  raw: string,
  options?: { requestId?: string; path?: string; reportable?: boolean },
): ApiError {
  const fallback = `Request failed (${status})`;
  try {
    return parseApiErrorJson(status, JSON.parse(raw) as unknown, fallback, options);
  } catch {
    return new ApiError(status, raw.trim() || fallback, options);
  }
}

function uploadBinary<T>(
  path: string,
  payload: Blob,
  headers: Record<string, string>,
  options?: {
    onProgress?: (pct: number) => void;
    gardenId?: number | null;
    operationId?: string;
  },
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path, true);
    xhr.withCredentials = true;
    const mergedHeaders = new Headers(authHeaders(options?.gardenId));
    for (const [key, value] of Object.entries(headers)) {
      if (value) mergedHeaders.set(key, value);
    }
    if (options?.operationId) {
      mergedHeaders.set(OFFLINE_OPERATION_ID_HEADER, options.operationId);
    }
    if (!mergedHeaders.has("x-csrf-token")) {
      const csrfToken = getStoredCsrfToken();
      if (csrfToken) mergedHeaders.set("x-csrf-token", csrfToken);
    }
    mergedHeaders.forEach((value, key) => {
      xhr.setRequestHeader(key, value);
    });
    xhr.upload.onprogress = (event) => {
      if (!options?.onProgress) return;
      if (!event.lengthComputable || event.total <= 0) {
        options.onProgress(0);
        return;
      }
      options.onProgress(Math.max(0, Math.min(100, Math.round((event.loaded / event.total) * 100))));
    };
    xhr.onerror = () => {
      const apiError = new ApiError(0, "Upload failed", { path, reportable: true });
      emitApiErrorReport(apiError);
      reject(apiError);
    };
    xhr.onabort = () => reject(new ApiError(0, "Upload cancelled", { path }));
    xhr.onload = () => {
      const status = xhr.status;
      if (status < 200 || status >= 300) {
        const apiError = parseApiErrorPayload(status, xhr.responseText || "", {
          requestId: xhr.getResponseHeader("X-Request-ID") || "",
          path,
          reportable: shouldReportApiError(status, path),
        });
        emitApiErrorReport(apiError);
        reject(apiError);
        return;
      }
      try {
        resolve(JSON.parse(xhr.responseText) as T);
      } catch {
        const apiError = new ApiError(status, "Upload returned invalid JSON", {
          requestId: xhr.getResponseHeader("X-Request-ID") || "",
          path,
          reportable: true,
        });
        emitApiErrorReport(apiError);
        reject(apiError);
      }
    };
    xhr.send(payload);
  });
}

const ERROR_MAP: Record<string, string> = {
  "Write access required": "error.write_access",
  "Forbidden: write access required": "error.write_access",
  "Authentication required": "error.auth_required",
  "Plant not found": "error.plant_not_found",
  "Plant not found in active garden": "error.plant_not_found",
  "Plot not found": "error.plot_not_found",
  "Plot not found in active garden": "error.plot_not_found",
  "Task not found": "error.task_not_found",
  "Issue not found": "error.issue_not_found",
  "Harvest entry not found": "error.harvest_not_found",
  "Missing garden context": "error.missing_garden",
};

export function getApiErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const raw = err.message.trim();
    const i18nKey = ERROR_MAP[raw];
    if (i18nKey) return t(i18nKey);
    if (err.status === 403) return t("error.forbidden");
    if (err.status === 401) return t("error.auth_required");
    return raw || t("error.request_failed", { status: err.status });
  }
  if (err instanceof Error) return err.message.trim() || t("error.unknown");
  return String(err);
}

export function isAuthApiError(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 401 || err.status === 403);
}

export interface AuthStatus {
  auth_required: boolean;
  auth_mode: "session" | "api_key" | "hybrid";
  session_auth_enabled: boolean;
  api_key_auth_enabled: boolean;
  bootstrap_required: boolean;
  user_lifecycle_enabled: boolean;
  admin_mfa_required: boolean;
  passkeys_enabled: boolean;
}

export interface AppVersionInfo {
  version: string;
  base_version: string;
  git_commit: string | null;
  dirty: boolean;
  last_updated_at_ms: number | null;
}

export interface AuthMfaState {
  enabled: boolean;
  setup_required: boolean;
  enrolled_at: string | null;
  pending_enrollment: boolean;
  pending_expires_at_ms: number | null;
  recovery_codes_remaining: number;
  methods: string[];
}

export interface AuthMfaChallenge {
  required: boolean;
  setup_required: boolean;
  methods: string[];
  method?: string | null;
}

export interface PlotAssignmentMeaning {
  pattern: string;
  label: string;
  description: string;
}

export type MediaTargetType = "journal_entry" | "plant" | "plot" | "issue" | "harvest_entry";

export interface MediaLinkRef {
  target_type: MediaTargetType;
  target_id: string;
  sort_order: number;
}

export interface MediaAsset {
  asset_id: string;
  mime_type: string;
  bytes: number;
  width: number;
  height: number;
  created_at_ms: number;
  actor_user_id: number | null;
  original_filename: string;
  preview_url: string;
  original_url: string;
  is_cover: boolean;
  targets: MediaLinkRef[];
}

export interface MediaListResponse {
  items: MediaAsset[];
  total: number;
  limit: number;
  offset: number;
}

export interface MediaTargetSummary {
  target_id: string;
  asset: MediaAsset;
}

export interface MediaSummariesResponse {
  target_type: MediaTargetType;
  items: MediaTargetSummary[];
}

export interface PopulatePlantCoverResultItem {
  plant_id: string;
  status: "adopted_existing" | "imported_remote" | "skipped";
  detail: string;
}

export interface MissingPlantCoverReportItem {
  plant_id: string;
  name: string;
  latin: string;
  link: string;
  reason_code:
    | "missing_latin"
    | "missing_link"
    | "remote_error"
    | "existing_media_needs_cover"
    | "ready_remote_import";
  status_detail: string;
  attempted_at_ms: number | null;
  has_existing_media: boolean;
}

export interface MissingPlantCoversResponse {
  items: MissingPlantCoverReportItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface PopulatePlantCoversResult {
  status: "ok";
  cursor: string | null;
  has_more: boolean;
  processed: number;
  total_without_cover_before: number;
  remaining_without_cover: number;
  adopted_existing: number;
  imported_remote: number;
  skipped: number;
  items: PopulatePlantCoverResultItem[];
}

export interface AuthUserProfile {
  username: string;
  role: "viewer" | "editor" | "admin";
  garden_id: number | null;
  garden_visible: boolean;
  garden_role: "viewer" | "editor" | "admin" | null;
  auth_type: "none" | "session" | "api_key";
  write_access: boolean;
  language: "en" | "no";
  shademap_available: boolean;
  mfa_enabled: boolean;
  mfa_setup_required: boolean;
  mfa_authenticated: boolean;
  mfa_methods: string[];
  must_change_password: boolean;
  passkeys_enabled: boolean;
  passkey_enrolled: boolean;
  passkey_count: number;
  password_auth_disabled: boolean;
  passkey_prompt_eligible: boolean;
  passkey_prompt_dismissed_until_ms: number;
  plot_assignment_meanings: PlotAssignmentMeaning[];
  subscription_tier: "home" | "enthusiast" | "pro";
  allowed_features: string[];
  security_warnings: string[];
}

export interface CalendarSubscriptionCreateResult {
  status: string;
  subscription: CalendarSubscription;
  feed_path: string;
}

export interface GardenSummary {
  id: number;
  slug: string;
  name: string;
  created_at?: string;
  role: "viewer" | "editor" | "admin";
  active?: boolean;
  onboarding_complete?: boolean;
  owned_by_current_user?: boolean;
}

export interface GardenSettings {
  garden_id: number;
  name: string;
  grid_rows: number;
  grid_cols: number;
  latitude: number | null;
  longitude: number | null;
  address: string;
  onboarding_complete: boolean;
}

export interface GardenLidarStatus {
  garden_id: number;
  available: boolean;
  uploaded: boolean;
  filename: string;
  uploaded_filename: string;
  bytes: number;
  uploaded_bytes: number;
  updated_at_ms: number | null;
  source: string;
  max_upload_bytes: number;
}

export interface GardenGeocodeResult {
  display_name: string;
  latitude: number;
  longitude: number;
}

export interface ZoneCreateResult {
  zone_code: string;
  zone_name: string;
  plots_created: number;
  requested_cells: number;
  skipped_cells: number;
  plots: { plot_id: string; grid_row: number; grid_col: number; plot_number: number }[];
}

export interface CompleteOnboardingResult extends GardenSettings {
  mode: "manual" | "import";
  plots_created: number;
}

export interface GardenMembership {
  user_id: number;
  username: string;
  role: "viewer" | "editor" | "admin";
  created_at: string;
}

export interface AuthManagedUser {
  id: number;
  username: string;
  role: "viewer" | "editor" | "admin";
  is_active: boolean;
  must_change_password: boolean;
  created_by_user_id: number | null;
  deactivated_at: string | null;
  deactivated_reason: string | null;
  created_at: string;
  last_login_at: string | null;
  mfa_enabled: boolean;
  mfa_enrolled_at: string | null;
  managed_garden_id: number | null;
  managed_garden_name: string | null;
  managed_garden_onboarding_complete: boolean | null;
  managed_garden_count: number;
  subscription_tier: "home" | "enthusiast" | "pro";
}

export interface AuthResetIssueResult {
  status: string;
  user_id: number;
  reset_token: string;
  expires_at_ms: number;
  must_change_password: boolean;
  revoked_sessions?: number;
}

export interface AuthDeleteUserResult {
  status: string;
  operation: "hard_deleted" | "deactivated";
  hard_delete: boolean;
  user_id: number;
  username: string;
  revoked_sessions: number;
  transfer_required?: boolean;
  retention_required?: boolean;
  blocking_resources?: string[];
  reference_counts?: Record<string, number>;
}

export interface RestartUserOnboardingResult {
  status: string;
  user_id: number;
  username: string;
  garden_id: number;
  garden_name: string;
  onboarding_complete: boolean;
}

export interface GardenInvitation {
  id: number;
  garden_id: number;
  invitee_username: string;
  role: "viewer" | "editor" | "admin";
  created_by_user_id: number | null;
  created_at_ms: number;
  expires_at_ms: number;
  accepted_at_ms: number | null;
  accepted_user_id: number | null;
  revoked_at_ms: number | null;
  status: "pending" | "accepted" | "revoked" | "expired";
}

export interface UserInvitation {
  id: number;
  invitee_username: string;
  role: "editor" | "admin";
  created_by_user_id: number | null;
  created_at_ms: number;
  expires_at_ms: number;
  accepted_at_ms: number | null;
  accepted_user_id: number | null;
  revoked_at_ms: number | null;
  status: "pending" | "accepted" | "revoked" | "expired";
  scope: "personal_garden";
}

export interface AuditEvent {
  id: number;
  occurred_at_ms: number;
  actor_user_id: number | null;
  actor_username: string;
  actor_role: string;
  actor_auth_type: string;
  garden_id: number | null;
  method: string;
  path: string;
  status_code: number;
  remote_host: string;
  detail: string;
}

export interface AuditEventQuery {
  limit?: number;
  offset?: number;
  garden_id?: number;
  actor?: string;
  path_prefix?: string;
  method?: string;
  status_code?: number;
  from_ms?: number;
  to_ms?: number;
}

export interface AuditEventPage {
  events: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface ActiveSession {
  token_hash: string;
  user_id: number;
  username: string;
  role: string;
  expires_at_ms: number;
  created_at_ms: number;
  last_seen_at_ms: number;
  reauthenticated_at_ms: number;
  mfa_authenticated_at_ms: number;
  mfa_setup_required: boolean;
}

export interface EmergencyReadOnlyStatus {
  enabled: boolean;
  expires_at_ms: number | null;
}

export interface AdminSystemHealth {
  status: "ok" | "degraded" | "corrupt";
  db_quick_check: string;
  fk_violations: number;
  table_count: number;
  last_backup: string | null;
  uptime_seconds: number;
  taillight?: {
    dropped: number;
    send_failed: number;
  };
}

export interface ProviderBudgetScopeSummary {
  scope_id: number;
  request_count: number;
  limit: number;
}

export interface ProviderBudgetFeatureSummary {
  feature: string;
  label: string;
  user_limit: number;
  garden_limit: number;
  concurrency_limit: number;
  active_concurrency: number;
  user_total_requests: number;
  garden_total_requests: number;
  top_user_scope: ProviderBudgetScopeSummary | null;
  top_garden_scope: ProviderBudgetScopeSummary | null;
}

export interface SecurityMetrics {
  counters: Record<string, number>;
  rates: Record<string, number>;
  garden_scope: {
    recent_destructive_admin_garden_ids: number[];
  };
  provider_limits: {
    day: string;
    features: ProviderBudgetFeatureSummary[];
    active_concurrency: Record<string, number>;
  };
  exporter: {
    enabled: boolean;
    destination: string;
    pending_count: number;
    oldest_pending_at_ms: number | null;
    last_attempt_at_ms: number | null;
    last_success_at_ms: number | null;
    last_error: string;
    snapshot_interval_seconds: number;
    poll_interval_seconds: number;
  };
}

export interface SecurityAlert {
  name: string;
  value: number;
  threshold: number;
  severity: string;
  ratio_pct?: number;
  ratio_threshold_pct?: number;
  request_count?: number;
  miss_count?: number;
  garden_ids?: number[];
}

export interface SecurityAlertsResponse {
  alerts: SecurityAlert[];
  thresholds: Record<string, number>;
  rates: Record<string, number>;
}

export interface LoginResponse {
  status: string;
  expires_at_ms?: number;
  user: {
    username: string;
    role: "viewer" | "editor" | "admin";
    must_change_password?: boolean;
  };
  mfa?: AuthMfaChallenge;
}

export interface PasskeySummary {
  id: number;
  nickname: string;
  created_at_ms: number;
  last_used_at_ms: number | null;
  transports: string[];
  credential_device_type: string;
  credential_backed_up: boolean;
}

export interface PasskeyOptionsResponse {
  challenge_token: string;
  publicKey: unknown;
}

export async function getAuthStatusApi(): Promise<AuthStatus> {
  return apiGet<AuthStatus>("/api/auth/status");
}

export async function getAppVersionApi(): Promise<AppVersionInfo> {
  return apiGet<AppVersionInfo>("/api/version");
}

export async function bootstrapAuthApi(
  username: string,
  password: string,
): Promise<void> {
  await apiPost("/api/auth/bootstrap", { username, password, role: "admin" });
}

export async function loginApi(
  username: string,
  password: string,
  options: {
    mfaCode?: string;
    recoveryCode?: string;
  } = {},
): Promise<LoginResponse> {
  return apiPost<LoginResponse>("/api/auth/login", {
    username,
    password,
    mfa_code: options.mfaCode ?? "",
    recovery_code: options.recoveryCode ?? "",
  }, { suppressAuthExpiry: true });
}

export async function getPasskeysApi(): Promise<PasskeySummary[]> {
  const body = await apiGet<{ passkeys?: PasskeySummary[] }>("/api/auth/passkeys");
  return Array.isArray(body.passkeys) ? body.passkeys : [];
}

export async function beginPasskeyRegistrationApi(
  nickname = "",
  currentPassword = "",
): Promise<PasskeyOptionsResponse> {
  return apiPost<PasskeyOptionsResponse>(
    "/api/auth/passkeys/register/options",
    { nickname, current_password: currentPassword },
  );
}

export async function finishPasskeyRegistrationApi(
  challengeToken: string,
  nickname: string,
  credential: unknown,
): Promise<{ status: string; passkey: PasskeySummary }> {
  return apiPost<{ status: string; passkey: PasskeySummary }>(
    "/api/auth/passkeys/register/verify",
    {
      challenge_token: challengeToken,
      nickname,
      credential,
    },
  );
}

export async function dismissPasskeyPromptApi(
  dismissForDays = 30,
): Promise<{ status: string; passkey_prompt_dismissed_until_ms: number }> {
  return apiPost<{ status: string; passkey_prompt_dismissed_until_ms: number }>(
    "/api/auth/passkeys/prompt/dismiss",
    { dismiss_for_days: dismissForDays },
  );
}

export async function deletePasskeyApi(
  passkeyId: number,
  actionReason = "ui-passkey-delete",
): Promise<void> {
  await checked(
    await apiFetch(`/api/auth/passkeys/${passkeyId}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action_reason: actionReason }),
    }),
    `/api/auth/passkeys/${passkeyId}`,
  );
}

export async function beginPasskeyLoginApi(username: string): Promise<PasskeyOptionsResponse> {
  const trimmedUsername = username.trim();
  if (!trimmedUsername) {
    throw new Error("Username is required before passkey sign-in.");
  }
  return apiPost<PasskeyOptionsResponse>(
    "/api/auth/passkeys/login/options",
    { username: trimmedUsername },
  );
}

export async function finishPasskeyLoginApi(
  challengeToken: string,
  credential: unknown,
): Promise<LoginResponse> {
  return apiPost<LoginResponse>(
    "/api/auth/passkeys/login/verify",
    {
      challenge_token: challengeToken,
      credential,
    },
    { suppressAuthExpiry: true },
  );
}

export async function beginPasskeyReauthenticationApi(): Promise<PasskeyOptionsResponse> {
  return apiPost<PasskeyOptionsResponse>(
    "/api/auth/reauthenticate/passkey/options",
    {},
  );
}

export async function finishPasskeyReauthenticationApi(
  challengeToken: string,
  credential: unknown,
): Promise<{
  status: string;
  csrf_token: string;
  reauthenticated_at_ms: number;
  reauthenticated_until_ms: number;
  mfa_authenticated_at_ms: number;
}> {
  return apiPost<{
    status: string;
    csrf_token: string;
    reauthenticated_at_ms: number;
    reauthenticated_until_ms: number;
    mfa_authenticated_at_ms: number;
  }>(
    "/api/auth/reauthenticate/passkey/verify",
    {
      challenge_token: challengeToken,
      credential,
    },
    { suppressAuthExpiry: true },
  );
}

export async function changePasswordApi(
  currentPassword: string,
  newPassword: string,
): Promise<{ status: string; revoked_sessions: number }> {
  return apiPost<{ status: string; revoked_sessions: number }>(
    "/api/auth/change-password",
    {
      current_password: currentPassword,
      new_password: newPassword,
    },
  );
}

export async function logoutApi(): Promise<void> {
  await apiPost("/api/auth/logout", {});
}

export async function getAuthMeApi(): Promise<AuthUserProfile> {
  return apiGet<AuthUserProfile>("/api/auth/me");
}

export interface MeSettings {
  language: "en" | "no";
  mfa: AuthMfaState;
  plot_assignment_meanings: PlotAssignmentMeaning[];
}

export async function getAuthMeSettingsApi(): Promise<MeSettings> {
  return apiGet<MeSettings>("/api/auth/me/settings");
}

export type ProviderSecretStatus = {
  configured: boolean;
  source: "db" | "env" | "none";
  last4: string | null;
  updated_at_ms: number | null;
  updated_by_user_id: number | null;
  updated_by_username: string | null;
};

export type ProviderSecretKey =
  | "openai_api_key"
  | "anthropic_api_key"
  | "plantnet_api_key"
  | "shademap_api_key";

export interface ProviderSettings {
  ai_provider: "disabled" | "openai" | "anthropic";
  models: {
    openai_model: string;
    openai_fast_model: string;
    anthropic_model: string;
  };
  secrets: Record<ProviderSecretKey, ProviderSecretStatus>;
  secrets_encryption_configured: boolean;
}

export interface ProviderSettingsUpdate {
  ai_provider?: "disabled" | "openai" | "anthropic";
  openai_model?: string;
  openai_fast_model?: string;
  anthropic_model?: string;
  openai_api_key?: string;
  anthropic_api_key?: string;
  plantnet_api_key?: string;
  shademap_api_key?: string;
  clear_openai_api_key?: boolean;
  clear_anthropic_api_key?: boolean;
  clear_plantnet_api_key?: boolean;
  clear_shademap_api_key?: boolean;
  action_reason: string;
}

export async function getProviderSettingsApi(): Promise<ProviderSettings> {
  return apiGet<ProviderSettings>("/api/admin/provider-settings");
}

export async function updateProviderSettingsApi(
  update: ProviderSettingsUpdate,
): Promise<ProviderSettings> {
  const response = await checked(
    await apiFetch("/api/admin/provider-settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }),
    "/api/admin/provider-settings",
  );
  return (await response.json()) as ProviderSettings;
}

export async function getAuthMfaStatusApi(): Promise<AuthMfaState> {
  return apiGet<AuthMfaState>("/api/auth/mfa");
}

export async function startAuthTotpEnrollmentApi(): Promise<{
  status: string;
  secret: string;
  provisioning_uri: string;
  expires_at_ms: number;
}> {
  return apiPost<{
    status: string;
    secret: string;
    provisioning_uri: string;
    expires_at_ms: number;
  }>("/api/auth/mfa/totp/start", {});
}

export async function confirmAuthTotpEnrollmentApi(code: string): Promise<{
  status: string;
  recovery_codes: string[];
  mfa: AuthMfaState;
}> {
  return apiPost<{
    status: string;
    recovery_codes: string[];
    mfa: AuthMfaState;
  }>("/api/auth/mfa/totp/confirm", { code });
}

export async function disableAuthMfaApi(actionReason: string): Promise<{
  status: string;
  mfa: AuthMfaState;
}> {
  return apiPost<{
    status: string;
    mfa: AuthMfaState;
  }>("/api/auth/mfa/disable", {
    action_reason: actionReason,
  });
}

export async function regenerateAuthMfaRecoveryCodesApi(actionReason: string): Promise<{
  status: string;
  recovery_codes: string[];
  mfa: AuthMfaState;
}> {
  return apiPost<{
    status: string;
    recovery_codes: string[];
    mfa: AuthMfaState;
  }>("/api/auth/mfa/recovery-codes/regenerate", {
    action_reason: actionReason,
  });
}

export async function updateAuthMeSettingsApi(
  settings: {
    plot_assignment_meanings?: PlotAssignmentMeaning[];
    language?: "en" | "no";
  },
): Promise<void> {
  await checked(
    await apiFetch("/api/auth/me/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
    "/api/auth/me/settings",
  );
}

export async function getGardensApi(): Promise<GardenSummary[]> {
  return apiGet<GardenSummary[]>("/api/gardens");
}

export async function createGardenApi(
  name: string,
  slug?: string,
): Promise<GardenSummary> {
  return apiPost<GardenSummary>("/api/gardens", { name, slug });
}

export async function deleteGardenApi(
  gardenId: number,
  actionReason = "ui-garden-delete",
): Promise<{
  status: string;
  garden_id: number;
  garden_name: string;
  plots_deleted: number;
  snapshots_deleted: number;
  plants_deleted: number;
}> {
  const response = await checked(await apiFetch(
    `/api/gardens/${gardenId}`,
    {
      method: "DELETE",
      headers: { "x-action-reason": actionReason },
    },
  ), `/api/gardens/${gardenId}`);
  return (await response.json()) as {
    status: string;
    garden_id: number;
    garden_name: string;
    plots_deleted: number;
    snapshots_deleted: number;
    plants_deleted: number;
  };
}

export async function getGardenMembershipsApi(
  gardenId: number,
): Promise<GardenMembership[]> {
  const body = await apiGet<{ memberships?: GardenMembership[] }>(
    `/api/gardens/${gardenId}/memberships`,
  );
  return Array.isArray(body.memberships) ? body.memberships : [];
}

export async function upsertGardenMembershipApi(
  gardenId: number,
  username: string,
  role: "viewer" | "editor" | "admin",
  actionReason = "ui-membership-upsert",
): Promise<GardenMembership> {
  return apiPost<GardenMembership>(
    `/api/gardens/${gardenId}/memberships`,
    { username, role, action_reason: actionReason },
  );
}

export async function deleteGardenMembershipApi(
  gardenId: number,
  userId: number,
  actionReason = "ui-membership-remove",
): Promise<void> {
  await checked(await apiFetch(
    `/api/gardens/${gardenId}/memberships/${userId}`,
    {
      method: "DELETE",
      headers: { "x-action-reason": actionReason },
    },
  ), `/api/gardens/${gardenId}/memberships/${userId}`);
}

export async function getGardenInvitationsApi(
  gardenId: number,
): Promise<GardenInvitation[]> {
  const body = await apiGet<{ invitations?: GardenInvitation[] }>(
    `/api/gardens/${gardenId}/invitations`,
  );
  return Array.isArray(body.invitations) ? body.invitations : [];
}

export async function createGardenInvitationApi(
  gardenId: number,
  inviteeUsername: string,
  role: "viewer" | "editor" | "admin",
  expiresInMinutes?: number,
  actionReason = "ui-invitation-create",
): Promise<{ invite_token: string; invitation: GardenInvitation }> {
  const body = await apiPost<{
    invite_token: string;
    invitation: GardenInvitation;
  }>(
    `/api/gardens/${gardenId}/invitations`,
    {
      invitee_username: inviteeUsername,
      role,
      expires_in_minutes: expiresInMinutes,
      action_reason: actionReason,
    },
  );
  return body;
}

export async function getUserInvitationsApi(): Promise<UserInvitation[]> {
  const body = await apiGet<{ invitations?: UserInvitation[] }>(
    "/api/auth/user-invitations",
  );
  return Array.isArray(body.invitations) ? body.invitations : [];
}

export async function createUserInvitationApi(
  inviteeUsername: string,
  role: "editor" | "admin",
  expiresInMinutes?: number,
  actionReason = "ui-user-invitation-create",
): Promise<{ invite_token: string; invitation: UserInvitation }> {
  return apiPost<{ invite_token: string; invitation: UserInvitation }>(
    "/api/auth/user-invitations",
    {
      invitee_username: inviteeUsername,
      role,
      expires_in_minutes: expiresInMinutes,
      action_reason: actionReason,
    },
  );
}

export async function revokeUserInvitationApi(
  invitationId: number,
  actionReason = "ui-user-invitation-revoke",
): Promise<number> {
  const response = await checked(await apiFetch(
    `/api/auth/user-invitations/${invitationId}`,
    {
      method: "DELETE",
      headers: { "x-action-reason": actionReason },
    },
  ), `/api/auth/user-invitations/${invitationId}`);
  const body = (await response.json()) as { revoked_at_ms?: number };
  return Number(body.revoked_at_ms ?? 0);
}

export async function revokeGardenInvitationApi(
  gardenId: number,
  invitationId: number,
  actionReason = "ui-invitation-revoke",
): Promise<number> {
  const response = await checked(await apiFetch(
    `/api/gardens/${gardenId}/invitations/${invitationId}`,
    {
      method: "DELETE",
      headers: { "x-action-reason": actionReason },
    },
  ), `/api/gardens/${gardenId}/invitations/${invitationId}`);
  const body = (await response.json()) as { revoked_at_ms?: number };
  return Number(body.revoked_at_ms ?? 0);
}

export async function acceptInvitationApi(
  token: string,
  password: string,
): Promise<{ garden_id: number | null; username: string; role: string; invitation_scope: string }> {
  return apiPost<{ garden_id: number | null; username: string; role: string; invitation_scope: string }>(
    "/api/auth/invitations/accept",
    { token, password },
  );
}

export async function getPasswordPolicyApi(): Promise<PasswordPolicy> {
  return apiGet<PasswordPolicy>("/api/auth/password-policy");
}

export async function peekInvitationApi(
  token: string,
): Promise<{ username: string }> {
  return apiPost<{ username: string }>(
    "/api/auth/invitations/peek",
    { token },
  );
}

export async function checkHibpApi(
  password: string,
): Promise<{ breached: boolean }> {
  return apiPost<{ breached: boolean }>(
    "/api/auth/check-hibp",
    { password },
  );
}

export async function getAuthUsersApi(): Promise<AuthManagedUser[]> {
  const body = await apiGet<{ users?: AuthManagedUser[] }>("/api/auth/users");
  return Array.isArray(body.users) ? body.users : [];
}

export async function createAuthUserApi(
  username: string,
  password: string,
  role: "viewer" | "editor" | "admin",
  mustChangePassword: boolean,
  actionReason = "ui-user-create",
): Promise<AuthManagedUser> {
  return apiPost<AuthManagedUser>("/api/auth/users", {
    username,
    password,
    role,
    must_change_password: mustChangePassword,
    action_reason: actionReason,
  });
}

export async function updateAuthUserApi(
  userId: number,
  patch: {
    role?: "viewer" | "editor" | "admin";
    is_active?: boolean;
    must_change_password?: boolean;
    deactivated_reason?: string;
    action_reason?: string;
  },
): Promise<AuthManagedUser> {
  return apiPatch<AuthManagedUser>(`/api/auth/users/${userId}`, patch);
}

export async function updateUserTierApi(
  userId: number,
  tier: string,
  actionReason = "ui-user-tier-update",
): Promise<{ status: string; subscription_tier: string }> {
  const response = await checked(
    await apiFetch(`/api/auth/users/${userId}/tier`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subscription_tier: tier, action_reason: actionReason }),
    }),
    `/api/auth/users/${userId}/tier`,
  );
  return response.json();
}

export async function deleteAuthUserApi(
  userId: number,
  actionReason = "ui-user-delete",
): Promise<AuthDeleteUserResult> {
  return await apiDelete<AuthDeleteUserResult>(
    `/api/auth/users/${userId}`,
    { headers: { "x-action-reason": actionReason } },
  );
}

export async function revokeUserSessionsByIdApi(
  userId: number,
  actionReason = "ui-user-revoke-sessions",
): Promise<number> {
  const body = await apiPost<{ revoked_sessions?: number }>(
    `/api/auth/users/${userId}/revoke-sessions`,
    { action_reason: actionReason },
  );
  return Number(body.revoked_sessions ?? 0);
}

export async function restartUserOnboardingApi(
  userId: number,
  actionReason = "ui-user-restart-onboarding",
): Promise<RestartUserOnboardingResult> {
  return apiPost<RestartUserOnboardingResult>(
    `/api/auth/users/${userId}/restart-onboarding`,
    { action_reason: actionReason },
  );
}

export async function issueUserResetTokenApi(
  userId: number,
  options: {
    expires_in_minutes?: number;
    must_change_password?: boolean;
    action_reason?: string;
  } = {},
): Promise<AuthResetIssueResult> {
  return apiPost<AuthResetIssueResult>(
    `/api/auth/users/${userId}/issue-reset`,
    options,
  );
}

export async function getAuthAuditEventsApi(params: AuditEventQuery = {}): Promise<AuditEventPage> {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  if (params.garden_id !== undefined) query.set("garden_id", String(params.garden_id));
  if (params.actor) query.set("actor", params.actor);
  if (params.path_prefix) query.set("path_prefix", params.path_prefix);
  if (params.method) query.set("method", params.method);
  if (params.status_code !== undefined) query.set("status_code", String(params.status_code));
  if (params.from_ms !== undefined) query.set("from_ms", String(params.from_ms));
  if (params.to_ms !== undefined) query.set("to_ms", String(params.to_ms));
  const body = await apiGet<AuditEventPage>(`/api/auth/audit-events?${query.toString()}`);
  return {
    events: Array.isArray(body.events) ? body.events : [],
    total: Number(body.total || 0),
    limit: Number(body.limit || (params.limit ?? 100)),
    offset: Number(body.offset || (params.offset ?? 0)),
  };
}

export async function getAuthSessionsApi(): Promise<ActiveSession[]> {
  const body = await apiGet<{ sessions?: ActiveSession[] }>("/api/auth/sessions");
  return Array.isArray(body.sessions) ? body.sessions : [];
}

export async function reauthenticateApi(
  currentPassword: string,
  options: {
    mfaCode?: string;
    recoveryCode?: string;
  } = {},
): Promise<{
  reauthenticated_at_ms: number;
  reauthenticated_until_ms: number;
}> {
  return apiPost<{
    reauthenticated_at_ms: number;
    reauthenticated_until_ms: number;
  }>("/api/auth/reauthenticate", {
    current_password: currentPassword,
    mfa_code: options.mfaCode ?? "",
    recovery_code: options.recoveryCode ?? "",
  }, { suppressAuthExpiry: true });
}

export async function revokeUserSessionsApi(
  username: string,
  actionReason = "",
): Promise<number> {
  const body = await apiPost<{ revoked: number }>("/api/auth/revoke-user-sessions", {
    username,
    action_reason: actionReason,
  });
  return Number(body.revoked || 0);
}

export async function revokeAllSessionsApi(actionReason = ""): Promise<number> {
  const body = await apiPost<{ revoked: number }>("/api/auth/revoke-all-sessions", {
    action_reason: actionReason,
  });
  return Number(body.revoked || 0);
}

export async function getEmergencyReadOnlyApi(): Promise<EmergencyReadOnlyStatus> {
  return apiGet<EmergencyReadOnlyStatus>("/api/auth/emergency-read-only");
}

export async function getAdminSystemHealthApi(): Promise<AdminSystemHealth> {
  return apiGet<AdminSystemHealth>("/api/admin/system/health");
}

export async function getSecurityMetricsApi(): Promise<SecurityMetrics> {
  return apiGet<SecurityMetrics>("/api/auth/security-metrics");
}

export async function getSecurityAlertsApi(): Promise<SecurityAlertsResponse> {
  return apiGet<SecurityAlertsResponse>("/api/auth/security-alerts");
}

export async function setEmergencyReadOnlyApi(
  enabled: boolean,
  options: {
    actionReason?: string;
    expiresInMinutes?: number;
  } = {},
): Promise<EmergencyReadOnlyStatus> {
  const payload: {
    enabled: boolean;
    action_reason: string;
    expires_in_minutes?: number;
  } = {
    enabled,
    action_reason: options.actionReason ?? "",
  };
  if (enabled && options.expiresInMinutes !== undefined) {
    payload.expires_in_minutes = options.expiresInMinutes;
  }
  return apiPatch<EmergencyReadOnlyStatus>("/api/auth/emergency-read-only", payload);
}

export async function getPlots(): Promise<Plot[]> {
  return apiGet<Plot[]>("/api/plots");
}

export interface HouseLayoutState {
  row: number;
  col: number;
  width: number;
  height: number;
  north_degrees: number;
  grid_rows: number;
  grid_cols: number;
}

export interface LayoutExportPlot {
  plot_id: string;
  zone_code: string;
  zone_name: string;
  plot_number: number;
  grid_row: number;
  grid_col: number;
  sub_zone?: string;
  notes?: string;
  color?: string | null;
}

export interface LayoutExport {
  plots: LayoutExportPlot[];
  house?: HouseLayoutState;
  shademap?: PersistedShadeMapState;
  shademap_calibration?: ShadeMapCalibration;
  shademap_obstacles?: ShadeMapObstacle[];
  map_objects?: MapObject[];
}

export type ShadeMapMode = "shadow" | "sun-hours";
export type ShadeMapPreset = "now" | "custom" | "spring" | "summer" | "autumn" | "winter";
export type ShadeMapFeature = Record<string, unknown>;

export interface ShadeMapConfig {
  api_key: string;
  latitude: number;
  longitude: number;
  zoom: number;
  label: string;
  share_url: string;
  terrain_url_template: string;
  terrain_token_expires_at_ms: number;
  terrain_max_zoom: number;
  terrain_tile_size: number;
  features_min_zoom: number;
}

export interface PersistedShadeMapState {
  mode: ShadeMapMode;
  selected_plot_id: string | null;
  analysis_timestamp_ms: number;
  preset: ShadeMapPreset;
}

export interface ShadeMapCalibration {
  enabled: boolean;
  calibration_type: "two-point" | "house-corners";
  origin_grid_col: number | null;
  origin_grid_row: number | null;
  origin_latitude: number | null;
  origin_longitude: number | null;
  axis_grid_col: number | null;
  axis_grid_row: number | null;
  axis_latitude: number | null;
  axis_longitude: number | null;
  house_nw_latitude: number | null;
  house_nw_longitude: number | null;
  house_ne_latitude: number | null;
  house_ne_longitude: number | null;
  house_se_latitude: number | null;
  house_se_longitude: number | null;
  house_sw_latitude: number | null;
  house_sw_longitude: number | null;
}

export type ShadeMapObstacleKind = "tree" | "structure";

export interface ShadeMapObstacle {
  id: number;
  label: string;
  kind: ShadeMapObstacleKind;
  linked_plot_id: string | null;
  latitude: number;
  longitude: number;
  height_m: number;
  crown_radius_m: number;
  active: boolean;
}

export type ShadeMapObstacleInput = Omit<ShadeMapObstacle, "id">;

export interface ShadeMapMonthlyEstimateValue {
  month: number;
  month_label: string;
  hours: number;
  sample_days: number;
}

export interface ShadeMapMonthlyEstimate {
  source_name: string;
  source_date_start: string;
  source_date_end: string;
  values: ShadeMapMonthlyEstimateValue[];
}

export async function getLayoutStateApi(): Promise<HouseLayoutState> {
  return apiGet<HouseLayoutState>("/api/layout-state");
}

export async function getShadeMapConfigApi(): Promise<ShadeMapConfig> {
  return apiGet<ShadeMapConfig>("/api/shademap/config");
}

export async function getShadeMapFeaturesApi(params: {
  north: number;
  south: number;
  east: number;
  west: number;
  zoom: number;
}): Promise<ShadeMapFeature[]> {
  const query = new URLSearchParams({
    north: String(params.north),
    south: String(params.south),
    east: String(params.east),
    west: String(params.west),
    zoom: String(params.zoom),
  });
  const body = await apiGet<{ features?: ShadeMapFeature[] }>(
    `/api/shademap/features?${query.toString()}`,
  );
  return Array.isArray(body.features) ? body.features : [];
}

export function buildShadeMapTerrainUrl(
  template: string,
  params: { z: number; x: number; y: number },
): string {
  return template
    .replace("{z}", String(params.z))
    .replace("{x}", String(params.x))
    .replace("{y}", String(params.y));
}

export async function getShadeMapStateApi(): Promise<PersistedShadeMapState> {
  return apiGet<PersistedShadeMapState>("/api/shademap/state");
}

export async function getShadeMapCalibrationApi(): Promise<ShadeMapCalibration> {
  return apiGet<ShadeMapCalibration>("/api/shademap/calibration");
}

export async function updateShadeMapCalibrationApi(
  calibration: ShadeMapCalibration,
): Promise<ShadeMapCalibration> {
  return apiPatch<ShadeMapCalibration>(
    "/api/shademap/calibration",
    calibration,
  );
}

export async function listShadeMapObstaclesApi(): Promise<ShadeMapObstacle[]> {
  return apiGet<ShadeMapObstacle[]>("/api/shademap/obstacles");
}

export async function createShadeMapObstacleApi(
  obstacle: ShadeMapObstacleInput,
): Promise<ShadeMapObstacle> {
  return apiPost<ShadeMapObstacle>("/api/shademap/obstacles", obstacle);
}

export async function updateShadeMapObstacleApi(
  obstacleId: number,
  obstacle: ShadeMapObstacleInput,
): Promise<ShadeMapObstacle> {
  return apiPatch<ShadeMapObstacle>(
    `/api/shademap/obstacles/${obstacleId}`,
    obstacle,
  );
}

export async function deleteShadeMapObstacleApi(
  obstacleId: number,
): Promise<void> {
  await apiDelete<unknown>(`/api/shademap/obstacles/${obstacleId}`);
}

export async function updateShadeMapStateApi(
  state: PersistedShadeMapState,
): Promise<void> {
  await apiPatch<unknown>("/api/shademap/state", state);
}

export async function getShadeMapMonthlyEstimatedSunApi(): Promise<ShadeMapMonthlyEstimate> {
  return apiGet<ShadeMapMonthlyEstimate>(
    "/api/shademap/monthly-estimated-sun",
  );
}

export interface SunWindow {
  sol_opp: string | null;
  sol_ned: string | null;
}

export async function getSunWindowApi(
  month: number,
  day: number,
): Promise<SunWindow> {
  const query = new URLSearchParams({
    month: String(month),
    day: String(day),
  });
  return apiGet<SunWindow>(
    `/api/shademap/sun-window?${query.toString()}`,
  );
}

export async function updateLayoutStateApi(
  house: HouseLayoutState,
): Promise<HouseLayoutState> {
  return apiPatch<HouseLayoutState>("/api/layout-state", house);
}

export async function listMapObjectsApi(gardenId: number): Promise<MapObject[]> {
  const body = await apiGet<{ objects: MapObject[] }>(
    `/api/gardens/${gardenId}/map-objects`,
  );
  return body.objects;
}

export async function createMapObjectApi(
  gardenId: number,
  object: MapObjectInput,
): Promise<MapObject> {
  return apiPost<MapObject>(`/api/gardens/${gardenId}/map-objects`, object);
}

export async function updateMapObjectApi(
  gardenId: number,
  publicId: string,
  object: Partial<MapObjectInput>,
): Promise<MapObject> {
  return apiPatch<MapObject>(`/api/gardens/${gardenId}/map-objects/${publicId}`, object);
}

export async function deleteMapObjectApi(
  gardenId: number,
  publicId: string,
): Promise<void> {
  await apiDelete<unknown>(`/api/gardens/${gardenId}/map-objects/${publicId}`);
}

export async function createMapObjectUnitApi(
  gardenId: number,
  objectPublicId: string,
  unit: MapObjectUnitInput,
): Promise<MapObjectUnit> {
  return apiPost<MapObjectUnit>(
    `/api/gardens/${gardenId}/map-objects/${objectPublicId}/units`,
    unit,
  );
}

export async function updateMapObjectUnitApi(
  gardenId: number,
  objectPublicId: string,
  unitPublicId: string,
  unit: Partial<MapObjectUnitInput>,
): Promise<MapObjectUnit> {
  return apiPatch<MapObjectUnit>(
    `/api/gardens/${gardenId}/map-objects/${objectPublicId}/units/${unitPublicId}`,
    unit,
  );
}

export async function deleteMapObjectUnitApi(
  gardenId: number,
  objectPublicId: string,
  unitPublicId: string,
): Promise<void> {
  await apiDelete<unknown>(
    `/api/gardens/${gardenId}/map-objects/${objectPublicId}/units/${unitPublicId}`,
  );
}

export async function getPlants(
  q = "",
  category = "",
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<Plant[]> {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (category) params.set("category", category);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return apiGet<Plant[]>(`/api/plants${suffix}`, options);
}

export interface PlantSearchResult {
  plt_id: string;
  name: string;
  latin: string;
  category: string;
  plot_ids?: string[];
  quantity?: number;
}

export async function searchPlantsApi(
  q: string,
  options?: {
    limit?: number;
    includeAssignments?: boolean;
    gardenId?: number | null;
  },
): Promise<PlantSearchResult[]> {
  const params = new URLSearchParams();
  params.set("q", q);
  if (options?.limit !== undefined) {
    params.set("limit", String(options.limit));
  }
  if (options?.includeAssignments) {
    params.set("include_assignments", "true");
  }
  return apiGet<PlantSearchResult[]>(
    `/api/plants/search?${params.toString()}`,
    options?.gardenId !== undefined ? { gardenId: options.gardenId } : undefined,
  );
}

export async function getPlotPlants(plotId: string): Promise<Plant[]> {
  return apiGet<Plant[]>(`/api/plots/${encodeApiPathSegment(plotId)}/plants`);
}

export interface PlotPlantAlerts {
  plant_alerts: Record<string, string[]>;
}

export async function getPlotPlantAlerts(
  plotId: string,
): Promise<PlotPlantAlerts> {
  return apiGet<PlotPlantAlerts>(
    `/api/plots/${encodeApiPathSegment(plotId)}/plant-alerts`,
  );
}

export interface AddPlantToPlotResult {
  status: string;
  plot_id: string;
  plt_id: string;
  quantity: number;
  companion_warnings?: Array<{ description: string }>;
}

export async function addPlantToPlotApi(
  plotId: string,
  pltId: string,
  quantity = 1,
  roomLabel?: string | null,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<AddPlantToPlotResult> {
  return apiPost<AddPlantToPlotResult>(
    `/api/plots/${encodeApiPathSegment(plotId)}/plants/${encodeApiPathSegment(pltId)}`,
    { quantity, ...(roomLabel != null ? { room_label: roomLabel } : {}) },
    options,
  );
}

export async function removePlantFromPlotApi(
  plotId: string,
  pltId: string,
): Promise<void> {
  await apiDelete<unknown>(
    `/api/plots/${encodeApiPathSegment(plotId)}/plants/${encodeApiPathSegment(pltId)}`,
  );
}

export async function getRoomLabels(plotId: string): Promise<string[]> {
  return apiGet<string[]>(`/api/plots/${encodeApiPathSegment(plotId)}/room-labels`);
}

export async function updatePlotPlant(
  plotId: string,
  pltId: string,
  quantity: number,
  roomLabel?: string | null,
): Promise<{ status: string }> {
  return apiPatch<{ status: string }>(
    `/api/plots/${encodeApiPathSegment(plotId)}/plants/${encodeApiPathSegment(pltId)}`,
    { quantity, ...(roomLabel != null ? { room_label: roomLabel } : {}) },
  );
}

export async function deletePlotApi(plotId: string): Promise<void> {
  await apiDelete<unknown>(`/api/plots/${encodeApiPathSegment(plotId)}`);
}

export interface PlotDeleteImpact {
  plot_id: string;
  counts: Record<string, number>;
  total_dependent_references: number;
  has_dependents: boolean;
}

export async function getPlotDeleteImpactApi(plotId: string): Promise<PlotDeleteImpact> {
  return apiGet<PlotDeleteImpact>(
    `/api/plots/${encodeApiPathSegment(plotId)}/delete-impact`,
  );
}

export async function batchMovePlotsApi(
  moves: Array<{ plot_id: string; grid_row: number; grid_col: number }>,
): Promise<void> {
  await apiPost<unknown>("/api/plots/batch-move", { moves });
}

export async function createPlotApi(
  data: Record<string, string | number>,
): Promise<void> {
  await apiPost<unknown>("/api/plots", data);
}

export async function updatePlotApi(
  plotId: string,
  fields: Record<string, string | number | null>,
): Promise<void> {
  await apiPatch<unknown>(`/api/plots/${encodeApiPathSegment(plotId)}`, fields);
}

export async function movePlantBetweenPlotsApi(
  fromPlotId: string,
  toPlotId: string,
  pltId: string,
): Promise<void> {
  await apiPost<unknown>(
    `/api/plots/${encodeApiPathSegment(fromPlotId)}/plants/${encodeApiPathSegment(
      pltId,
    )}/move/${encodeApiPathSegment(toPlotId)}`,
    {},
  );
}

export async function getPlantPlots(
  pltId: string,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<string[]> {
  return apiGet<string[]>(
    `/api/plants/${encodeApiPathSegment(pltId)}/plots`,
    options,
  );
}

export async function updatePlantApi(
  pltId: string,
  fields: Record<string, string | number | boolean | null>,
): Promise<void> {
  await apiPatch<unknown>(`/api/plants/${encodeApiPathSegment(pltId)}`, fields);
}

export async function getPlantApi(
  pltId: string,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<Plant> {
  return apiGet<Plant>(
    `/api/plants/${encodeApiPathSegment(pltId)}/details`,
    options,
  );
}

export async function getNextPlantIdApi(): Promise<string> {
  const res = await apiGet<{ next_id: string }>("/api/plants/next-id");
  return res.next_id;
}

export async function createPlantApi(
  data: Record<string, string | number | boolean | null>,
): Promise<void> {
  await apiPost<unknown>("/api/plants", data);
}

export async function deletePlantApi(pltId: string): Promise<void> {
  await apiDelete<unknown>(`/api/plants/${encodeApiPathSegment(pltId)}`);
}

export interface CatalogPlant {
  name: string;
  latin: string;
  category: string;
  bloom_month: string;
  color: string;
  hardiness: string;
  height_cm: number | null;
  light: string;
  link?: string | undefined;
}

export async function searchPlantCatalog(
  q: string,
): Promise<CatalogPlant[]> {
  return apiGet<CatalogPlant[]>(
    `/api/external-plants?q=${encodeURIComponent(q)}`,
  );
}

export interface Snapshot {
  id: string;
  name: string;
  created_at: string;
}

export async function saveSnapshotApi(name: string): Promise<void> {
  await apiPost<unknown>("/api/snapshots", { name });
}

export async function listSnapshotsApi(): Promise<Snapshot[]> {
  return apiGet<Snapshot[]>("/api/snapshots");
}

export async function restoreSnapshotApi(
  id: string,
  actionReason = "ui-snapshot-restore",
): Promise<void> {
  await apiPost<unknown>(
    `/api/snapshots/${id}/restore`,
    { action_reason: actionReason },
  );
}

export async function deleteSnapshotApi(
  id: string,
  actionReason = "ui-snapshot-delete",
): Promise<void> {
  await apiDelete<unknown>(
    `/api/snapshots/${id}`,
    { headers: { "x-action-reason": actionReason } },
  );
}

export async function exportMapApi(): Promise<Blob> {
  const response = await checked(await apiFetch("/api/plots/export"), "/api/plots/export");
  return await response.blob();
}

export async function importMapApi(
  layout: LayoutExport,
  options: {
    actionReason?: string;
  } = {},
): Promise<void> {
  await checked(await apiFetch("/api/plots/import", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(options.actionReason ? { "x-action-reason": options.actionReason } : {}),
    },
    body: JSON.stringify(layout),
  }), "/api/plots/import");
}

export async function exportPlantsCsvApi(): Promise<Blob> {
  const response = await checked(
    await apiFetch("/api/plants/export-csv"),
    "/api/plants/export-csv",
  );
  return await response.blob();
}

export async function importPlantsCsvApi(csvText: string): Promise<{
  rows: number;
  created: number;
  updated: number;
}> {
  return apiPost<{ rows: number; created: number; updated: number }>(
    "/api/plants/import-csv",
    { csv_text: csvText },
  );
}

export interface PlotElevations {
  available: boolean;
  elevations: Record<string, number>;
  overrides: Record<string, number>;
  min_m: number | null;
  max_m: number | null;
}

export async function getPlotElevationsApi(): Promise<PlotElevations> {
  return apiGet<PlotElevations>("/api/plots/elevations");
}

export async function updatePlotElevationsApi(
  overrides: Record<string, number | null>,
): Promise<PlotElevations> {
  return apiPatch<PlotElevations>(
    "/api/plots/elevations",
    { overrides },
  );
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export async function gardenChatApi(
  message: string,
  history: ChatMessage[],
): Promise<string> {
  const data = await apiPost<{ reply: string }>(
    "/api/ai/garden-chat",
    { message, history },
    { timeoutMs: AI_CHAT_TIMEOUT_MS, timeoutMessage: AI_CHAT_TIMEOUT_MESSAGE },
  );
  return data.reply;
}

export interface AiPlantResult {
  name: string;
  latin: string;
  category: string;
  bloom_month: string;
  color: string;
  hardiness: string;
  height_cm: number;
  light: string;
  link?: string;
}

export async function aiPlantLookup(
  query: string,
): Promise<AiPlantResult> {
  return apiPost<AiPlantResult>(
    "/api/ai/plant-lookup",
    { query },
  );
}

export interface GeneratedCareResult {
  status: "ok" | "partial";
  generated: number;
  missing_before: number;
  remaining_without_care: number;
  updated_plant_ids: string[];
  attempted: number;
  has_more: boolean;
  next_cursor?: string | null;
  error?: string;
}

export async function generateMissingCareInstructionsApi(
  options?: {
    maxPlants?: number;
    regenerate?: boolean;
    timeoutMs?: number;
    gardenId?: number | null;
    cursor?: string;
  },
): Promise<GeneratedCareResult> {
  const body: {
    max_plants?: number;
    regenerate?: boolean;
    cursor?: string;
  } = {};
  if (options?.maxPlants !== undefined) {
    body.max_plants = options.maxPlants;
  }
  if (options?.regenerate) {
    body.regenerate = true;
  }
  if (options?.cursor) {
    body.cursor = options.cursor;
  }
  return apiPost<GeneratedCareResult>(
    "/api/ai/generate-missing-care",
    body,
    {
      timeoutMs: options?.timeoutMs ?? 120_000,
      ...(options?.gardenId !== undefined ? { gardenId: options.gardenId } : {}),
    },
  );
}

// ── Garden settings & onboarding ────────────────────────────

export async function getGardenSettingsApi(
  gardenId: number,
): Promise<GardenSettings> {
  return apiGet<GardenSettings>(`/api/gardens/${gardenId}/settings`);
}

export async function updateGardenSettingsApi(
  gardenId: number,
  settings: Partial<Omit<GardenSettings, "garden_id">>,
): Promise<GardenSettings> {
  return apiPatch<GardenSettings>(
    `/api/gardens/${gardenId}/settings`,
    settings,
  );
}

export async function geocodeGardenLocationApi(
  gardenId: number,
  query: string,
): Promise<GardenGeocodeResult[]> {
  const params = new URLSearchParams({ q: query });
  const response = await apiGet<{ results?: GardenGeocodeResult[] }>(
    `/api/gardens/${gardenId}/geocode?${params.toString()}`,
  );
  return Array.isArray(response.results) ? response.results : [];
}

export async function getGardenLidarApi(
  gardenId: number,
): Promise<GardenLidarStatus> {
  return apiGet<GardenLidarStatus>(`/api/gardens/${gardenId}/lidar`);
}

export async function uploadGardenLidarApi(options: {
  gardenId: number;
  file: File;
  onProgress?: (pct: number) => void;
}): Promise<GardenLidarStatus> {
  const uploadOptions: { onProgress?: (pct: number) => void; gardenId: number } = {
    gardenId: options.gardenId,
  };
  if (options.onProgress) uploadOptions.onProgress = options.onProgress;
  return uploadBinary<GardenLidarStatus>(
    `/api/gardens/${options.gardenId}/lidar`,
    options.file,
    {
      "content-type": options.file.type || "application/octet-stream",
      "x-upload-filename": options.file.name || "terrain.laz",
    },
    uploadOptions,
  );
}

export async function deleteGardenLidarApi(
  gardenId: number,
): Promise<GardenLidarStatus> {
  return apiDelete<GardenLidarStatus>(`/api/gardens/${gardenId}/lidar`);
}

export async function createZoneApi(
  gardenId: number,
  zone: {
    zone_code: string;
    zone_name: string;
    start_row: number;
    start_col: number;
    end_row: number;
    end_col: number;
    color?: string;
  },
): Promise<ZoneCreateResult> {
  return apiPost<ZoneCreateResult>(
    `/api/gardens/${gardenId}/zones`,
    zone,
  );
}

export async function completeGardenOnboardingApi(
  gardenId: number,
  body: {
    name: string;
    grid_rows: number;
    grid_cols: number;
    latitude: number | null;
    longitude: number | null;
    address: string;
    mode: "manual" | "import";
    house?: HouseLayoutState;
    zones?: Array<{
      zone_code: string;
      zone_name: string;
      start_row: number;
      start_col: number;
      end_row: number;
      end_col: number;
      color?: string;
    }>;
    imported_layout?: LayoutExport;
  },
): Promise<CompleteOnboardingResult> {
  return apiPost<CompleteOnboardingResult>(
    `/api/gardens/${gardenId}/complete-onboarding`,
    body,
  );
}

// ── Journal ─────────────────────────────────────────────────────────

export interface JournalFilterParams {
  event_type?: string;
  plant_id?: string;
  plot_id?: string;
  q?: string;
  date_from?: string;
  date_to?: string;
  actor?: string;
  actor_user_id?: number;
  limit?: number;
  offset?: number;
}

export async function fetchJournalEntriesApi(
  params?: JournalFilterParams,
): Promise<JournalListResponse> {
  const qs = new URLSearchParams();
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    }
  }
  const query = qs.toString();
  return apiGet<JournalListResponse>(
    `/api/journal${query ? `?${query}` : ""}`,
  );
}

export async function fetchJournalEntryApi(
  entryId: string,
): Promise<JournalEntry> {
  return apiGet<JournalEntry>(`/api/journal/${entryId}`);
}

export async function createJournalEntryApi(data: {
  event_type: string;
  occurred_on: string;
  title?: string;
  notes?: string;
  metadata?: Record<string, unknown>;
  plant_ids?: string[];
  plot_ids?: string[];
}, options?: Pick<ApiRequestOptions, "gardenId" | "operationId">): Promise<{ status: string; id: string }> {
  return apiPost<{ status: string; id: string }>("/api/journal", data, options);
}

export async function updateJournalEntryApi(
  entryId: string,
  fields: Record<string, unknown>,
): Promise<{ status: string }> {
  return apiPatch<{ status: string }>(`/api/journal/${entryId}`, fields);
}

export async function deleteJournalEntryApi(
  entryId: string,
): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/api/journal/${entryId}`);
}

export async function listMediaApi(params?: {
  target_type?: MediaTargetType;
  target_id?: string;
  limit?: number;
  offset?: number;
}): Promise<MediaListResponse> {
  const qs = new URLSearchParams();
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") {
        qs.set(key, String(value));
      }
    }
  }
  const query = qs.toString();
  return apiGet<MediaListResponse>(`/api/media${query ? `?${query}` : ""}`);
}

export async function listMediaSummariesApi(options: {
  targetType: MediaTargetType;
  targetIds: Array<string | number>;
  gardenId?: number | null;
}): Promise<MediaSummariesResponse> {
  const requestOptions: Pick<ApiRequestOptions, "gardenId"> | undefined =
    options.gardenId !== undefined ? { gardenId: options.gardenId } : undefined;
  return apiPost<MediaSummariesResponse>("/api/media/summaries", {
    target_type: options.targetType,
    target_ids: options.targetIds.map((targetId) => String(targetId)),
  }, requestOptions);
}

export async function uploadMediaApi(options: {
  targetType: MediaTargetType;
  targetId: string | number;
  file: File;
  onProgress?: (pct: number) => void;
  gardenId?: number | null;
  operationId?: string;
}): Promise<MediaAsset> {
  const qs = new URLSearchParams({
    target_type: options.targetType,
    target_id: String(options.targetId),
  });
  const uploadOptions: {
    onProgress?: (pct: number) => void;
    gardenId?: number | null;
    operationId?: string;
  } = {};
  if (options.onProgress) {
    uploadOptions.onProgress = options.onProgress;
  }
  if ("gardenId" in options) {
    uploadOptions.gardenId = options.gardenId ?? null;
  }
  if (options.operationId) {
    uploadOptions.operationId = options.operationId;
  }
  return uploadBinary<MediaAsset>(
    `/api/media/upload?${qs.toString()}`,
    options.file,
    {
      "Content-Type": options.file.type || "application/octet-stream",
      "x-upload-filename": options.file.name || "upload",
    },
    uploadOptions,
  );
}

export async function deleteMediaAssetApi(assetId: string): Promise<{ status: string; asset_id: string }> {
  return apiDelete<{ status: string; asset_id: string }>(`/api/media/${encodeURIComponent(assetId)}`);
}

export async function removeMediaLinkApi(options: {
  assetId: string;
  targetType: MediaTargetType;
  targetId: string | number;
}): Promise<{
  status: string;
  asset_id: string;
  target_type: MediaTargetType;
  target_id: string;
  deleted_asset: boolean;
}> {
  const qs = new URLSearchParams({
    target_type: options.targetType,
    target_id: String(options.targetId),
  });
  return apiDelete<{
    status: string;
    asset_id: string;
    target_type: MediaTargetType;
    target_id: string;
    deleted_asset: boolean;
  }>(`/api/media/${encodeURIComponent(options.assetId)}/links?${qs.toString()}`);
}

export async function addMediaLinkApi(options: {
  assetId: string;
  targetType: MediaTargetType;
  targetId: string | number;
  gardenId?: number | null;
}): Promise<MediaAsset> {
  const requestOptions: Pick<ApiRequestOptions, "gardenId"> | undefined =
    options.gardenId !== undefined ? { gardenId: options.gardenId } : undefined;
  return apiPost<MediaAsset>(
    `/api/media/${encodeURIComponent(options.assetId)}/links`,
    {
      target_type: options.targetType,
      target_id: String(options.targetId),
    },
    requestOptions,
  );
}

export async function setPlantCoverApi(
  plantId: string,
  assetId: string,
): Promise<{ status: string; plant_id: string; asset: MediaAsset }> {
  return apiPost<{ status: string; plant_id: string; asset: MediaAsset }>(
    `/api/media/plants/${encodeURIComponent(plantId)}/cover`,
    { asset_id: assetId },
  );
}

export async function populateMissingPlantCoversApi(options?: {
  cursor?: string | null;
  maxPlants?: number;
  timeoutMs?: number;
  actionReason?: string;
}): Promise<PopulatePlantCoversResult> {
  const body: { cursor?: string; max_plants?: number; action_reason?: string } = {};
  if (options?.cursor) body.cursor = options.cursor;
  if (options?.maxPlants !== undefined) body.max_plants = options.maxPlants;
  if (options?.actionReason) body.action_reason = options.actionReason;
  return apiPost<PopulatePlantCoversResult>(
    "/api/media/plants/populate-missing-covers",
    body,
    { timeoutMs: options?.timeoutMs ?? 60_000 },
  );
}

export async function getMissingPlantCoversApi(options?: {
  limit?: number;
  offset?: number;
}): Promise<MissingPlantCoversResponse> {
  const qs = new URLSearchParams();
  if (options?.limit !== undefined) qs.set("limit", String(options.limit));
  if (options?.offset !== undefined) qs.set("offset", String(options.offset));
  const query = qs.toString();
  return apiGet<MissingPlantCoversResponse>(
    `/api/media/plants/missing-covers${query ? `?${query}` : ""}`,
  );
}

// ── Batch actions ─────────────────────────────────────────

export async function batchUpdatePlantsApi(
  pltIds: string[],
  updates: Record<string, unknown>,
  plotAction?: { plot_ids: string[]; action: "assign" | "remove" },
  options?: { care_note_append?: string },
): Promise<{ status: string; updated: number }> {
  const body: Record<string, unknown> = {
    plt_ids: pltIds,
    updates,
  };
  if (plotAction) {
    body["plot_ids"] = plotAction.plot_ids;
    body["plot_action"] = plotAction.action;
  }
  if (options?.care_note_append) {
    body["care_note_append"] = options.care_note_append;
  }
  return apiPost<{ status: string; updated: number }>(
    "/api/plants/batch-update",
    body,
  );
}

export async function batchJournalEntryApi(data: {
  plt_ids: string[];
  event_type: string;
  occurred_on: string;
  title?: string;
  notes?: string;
  plot_ids?: string[];
}): Promise<{ status: string; id: string }> {
  return apiPost<{ status: string; id: string }>(
    "/api/plants/batch-journal-entry",
    data,
  );
}

// ── Statistics actions ────────────────────────────────────

export interface StatisticsActions {
  unassigned_plants: Array<{ plt_id: string; name: string }>;
  empty_plots_by_zone: Array<{
    zone_code: string;
    plot_ids: string[];
    count: number;
  }>;
  bloom_gap_months: number[];
  no_year_plants: Array<{ plt_id: string; name: string }>;
  stale_plants: Array<{ plt_id: string; name: string }>;
  missing_care_plants: Array<{
    plt_id: string;
    name: string;
    missing: string[];
  }>;
}

export async function getStatisticsActionsApi(
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<StatisticsActions> {
  return apiGet<StatisticsActions>("/api/statistics/actions", options);
}

export interface AutomationStatusTask {
  rule_source: string;
  task_type: string;
  title: string;
  status: string;
  created_at_ms: number;
}

export interface AutomationStatus {
  automated_tasks: AutomationStatusTask[];
  total: number;
}

export async function getAutomationStatusApi(
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<AutomationStatus> {
  return apiGet<AutomationStatus>("/api/statistics/automation-status", options);
}

export interface PlotAlerts {
  task_plots: string[];
  issue_plots: string[];
  frost_plots: string[];
}

export async function fetchPlotAlertsApi(): Promise<PlotAlerts> {
  return apiGet<PlotAlerts>("/api/plots/alerts");
}

export interface BadgeCounts {
  overdue_tasks: number;
  open_issues: number;
  active_alerts: number;
  unread_notifications: number;
}

const BADGE_COUNTS_CACHE_MS = 5_000;
let badgeCountsCache: BadgeCounts | null = null;
let badgeCountsCacheExpiresAt = 0;
let badgeCountsRequest: Promise<BadgeCounts> | null = null;

function storeBadgeCounts(result: BadgeCounts): BadgeCounts {
  badgeCountsCache = result;
  badgeCountsCacheExpiresAt = Date.now() + BADGE_COUNTS_CACHE_MS;
  return result;
}

export async function fetchBadgeCountsApi(options?: {
  force?: boolean;
}): Promise<BadgeCounts> {
  const force = options?.force ?? false;
  if (!force && badgeCountsCache && Date.now() < badgeCountsCacheExpiresAt) {
    return badgeCountsCache;
  }
  if (!force && badgeCountsRequest) {
    return badgeCountsRequest;
  }
  const request = apiGet<BadgeCounts>("/api/dashboard/badge-counts")
    .then((result) => storeBadgeCounts(result))
    .finally(() => {
      if (badgeCountsRequest === request) {
        badgeCountsRequest = null;
      }
    });
  badgeCountsRequest = request;
  return request;
}

export interface TodayTask {
  id: string;
  task_type: string;
  title: string;
  severity: string;
  due_on: string;
}

export interface TodayIssue {
  id: string;
  issue_type: string;
  title: string;
  severity: string;
  status: string;
}

export interface TodayWeatherAlert {
  id: number;
  alert_type: string;
  severity: string;
  title: string;
}

export interface TodayForecast {
  date: string;
  temp_min?: number;
  temp_max?: number;
  precipitation?: number;
  symbol?: string;
  [key: string]: unknown;
}

export interface TodayDashboard {
  date: string;
  tasks_due_today: TodayTask[];
  tasks_due_today_total?: number;
  tasks_overdue: TodayTask[];
  tasks_overdue_total?: number;
  tasks_upcoming: TodayTask[];
  tasks_upcoming_total?: number;
  active_issues: TodayIssue[];
  active_issues_total?: number;
  weather_alerts: TodayWeatherAlert[];
  weather_alerts_total?: number;
  forecast_today: TodayForecast | null;
}

export async function fetchTodayDashboardApi(
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<TodayDashboard> {
  return apiGet<TodayDashboard>("/api/dashboard/today", options);
}

export interface GardenerReportZone {
  zone_code: string;
  zone_name: string;
  plot_count: number;
}

export interface GardenerReportPlantPreview {
  plt_id: string;
  name: string;
}

export interface GardenerReportPlotPreview {
  plot_id: string;
  zone_code: string;
  zone_name: string;
}

export interface GardenerReportProducerUnit {
  unit: string;
  total_qty: number;
}

export interface GardenerReportTopProducer {
  plt_id: string;
  name: string;
  entries: number;
  units: GardenerReportProducerUnit[];
}

export interface GardenerReports {
  zone_code: string | null;
  zone_name: string | null;
  available_zones: GardenerReportZone[];
  needs_attention: {
    overdue_tasks_count: number;
    overdue_task_ids: string[];
    due_this_week_count: number;
    due_this_week_task_ids: string[];
    open_issues_count: number;
    open_issue_ids: string[];
    overdue_follow_ups_count: number;
    overdue_follow_up_issue_ids: string[];
    active_weather_alerts_count: number;
    weather_alert_titles: string[];
  };
  bloom_now: {
    month: number;
    count: number;
    plant_ids: string[];
    plants: GardenerReportPlantPreview[];
  };
  bloom_next: {
    month: number;
    count: number;
    plant_ids: string[];
    plants: GardenerReportPlantPreview[];
  };
  missing_observations: {
    threshold_months: number;
    count: number;
    plant_ids: string[];
    plants: GardenerReportPlantPreview[];
  };
  plot_use: {
    total_plots: number;
    empty_count: number;
    empty_plot_ids: string[];
    empty_plots: GardenerReportPlotPreview[];
    underused_count: number;
    underused_plot_ids: string[];
    underused_plots: GardenerReportPlotPreview[];
  };
  data_quality: {
    missing_care_count: number;
    missing_care_plant_ids: string[];
    missing_care_plants: GardenerReportPlantPreview[];
    missing_year_count: number;
    missing_year_plant_ids: string[];
    missing_year_plants: GardenerReportPlantPreview[];
    missing_cover_count: number;
    missing_cover_plant_ids: string[];
    missing_cover_plants: GardenerReportPlantPreview[];
  };
  yield_summary: {
    year: number;
    total_entries: number;
    harvested_plot_count: number;
    active_month_count: number;
    best_month: number | null;
    best_month_entries: number;
    top_producers: GardenerReportTopProducer[];
  };
}

export async function fetchGardenerReportsApi(
  params?: Record<string, string | number>,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<GardenerReports> {
  const search = new URLSearchParams();
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value === "" || value === null || value === undefined) return;
    search.set(key, String(value));
  });
  const suffix = search.size > 0 ? `?${search.toString()}` : "";
  return apiGet<GardenerReports>(`/api/statistics/reports${suffix}`, options);
}

// ── Inventory ─────────────────────────────────────────────

export type InventoryType =
  | "seed"
  | "bulb"
  | "tuber"
  | "division"
  | "bare_root"
  | "nursery"
  | "cutting"
  | "other";

export type TransactionReason =
  | "purchased"
  | "harvested"
  | "sowed"
  | "planted"
  | "divided"
  | "gifted"
  | "disposed"
  | "adjusted"
  | "";

export interface InventoryProcurementHistoryEntry {
  id: string;
  label: string;
  vendor_name: string;
  vendor_url: string;
  status: string;
  quantity: number;
  unit: string;
  cost_minor: number;
  currency: string;
  ordered_on: string | null;
  expected_on: string | null;
  received_on: string | null;
  updated_at_ms: number;
}

export interface InventoryItem {
  id: string;
  garden_id: number;
  plt_id: string | null;
  label: string;
  inventory_type: InventoryType;
  unit: string;
  quantity: number;
  created_at_ms: number;
  procurement_history: InventoryProcurementHistoryEntry[];
}

export interface InventoryTransaction {
  id: number;
  item_id: string;
  delta: number;
  reason: TransactionReason;
  source_name: string;
  cost_minor: number | null;
  occurred_on: string;
  storage_location: string;
  notes: string;
  actor_user_id: number | null;
  actor_username: string | null;
  journal_entry_id: string | null;
  created_at_ms: number;
}

export interface InventoryListResponse {
  items: InventoryItem[];
  total: number;
}

export interface InventoryTransactionListResponse {
  transactions: InventoryTransaction[];
  total: number;
}

export async function listInventoryApi(params?: {
  plt_id?: string;
  inventory_type?: string;
  q?: string;
  limit?: number;
  offset?: number;
}, options?: Pick<ApiRequestOptions, "gardenId">): Promise<InventoryListResponse> {
  const qs = new URLSearchParams();
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    }
  }
  const query = qs.toString();
  return apiGet<InventoryListResponse>(
    `/api/inventory${query ? `?${query}` : ""}`,
    options,
  );
}

export async function getInventoryItemApi(
  itemId: string,
): Promise<InventoryItem> {
  return apiGet<InventoryItem>(`/api/inventory/${itemId}`);
}

export async function createInventoryItemApi(data: {
  plt_id?: string | null;
  label?: string;
  inventory_type?: InventoryType;
  unit?: string;
}, options?: Pick<ApiRequestOptions, "gardenId">): Promise<{ status: string; id: string }> {
  return apiPost<{ status: string; id: string }>("/api/inventory", data, options);
}

export async function updateInventoryItemApi(
  itemId: string,
  fields: {
    plt_id?: string | null;
    label?: string;
    inventory_type?: InventoryType;
    unit?: string;
  },
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<{ status: string }> {
  return apiPatch<{ status: string }>(`/api/inventory/${itemId}`, fields, options);
}

export async function deleteInventoryItemApi(
  itemId: string,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/api/inventory/${itemId}`, options);
}

export async function listInventoryTransactionsApi(
  itemId: string,
  params?: { limit?: number; offset?: number },
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<InventoryTransactionListResponse> {
  const qs = new URLSearchParams();
  if (params) {
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.offset !== undefined) qs.set("offset", String(params.offset));
  }
  const query = qs.toString();
  return apiGet<InventoryTransactionListResponse>(
    `/api/inventory/${itemId}/transactions${query ? `?${query}` : ""}`,
    options,
  );
}

export async function addInventoryTransactionApi(
  itemId: string,
  data: {
    delta: number;
    reason?: TransactionReason;
    source_name?: string;
    cost_minor?: number | null;
    occurred_on: string;
    storage_location?: string;
    notes?: string;
    journal_entry_id?: string | null;
  },
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<{ status: string; id: number }> {
  return apiPost<{ status: string; id: number }>(
    `/api/inventory/${itemId}/transactions`,
    data,
    options,
  );
}

// ── Tasks API ─────────────────────────────────────────────────

export async function fetchAttentionTodayApi(): Promise<AttentionTodayResponse> {
  return apiGet<AttentionTodayResponse>("/api/attention/today");
}

export async function fetchAttentionPreferencesApi(): Promise<AttentionPreferences> {
  return apiGet<AttentionPreferences>("/api/attention/preferences");
}

export async function updateAttentionPreferencesApi(
  body: AttentionPreferencesUpdate,
): Promise<AttentionPreferences> {
  const response = await checked(
    await apiFetch("/api/attention/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
    "/api/attention/preferences",
  );
  return (await response.json()) as AttentionPreferences;
}

export async function markAttentionItemReadApi(
  itemId: string,
): Promise<{ status: string }> {
  return apiPost<{ status: string }>(
    `/api/attention/items/${encodeApiPathSegment(itemId)}/read`,
    {},
  );
}

export async function dismissAttentionItemApi(
  itemId: string,
): Promise<{ status: string }> {
  return apiPost<{ status: string }>(
    `/api/attention/items/${encodeApiPathSegment(itemId)}/dismiss`,
    {},
  );
}

export async function snoozeAttentionItemApi(
  itemId: string,
  body: { snoozed_until_ms: number; reason?: string; metadata?: Record<string, unknown> },
): Promise<{ status: string }> {
  return apiPost<{ status: string }>(
    `/api/attention/items/${encodeApiPathSegment(itemId)}/snooze`,
    body,
  );
}

export async function restoreAttentionItemApi(
  itemId: string,
): Promise<{ status: string }> {
  return apiPost<{ status: string }>(
    `/api/attention/items/${encodeApiPathSegment(itemId)}/restore`,
    {},
  );
}

export async function restoreAttentionOutcomeApi(
  outcomeId: string,
): Promise<{ status: string }> {
  return apiPost<{ status: string }>(
    `/api/attention/outcomes/${encodeApiPathSegment(outcomeId)}/restore`,
    {},
  );
}

export async function fetchTasksApi(
  params: Record<string, string | number>,
): Promise<TaskListResponse> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const query = qs.toString();
  return apiGet<TaskListResponse>(`/api/tasks${query ? `?${query}` : ""}`);
}

export async function fetchTaskApi(
  taskId: string,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<GardenTask> {
  return apiGet<GardenTask>(`/api/tasks/${taskId}`, options);
}

export async function createTaskApi(body: {
  task_type: string;
  title: string;
  description?: string;
  severity?: string;
  due_on: string;
  plant_ids?: string[];
  plot_ids?: string[];
}): Promise<{ status: string; id: string }> {
  return apiPost<{ status: string; id: string }>("/api/tasks", body);
}

export async function updateTaskApi(
  taskId: string,
  body: Record<string, unknown>,
): Promise<{ status: string }> {
  return apiPatch<{ status: string }>(`/api/tasks/${taskId}`, body);
}

export async function taskActionApi(
  taskId: string,
  body: TaskActionRequest,
  options?: Pick<ApiRequestOptions, "gardenId" | "operationId">,
): Promise<{ status: string; updated_at_ms: number }> {
  return apiPost<{ status: string; updated_at_ms: number }>(
    `/api/tasks/${taskId}/action`,
    body,
    options,
  );
}

export async function batchTaskActionApi(
  taskIds: string[],
  body: BatchTaskActionRequest,
): Promise<{ status: string; updated: number }> {
  return apiPost<{ status: string; updated: number }>("/api/tasks/batch-action", {
    task_ids: taskIds,
    ...body,
  });
}

export async function deleteTaskApi(
  taskId: string,
): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/api/tasks/${taskId}`);
}

export async function generateTasksApi(): Promise<{
  created: number;
  skipped: number;
}> {
  return apiPost<{ created: number; skipped: number }>(
    "/api/tasks/generate",
    {},
  );
}

export async function refreshTaskDescriptionsApi(
  forceAll = false,
): Promise<{
  updated: number;
}> {
  return apiPost<{ updated: number }>(
    "/api/tasks/refresh-descriptions",
    { force_all: forceAll },
  );
}

// ── Notifications API ────────────────────────────────────────

export async function fetchNotificationsApi(
  params: Record<string, string | number | boolean>,
): Promise<NotificationListResponse> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  if (!qs.has("include_total")) {
    qs.set("include_total", "false");
  }
  const query = qs.toString();
  const result = await apiGet<NotificationListResponse & { total?: number }>(
    `/api/notifications${query ? `?${query}` : ""}`,
  );
  return {
    ...result,
    total: result.total ?? result.notifications.length,
  };
}

export async function markNotificationReadApi(
  id: string,
): Promise<{ status: string }> {
  return apiPost<{ status: string }>(`/api/notifications/${id}/read`, {});
}

export async function markAllNotificationsReadApi(): Promise<{
  status: string;
  updated: number;
}> {
  return apiPost<{ status: string; updated: number }>(
    "/api/notifications/read-all",
    {},
  );
}

export async function dismissNotificationApi(
  id: string,
): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/api/notifications/${id}`);
}

export async function fetchNotificationPreferencesApi(): Promise<NotificationPreferences> {
  return apiGet<NotificationPreferences>("/api/notifications/preferences");
}

export async function updateNotificationPreferencesApi(
  body: Partial<NotificationPreferences>,
): Promise<{ status: string }> {
  const response = await checked(
    await apiFetch("/api/notifications/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
    "/api/notifications/preferences",
  );
  return (await response.json()) as { status: string };
}

export async function generateNotificationsApi(): Promise<{
  created: number;
  skipped: number;
}> {
  return apiPost<{ created: number; skipped: number }>(
    "/api/notifications/generate",
    {},
  );
}

// ── Weather API ──────────────────────────────────────────────

export async function fetchWeatherSummaryApi(): Promise<WeatherSummary> {
  return apiGet<WeatherSummary>("/api/weather/summary");
}

export async function checkWeatherApi(): Promise<{
  forecast_available: boolean;
  alerts_created: number;
  alerts_skipped: number;
}> {
  return apiPost<{
    forecast_available: boolean;
    alerts_created: number;
    alerts_skipped: number;
  }>("/api/weather/check", {});
}

export async function fetchWeatherAlertsApi(): Promise<{ alerts: WeatherAlert[] }> {
  return apiGet<{ alerts: WeatherAlert[] }>("/api/weather/alerts");
}

export async function dismissWeatherAlertApi(id: number): Promise<{ status: string }> {
  return apiPost<{ status: string }>(`/api/weather/alerts/${id}/dismiss`, {});
}

// ── Issues API ──────────────────────────────────────────────────

export async function fetchIssuesApi(
  params: Record<string, string | number>,
): Promise<IssueListResponse> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const query = qs.toString();
  return apiGet<IssueListResponse>(`/api/issues${query ? `?${query}` : ""}`);
}

export async function fetchIssueApi(
  issueId: string,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<GardenIssue> {
  return apiGet<GardenIssue>(`/api/issues/${issueId}`, options);
}

export async function fetchIssueHistoryApi(issueId: string): Promise<IssueHistoryResponse> {
  return apiGet<IssueHistoryResponse>(`/api/issues/${issueId}/history`);
}

export async function createIssueApi(body: {
  issue_type: string;
  title: string;
  description?: string;
  severity?: string;
  suspected_cause?: string;
  treatment_plan?: string;
  follow_up_on?: string;
  plant_ids?: string[];
  plot_ids?: string[];
}, options?: Pick<ApiRequestOptions, "gardenId" | "operationId">): Promise<{ status: string; id: string }> {
  return apiPost<{ status: string; id: string }>("/api/issues", body, options);
}

export async function updateIssueApi(
  issueId: string,
  body: Record<string, unknown>,
): Promise<{ status: string }> {
  return apiPatch<{ status: string }>(`/api/issues/${issueId}`, body);
}

export async function resolveIssueApi(issueId: string): Promise<{ status: string }> {
  return apiPost<{ status: string }>(`/api/issues/${issueId}/resolve`, {});
}

export async function deleteIssueApi(issueId: string): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/api/issues/${issueId}`);
}

export async function fetchIssueSummaryApi(): Promise<IssueSummary> {
  return apiGet<IssueSummary>("/api/issues/summary");
}

// ── Saved Views ─────────────────────────────────────────────

export async function fetchSavedViewsApi(
  params?: Record<string, string>,
): Promise<{ views: SavedView[] }> {
  const qs = params ? new URLSearchParams(params).toString() : "";
  return apiGet<{ views: SavedView[] }>(`/api/saved-views${qs ? `?${qs}` : ""}`);
}

export async function fetchSavedViewPresetsApi(): Promise<{ presets: SavedViewPreset[] }> {
  return apiGet<{ presets: SavedViewPreset[] }>("/api/saved-views/presets");
}

export async function createSavedViewApi(body: {
  view_type: string;
  label: string;
  filter_json: Record<string, unknown>;
  sort_order?: number;
}): Promise<{ status: string; id: number }> {
  return apiPost<{ status: string; id: number }>("/api/saved-views", body);
}

export async function updateSavedViewApi(
  id: number,
  body: Record<string, unknown>,
): Promise<{ status: string }> {
  return apiPatch<{ status: string }>(`/api/saved-views/${id}`, body);
}

export async function deleteSavedViewApi(id: number): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/api/saved-views/${id}`);
}

// ── Calendar ───────────────────────────────────────────────

export async function fetchCalendarPreferencesApi(): Promise<CalendarPreferencesResponse> {
  return apiGet<CalendarPreferencesResponse>("/api/calendar/preferences");
}

export async function updateCalendarPreferencesApi(
  body: Partial<CalendarPreferences>,
): Promise<{ status: string; preferences: CalendarPreferences }> {
  return apiPatch<{ status: string; preferences: CalendarPreferences }>(
    "/api/calendar/preferences",
    body,
  );
}

export async function fetchCalendarEventsApi(params: {
  start: string;
  end: string;
  preset?: string;
  visible_sources?: string;
  include_recent_history?: boolean;
  selected_plant_ids?: string;
  selected_plot_ids?: string;
  selected_zone_codes?: string;
}): Promise<CalendarEventsResponse> {
  const query = new URLSearchParams({
    start: params.start,
    end: params.end,
  });
  if (params.preset) query.set("preset", params.preset);
  if (params.visible_sources) query.set("visible_sources", params.visible_sources);
  if (params.include_recent_history !== undefined) {
    query.set(
      "include_recent_history",
      params.include_recent_history ? "true" : "false",
    );
  }
  if (params.selected_plant_ids) query.set("selected_plant_ids", params.selected_plant_ids);
  if (params.selected_plot_ids) query.set("selected_plot_ids", params.selected_plot_ids);
  if (params.selected_zone_codes) query.set("selected_zone_codes", params.selected_zone_codes);
  return apiGet<CalendarEventsResponse>(`/api/calendar/events?${query.toString()}`);
}

export function buildCalendarExportUrl(params: {
  start: string;
  end: string;
  preset?: string;
  visible_sources?: string;
  include_recent_history?: boolean;
  selected_plant_ids?: string;
  selected_plot_ids?: string;
  selected_zone_codes?: string;
}): string {
  const query = new URLSearchParams({
    start: params.start,
    end: params.end,
  });
  if (activeGardenId !== null) query.set("garden_id", String(activeGardenId));
  if (params.preset) query.set("preset", params.preset);
  if (params.visible_sources) query.set("visible_sources", params.visible_sources);
  if (typeof params.include_recent_history === "boolean") {
    query.set(
      "include_recent_history",
      params.include_recent_history ? "true" : "false",
    );
  }
  if (params.selected_plant_ids) query.set("selected_plant_ids", params.selected_plant_ids);
  if (params.selected_plot_ids) query.set("selected_plot_ids", params.selected_plot_ids);
  if (params.selected_zone_codes) query.set("selected_zone_codes", params.selected_zone_codes);
  return `/api/calendar/export.ics?${query.toString()}`;
}

export async function listCalendarSubscriptionsApi(): Promise<{
  subscriptions: CalendarSubscription[];
}> {
  return apiGet<{ subscriptions: CalendarSubscription[] }>("/api/calendar/subscriptions");
}

export async function createCalendarSubscriptionApi(body: {
  label?: string;
  preset_key?: string;
  visible_sources?: string[];
}): Promise<CalendarSubscriptionCreateResult> {
  return apiPost<CalendarSubscriptionCreateResult>("/api/calendar/subscriptions", body);
}

export async function deleteCalendarSubscriptionApi(
  id: string,
): Promise<{ status: string; id: string }> {
  return apiDelete<{ status: string; id: string }>(
    `/api/calendar/subscriptions/${encodeURIComponent(id)}`,
  );
}

export async function createCalendarManualEventApi(
  body: CalendarManualEventInput,
): Promise<{ status: string; event: CalendarEventsResponse["events"][number] }> {
  return apiPost<{ status: string; event: CalendarEventsResponse["events"][number] }>(
    "/api/calendar/manual-events",
    body,
  );
}

export async function updateCalendarManualEventApi(
  id: string,
  body: CalendarManualEventInput,
): Promise<{ status: string; event: CalendarEventsResponse["events"][number] }> {
  return apiPatch<{ status: string; event: CalendarEventsResponse["events"][number] }>(
    `/api/calendar/manual-events/${encodeURIComponent(id)}`,
    body,
  );
}

export async function deleteCalendarManualEventApi(
  id: string,
): Promise<{ status: string; id: string }> {
  return apiDelete<{ status: string; id: string }>(
    `/api/calendar/manual-events/${encodeURIComponent(id)}`,
  );
}

// ── Harvest ──────────────────────────────────────────────────

export async function fetchHarvestApi(
  params: Record<string, string | number>,
): Promise<HarvestListResponse> {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== "" && v !== undefined) query.set(k, String(v));
  }
  const qs = query.toString();
  return apiGet<HarvestListResponse>(`/api/harvest${qs ? `?${qs}` : ""}`);
}

export async function createHarvestApi(body: {
  occurred_on: string;
  quantity: number;
  unit: string;
  quality?: string;
  notes?: string;
  plant_ids?: string[];
  plot_ids?: string[];
}, options?: Pick<ApiRequestOptions, "gardenId" | "operationId">): Promise<{ status: string; id: string; journal_entry_id?: string | null }> {
  return apiPost<{ status: string; id: string; journal_entry_id?: string | null }>(
    "/api/harvest",
    body,
    options,
  );
}

export async function updateHarvestApi(
  id: string,
  body: Record<string, unknown>,
): Promise<{ status: string }> {
  return apiPatch<{ status: string }>(`/api/harvest/${id}`, body);
}

export async function deleteHarvestApi(id: string): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/api/harvest/${id}`);
}

export async function fetchHarvestSummaryApi(
  params?: Record<string, string | number>,
): Promise<HarvestSummary> {
  const qs = params ? new URLSearchParams(
    Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)])),
  ).toString() : "";
  return apiGet<HarvestSummary>(`/api/harvest/summary${qs ? `?${qs}` : ""}`);
}

// ── Procurement API ──

export async function fetchProcurementApi(
  params: Record<string, string | number>,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<ProcurementListResponse> {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== "" && v !== undefined) query.set(k, String(v));
  }
  const qs = query.toString();
  return apiGet<ProcurementListResponse>(
    `/api/procurement${qs ? `?${qs}` : ""}`,
    options,
  );
}

export async function createProcurementApi(
  body: Record<string, unknown>,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<{ status: string; id: string }> {
  return apiPost<{ status: string; id: string }>("/api/procurement", body, options);
}

export async function updateProcurementApi(
  id: string,
  body: Record<string, unknown>,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<{ status: string }> {
  return apiPatch<{ status: string }>(`/api/procurement/${id}`, body, options);
}

export async function transitionProcurementApi(
  id: string,
  body: { to_status: string; ordered_on?: string; received_on?: string },
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<{ status: string }> {
  return apiPost<{ status: string }>(
    `/api/procurement/${id}/transition`,
    body,
    options,
  );
}

export async function deleteProcurementApi(
  id: string,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/api/procurement/${id}`, options);
}

export async function fetchProcurementSummaryApi(): Promise<ProcurementSummary> {
  return apiGet<ProcurementSummary>("/api/procurement/summary");
}

// ── Planner API ──

export async function fetchPlannerSuggestionsApi(
  params?: Record<string, string | number>,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<PlannerResult> {
  const query = new URLSearchParams();
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== "" && v !== undefined) query.set(k, String(v));
    }
  }
  const qs = query.toString();
  return apiGet<PlannerResult>(
    `/api/planner/suggestions${qs ? `?${qs}` : ""}`,
    options,
  );
}

export async function fetchGardenProfileApi(
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<GardenProfile> {
  return apiGet<GardenProfile>("/api/planner/garden-profile", options);
}

export async function fetchCompanionCheckApi(
  params: { plot_id: string; plt_id: string },
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<CompanionCheck> {
  const qs = new URLSearchParams(params).toString();
  return apiGet<CompanionCheck>(`/api/planner/companions?${qs}`, options);
}

export async function fetchPlannerGoalApi(
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<string | null> {
  const result = await apiGet<{ goal: string | null }>("/api/planner/goal", options);
  return result.goal;
}

export async function savePlannerGoalApi(
  goal: string | null,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<void> {
  const request: RequestInit & Pick<ApiRequestOptions, "gardenId"> = {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal }),
  };
  if (options?.gardenId !== undefined) request.gardenId = options.gardenId;
  await checked(await apiFetch("/api/planner/goal", request), "/api/planner/goal");
}

// ── Workflows API ──

export interface AvailableWorkflowStep {
  id: string;
  title: string;
}

export interface AvailableWorkflow {
  id: string;
  name: string;
  step_count: number;
  steps: AvailableWorkflowStep[];
}

export interface AvailableWorkflowsResponse {
  workflows: AvailableWorkflow[];
}

export interface StartWorkflowResult {
  created: number;
  skipped: number;
  workflow_id: string;
}

export async function fetchAvailableWorkflowsApi(
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<AvailableWorkflowsResponse> {
  return apiGet<AvailableWorkflowsResponse>("/api/workflows/available", options);
}

export async function startWorkflowApi(
  workflowId: string,
  selectedSteps: string[],
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<StartWorkflowResult> {
  return apiPost<StartWorkflowResult>(
    "/api/workflows/start",
    {
      workflow_id: workflowId,
      selected_steps: selectedSteps,
    },
    options,
  );
}

// ── Exports API ──

export type ExportResource =
  | "plants"
  | "inventory"
  | "tasks"
  | "journal"
  | "harvest"
  | "issues"
  | "procurement"
  | "seasonal-summary";

export type PrintableExportResource = ExportResource;

export function getExportUrl(
  resource: ExportResource,
  format?: "csv" | "json",
  params?: Record<string, string>,
): string;
export function getExportUrl(
  resource: PrintableExportResource,
  format: "html",
  params?: Record<string, string>,
): string;
export function getExportUrl(
  resource: ExportResource,
  format: "csv" | "json" | "html" = "csv",
  params?: Record<string, string>,
): string {
  const query = new URLSearchParams({ format, ...params });
  if (!query.has("garden_id") && activeGardenId !== null) {
    query.set("garden_id", String(activeGardenId));
  }
  return `/api/exports/${resource}?${query.toString()}`;
}

export async function fetchSeasonalSummary(
  params?: Record<string, string | number>,
  options?: Pick<ApiRequestOptions, "gardenId">,
): Promise<
  Record<string, unknown>
> {
  const qs = params ? new URLSearchParams(
    Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)])),
  ).toString() : "";
  return apiGet<Record<string, unknown>>(
    `/api/exports/seasonal-summary${qs ? `?${qs}` : ""}`,
    options,
  );
}

export interface PlotAssignment {
  plot_id: string;
  quantity: number;
  seen_growing: boolean | null;
  seen_growing_date: string | null;
  seen_growing_year?: number | null;
  seen_growing_is_current_year?: boolean;
}

export async function getPlantAssignmentsApi(
  pltId: string,
): Promise<PlotAssignment[]> {
  return apiGet<PlotAssignment[]>(
    `/api/plants/${encodeApiPathSegment(pltId)}/assignments`,
  );
}

export interface PlotSeenGrowingUpdate {
  plot_id: string;
  plt_id: string;
  seen_growing: boolean | null;
  seen_growing_date: string | null;
}

export async function bulkUpdateSeenGrowingApi(
  updates: PlotSeenGrowingUpdate[],
): Promise<{ status: string; updated: number }> {
  return apiPatch<{ status: string; updated: number }>(
    "/api/plots/plants/seen-growing",
    { updates },
  );
}

// --- Plant identification ---

export interface PlantCandidate {
  name: string;
  latin: string;
  scientific_name: string;
  family: string;
  confidence: number;
  source: "plantnet" | "claude";
  gbif_id: string;
}

export interface IdentifyPlantResult {
  candidates: PlantCandidate[];
  attribution: string;
  plantnet_remaining: number | null;
}

const AI_PHOTO_UPLOAD_ALLOWED_MIME_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

export const AI_PHOTO_UPLOAD_ACCEPT = "image/jpeg,image/png,image/webp";
export const AI_PHOTO_UPLOAD_MAX_BYTES = 5 * 1024 * 1024;

export type AiPhotoUploadValidationError = "unsupported_type" | "too_large";

export function validateAiPhotoUpload(
  file: Pick<File, "size" | "type">,
): AiPhotoUploadValidationError | null {
  const mimeType = file.type.trim().toLowerCase();
  if (mimeType && !AI_PHOTO_UPLOAD_ALLOWED_MIME_TYPES.has(mimeType)) {
    return "unsupported_type";
  }
  if (file.size > AI_PHOTO_UPLOAD_MAX_BYTES) {
    return "too_large";
  }
  return null;
}

export async function identifyPlantApi(options: {
  image: File;
  organ?: string;
  onProgress?: (pct: number) => void;
}): Promise<IdentifyPlantResult> {
  const params = new URLSearchParams();
  if (options.organ) params.set("organ", options.organ);
  const path = `/api/ai/identify-plant?${params.toString()}`;
  const uploadOpts: { onProgress?: (pct: number) => void } = {};
  if (options.onProgress) uploadOpts.onProgress = options.onProgress;
  return uploadBinary<IdentifyPlantResult>(
    path,
    options.image,
    {
      "Content-Type": options.image.type || "image/jpeg",
      "x-upload-filename": options.image.name || "photo.jpg",
    },
    uploadOpts,
  );
}

// --- Disease diagnosis ---

export interface DiagnosisCandidate {
  issue_type: string;
  likely_cause: string;
  confidence: "high" | "medium" | "low";
  description: string;
  suggested_treatment: string;
  reasoning: string;
  related_history: string;
}

export interface DiagnoseResult {
  diagnoses: DiagnosisCandidate[];
  context_used: {
    plant_name: string;
    plot_id: string;
    prior_issues_count: number;
  };
  disclaimer: string;
}

export async function diagnosePlantApi(options: {
  image: File;
  pltId?: string;
  plotId?: string;
  symptoms?: string;
  onProgress?: (pct: number) => void;
}): Promise<DiagnoseResult> {
  const params = new URLSearchParams();
  if (options.pltId) params.set("plt_id", options.pltId);
  if (options.plotId) params.set("plot_id", options.plotId);
  if (options.symptoms) params.set("symptoms", options.symptoms);
  const path = `/api/ai/diagnose-plant?${params.toString()}`;
  const uploadOpts: { onProgress?: (pct: number) => void } = {};
  if (options.onProgress) uploadOpts.onProgress = options.onProgress;
  return uploadBinary<DiagnoseResult>(
    path,
    options.image,
    {
      "Content-Type": options.image.type || "image/jpeg",
      "x-upload-filename": options.image.name || "photo.jpg",
    },
    uploadOpts,
  );
}
