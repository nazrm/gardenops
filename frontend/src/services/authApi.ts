import { t } from "../core/authI18n";

const AUTH_CSRF_STORAGE_KEY = "gardenops-csrf-token";
const ACTIVE_GARDEN_STORAGE_KEY = "gardenops-active-garden-id";
const DEFAULT_CSRF_COOKIE_NAMES = ["gardenops_csrf", "XSRF-TOKEN"];
const DEFAULT_TIMEOUT_MS = 30_000;

export class ApiError extends Error {
  status: number;
  requestId: string;
  path: string;

  constructor(status: number, message: string, options?: { requestId?: string; path?: string }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = options?.requestId ?? "";
    this.path = options?.path ?? "";
  }
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

export interface AuthMfaChallenge {
  required: boolean;
  setup_required: boolean;
  methods: string[];
  method?: string | null;
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
  plot_assignment_meanings: unknown[];
  subscription_tier: "home" | "enthusiast" | "pro";
  allowed_features: string[];
  security_warnings: string[];
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

export interface PasswordPolicy {
  min_length: number;
  require_upper: boolean;
  require_lower: boolean;
  require_digit: boolean;
  require_symbol: boolean;
  reject_common: boolean;
  disallow_username: boolean;
  check_hibp: boolean;
}

export interface PasskeyOptionsResponse {
  challenge_token: string;
  publicKey: unknown;
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
  options?: { requestId?: string; path?: string },
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

function responseRequestId(response: Response): string {
  return (response.headers.get("X-Request-ID") || "").trim();
}

function readCookieValue(name: string): string {
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

function authHeaders(): HeadersInit {
  const headers: Record<string, string> = {};
  if (activeGardenId !== null && activeGardenId !== undefined) {
    headers["x-garden-id"] = String(activeGardenId);
  }
  return headers;
}

async function apiFetch(input: RequestInfo | URL, init?: RequestInit & { timeoutMs?: number }): Promise<Response> {
  const path = normalizeApiPath(input);
  const { timeoutMs: requestedTimeoutMs, ...fetchInit } = init ?? {};
  const headers = new Headers(authHeaders());
  const requestHeaders = new Headers((fetchInit.headers as HeadersInit | undefined) ?? {});
  requestHeaders.forEach((value, key) => headers.set(key, value));
  const method = (fetchInit.method ?? "GET").toUpperCase();
  if (
    (method === "POST" || method === "PUT" || method === "PATCH" || method === "DELETE")
    && !headers.has("x-csrf-token")
    && !headers.has("x-xsrf-token")
  ) {
    const csrfToken = getStoredCsrfToken();
    if (csrfToken) headers.set("x-csrf-token", csrfToken);
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
    throw new ApiError(
      0,
      timedOut ? "Request timed out" : "Network request failed",
      { path },
    );
  } finally {
    clearTimeout(timeoutId);
  }
}

async function checked(res: Response, path: string): Promise<Response> {
  if (!res.ok) {
    const fallback = `Request failed (${res.status})`;
    const body = await res.json().catch(() => ({})) as unknown;
    throw parseApiErrorJson(res.status, body, fallback, {
      requestId: responseRequestId(res),
      path,
    });
  }
  return res;
}

async function apiGet<T>(path: string): Promise<T> {
  const response = await checked(await apiFetch(path), path);
  return (await response.json()) as T;
}

async function apiPost<T>(
  path: string,
  body: unknown,
  options?: { timeoutMs?: number },
): Promise<T> {
  const request: RequestInit & { timeoutMs?: number } = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  if (options?.timeoutMs !== undefined) request.timeoutMs = options.timeoutMs;
  const response = await checked(await apiFetch(path, request), path);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
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

export async function getAuthStatusApi(): Promise<AuthStatus> {
  return apiGet<AuthStatus>("/api/auth/status");
}

export async function bootstrapAuthApi(username: string, password: string): Promise<void> {
  await apiPost("/api/auth/bootstrap", { username, password, role: "admin" });
}

export async function loginApi(
  username: string,
  password: string,
  options: { mfaCode?: string; recoveryCode?: string } = {},
): Promise<LoginResponse> {
  return apiPost<LoginResponse>("/api/auth/login", {
    username,
    password,
    mfa_code: options.mfaCode ?? "",
    recovery_code: options.recoveryCode ?? "",
  });
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
    { challenge_token: challengeToken, credential },
  );
}

export async function changePasswordApi(
  currentPassword: string,
  newPassword: string,
): Promise<{ status: string; revoked_sessions: number }> {
  return apiPost<{ status: string; revoked_sessions: number }>(
    "/api/auth/change-password",
    { current_password: currentPassword, new_password: newPassword },
  );
}

export async function logoutApi(): Promise<void> {
  await apiPost("/api/auth/logout", {});
}

export async function getAuthMeApi(): Promise<AuthUserProfile> {
  return apiGet<AuthUserProfile>("/api/auth/me");
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

export async function peekInvitationApi(token: string): Promise<{ username: string }> {
  return apiPost<{ username: string }>("/api/auth/invitations/peek", { token });
}

export async function checkHibpApi(password: string): Promise<{ breached: boolean }> {
  return apiPost<{ breached: boolean }>("/api/auth/check-hibp", { password });
}
