import { appName } from "./branding";

export type Locale = "en" | "no";

type TranslationParams = Record<string, string | number | boolean | null | undefined>;
type TranslationEntry = string | ((params: TranslationParams) => string);

const LOCALE_STORAGE_KEY = "gardenops-locale";
const FALLBACK_LOCALE: Locale = "en";

const translations: Record<Locale, Record<string, TranslationEntry>> = {
  en: {
    "common.refresh": "Refresh",
    "auth.app_title": "{appName}",
    "auth.password": "Password",
    "auth.current_password": "Current password",
    "auth.new_password": "New password",
    "auth.confirm_new_password": "Confirm new password",
    "auth.change_password": "Change password",
    "auth.changing_password": "Changing password...",
    "auth.password_change_required": "Change your password",
    "auth.password_change_required_subtitle": ({ username }) => `${username}, update your password before continuing.`,
    "auth.accept_invitation": "Accept invitation",
    "auth.sign_in_instead": "Sign in instead",
    "auth.accepting": "Accepting...",
    "auth.welcome": "Welcome!",
    "auth.signed_in_as": ({ username }) => `You are signed in as ${username}`,
    "auth.continue": "Continue",
    "auth.bootstrap_subtitle": "Create your admin account to get started.",
    "auth.signin_subtitle": "",
    "auth.username": "Username",
    "auth.authenticator_code": "Authenticator code",
    "auth.recovery_code": "Recovery code",
    "auth.mfa_hint": "Enter an authenticator code, or leave that blank and use a recovery code.",
    "auth.create_account": "Create account",
    "auth.enter_action": "Enter",
    "auth.sign_in_action": "Sign in",
    "auth.login_action": "Login",
    "auth.use_passkey": "Use passkey",
    "auth.creating_passkey": "Creating passkey...",
    "auth.passkey_default_name": "Passkey",
    "auth.add_passkey": "Add passkey",
    "auth.not_now": "Not now",
    "auth.passkey_signing_in": "Waiting for passkey...",
    "auth.use_password_instead": "Use password instead",
    "auth.passkey_cancelled": "Passkey sign-in was cancelled.",
    "auth.passkey_password_change_required": "Sign in with your password to change it before using a passkey.",
    "identity.passkeys.rename": "Rename",
    "identity.passkeys.rename_prompt": "New passkey name",
    "identity.passkeys.renamed": "Passkey renamed",
    "identity.passkeys.revoke_warning": ({ name }) => `Revoke passkey \"${name}\"? Make sure another sign-in or recovery method works before continuing.`,
    "identity.passkeys.last_revoke_warning": ({ name }) => `Revoke your last passkey \"${name}\"? Confirm that your password or recovery method works first.`,
    "identity.passkeys.change_failed": "The passkey change could not be completed. Try again after verifying your sign-in.",
    "identity.sessions.title": "Your sessions",
    "identity.sessions.description": "Review where your account is signed in and revoke devices you no longer use.",
    "identity.sessions.current": "Current session",
    "identity.sessions.device": "Device",
    "identity.sessions.unknown_device": "Unknown device",
    "identity.sessions.last_seen": "Last active",
    "identity.sessions.expires": "Expires",
    "identity.sessions.revoke": "Revoke session",
    "identity.sessions.revoke_confirm": ({ device }) => `Revoke the session on ${device}? That device will need to sign in again.`,
    "identity.sessions.revoked": "Session revoked",
    "identity.sessions.none": "No sessions are available.",
    "identity.sessions.unavailable": "Session details are temporarily unavailable.",
    "identity.sessions.change_failed": "The session could not be revoked. Refresh the list and try again.",
    "identity.mfa.cancel": "Cancel setup",
    "identity.mfa.cancel_confirm": "Cancel this authenticator setup? The pending secret will no longer be usable.",
    "identity.mfa.cancelled": "Authenticator setup cancelled",
    "identity.mfa.regenerate_confirm": "Generate new recovery codes? Every previous recovery code will stop working.",
    "identity.mfa.disable_confirm": "Disable MFA? Confirm that you understand this removes the authenticator and all recovery codes.",
    "identity.mfa.change_failed": "The security change could not be completed. Verify your sign-in and try again.",
    "identity.incident.title": "Emergency read-only mode",
    "identity.incident.active": "Active: all write operations are blocked. Read-only access remains available.",
    "identity.incident.inactive": "Inactive: writes follow your normal garden role.",
    "identity.incident.expires": ({ date }) => `Automatic restore: ${date}`,
    "identity.capabilities.refreshed": "Account access refreshed",
    "identity.capabilities.role_confirm": ({ username, from, to }) => `Change ${username} from ${from} to ${to}? Their enabled controls will refresh on the next authorization check.`,
    "auth.verify_sign_in": "Verify sign in",
    "auth.signing_in": "Signing in...",
    "auth.verifying": "Verifying...",
    "auth.invite_welcome": ({ username }) => `Welcome, ${username}`,
    "auth.invite_invalid": "This invitation is invalid or has expired.",
    "auth.invite_loading": "Loading invitation...",
    "auth.password_checklist.min_length": ({ count }) => `At least ${count} characters`,
    "auth.password_checklist.lowercase": "Lowercase letter",
    "auth.password_checklist.uppercase": "Uppercase letter",
    "auth.password_checklist.digit": "Digit",
    "auth.password_checklist.symbol": "Symbol",
    "auth.password_checklist.hibp_ok": "Not found in known breaches",
    "auth.password_checklist.hibp_breached": "Found in a known data breach",
    "auth.password_checklist.hibp_checking": "Checking breaches...",
    "auth.password_checklist.hibp_error": "Could not check breaches",
    "auth.password_checklist.reject_common": "Must not be a common password",
    "auth.password_checklist.disallow_username": "Must not contain your username",
    "error.write_access": "Write access required",
    "error.auth_required": "Authentication required",
    "error.forbidden": "You do not have permission for this action",
    "error.plant_not_found": "Plant not found in active garden",
    "error.plot_not_found": "Plot not found in active garden",
    "error.task_not_found": "Task not found",
    "error.issue_not_found": "Issue not found",
    "error.harvest_not_found": "Harvest entry not found",
    "error.missing_garden": "No active garden selected",
    "error.request_failed": ({ status }) => `Request failed (${status})`,
    "error.unknown": "An unexpected error occurred",
  },
  no: {
    "common.refresh": "Oppdater",
    "auth.app_title": "{appName}",
    "auth.password": "Passord",
    "auth.current_password": "Nåværende passord",
    "auth.new_password": "Nytt passord",
    "auth.confirm_new_password": "Bekreft nytt passord",
    "auth.change_password": "Endre passord",
    "auth.changing_password": "Endrer passord...",
    "auth.password_change_required": "Endre passordet ditt",
    "auth.password_change_required_subtitle": ({ username }) => `${username}, oppdater passordet ditt før du fortsetter.`,
    "auth.accept_invitation": "Godta invitasjon",
    "auth.sign_in_instead": "Logg inn i stedet",
    "auth.accepting": "Godtar...",
    "auth.welcome": "Velkommen!",
    "auth.signed_in_as": ({ username }) => `Du er logget inn som ${username}`,
    "auth.continue": "Fortsett",
    "auth.bootstrap_subtitle": "Opprett adminkontoen din for å komme i gang.",
    "auth.signin_subtitle": "",
    "auth.username": "Brukernavn",
    "auth.authenticator_code": "Autentiseringskode",
    "auth.recovery_code": "Gjenopprettingskode",
    "auth.mfa_hint": "Skriv inn en autentiseringskode, eller la den stå tom og bruk en gjenopprettingskode.",
    "auth.create_account": "Opprett konto",
    "auth.enter_action": "Gå inn",
    "auth.sign_in_action": "Logg inn",
    "auth.login_action": "Logg inn",
    "auth.use_passkey": "Bruk passnøkkel",
    "auth.creating_passkey": "Oppretter passnøkkel...",
    "auth.passkey_default_name": "Passnøkkel",
    "auth.add_passkey": "Legg til passnøkkel",
    "auth.not_now": "Ikke nå",
    "auth.passkey_signing_in": "Venter på passnøkkel...",
    "auth.use_password_instead": "Bruk passord i stedet",
    "auth.passkey_cancelled": "Passnøkkel-innlogging ble avbrutt.",
    "auth.passkey_password_change_required": "Logg inn med passordet ditt for å endre det før du bruker passnøkkel.",
    "identity.passkeys.rename": "Gi nytt navn",
    "identity.passkeys.rename_prompt": "Nytt navn på passnøkkel",
    "identity.passkeys.renamed": "Passnøkkelen fikk nytt navn",
    "identity.passkeys.revoke_warning": ({ name }) => `Tilbakekalle passnøkkelen \"${name}\"? Kontroller at en annen innloggings- eller gjenopprettingsmetode virker først.`,
    "identity.passkeys.last_revoke_warning": ({ name }) => `Tilbakekalle den siste passnøkkelen \"${name}\"? Bekreft at passordet eller gjenopprettingsmetoden virker først.`,
    "identity.passkeys.change_failed": "Passnøkkelendringen kunne ikke fullføres. Prøv igjen etter at du har bekreftet innloggingen.",
    "identity.sessions.title": "Dine økter",
    "identity.sessions.description": "Se hvor kontoen er innlogget, og tilbakekall enheter du ikke lenger bruker.",
    "identity.sessions.current": "Gjeldende økt",
    "identity.sessions.device": "Enhet",
    "identity.sessions.unknown_device": "Ukjent enhet",
    "identity.sessions.last_seen": "Sist aktiv",
    "identity.sessions.expires": "Utløper",
    "identity.sessions.revoke": "Tilbakekall økt",
    "identity.sessions.revoke_confirm": ({ device }) => `Tilbakekalle økten på ${device}? Enheten må logge inn igjen.`,
    "identity.sessions.revoked": "Økten er tilbakekalt",
    "identity.sessions.none": "Ingen økter er tilgjengelige.",
    "identity.sessions.unavailable": "Øktdetaljer er midlertidig utilgjengelige.",
    "identity.sessions.change_failed": "Økten kunne ikke tilbakekalles. Oppdater listen og prøv igjen.",
    "identity.mfa.cancel": "Avbryt oppsett",
    "identity.mfa.cancel_confirm": "Avbryte dette autentikatoroppsettet? Den ventende hemmeligheten kan ikke brukes videre.",
    "identity.mfa.cancelled": "Autentikatoroppsettet er avbrutt",
    "identity.mfa.regenerate_confirm": "Generere nye gjenopprettingskoder? Alle tidligere koder slutter å virke.",
    "identity.mfa.disable_confirm": "Deaktivere MFA? Bekreft at du forstår at autentikatoren og alle gjenopprettingskoder fjernes.",
    "identity.mfa.change_failed": "Sikkerhetsendringen kunne ikke fullføres. Bekreft innloggingen og prøv igjen.",
    "identity.incident.title": "Nødmodus med lesetilgang",
    "identity.incident.active": "Aktiv: alle skriveoperasjoner er blokkert. Lesetilgang er fortsatt tilgjengelig.",
    "identity.incident.inactive": "Inaktiv: skrivetilgang følger den vanlige hagerollen din.",
    "identity.incident.expires": ({ date }) => `Automatisk gjenoppretting: ${date}`,
    "identity.capabilities.refreshed": "Kontotilgang oppdatert",
    "identity.capabilities.role_confirm": ({ username, from, to }) => `Endre ${username} fra ${from} til ${to}? Aktiverte kontroller oppdateres ved neste tilgangskontroll.`,
    "auth.verify_sign_in": "Bekreft innlogging",
    "auth.signing_in": "Logger inn...",
    "auth.verifying": "Bekrefter...",
    "auth.invite_welcome": ({ username }) => `Velkommen, ${username}`,
    "auth.invite_invalid": "Denne invitasjonen er ugyldig eller har utløpt.",
    "auth.invite_loading": "Laster invitasjon...",
    "auth.password_checklist.min_length": ({ count }) => `Minst ${count} tegn`,
    "auth.password_checklist.lowercase": "Liten bokstav",
    "auth.password_checklist.uppercase": "Stor bokstav",
    "auth.password_checklist.digit": "Siffer",
    "auth.password_checklist.symbol": "Symbol",
    "auth.password_checklist.hibp_ok": "Ikke funnet i kjente datalekkasjer",
    "auth.password_checklist.hibp_breached": "Funnet i en kjent datalekkasje",
    "auth.password_checklist.hibp_checking": "Sjekker datalekkasjer...",
    "auth.password_checklist.hibp_error": "Kunne ikke sjekke datalekkasjer",
    "auth.password_checklist.reject_common": "Må ikke være et vanlig passord",
    "auth.password_checklist.disallow_username": "Må ikke inneholde brukernavnet ditt",
    "error.write_access": "Skrivetilgang kreves",
    "error.auth_required": "Autentisering kreves",
    "error.forbidden": "Du har ikke tilgang til denne handlingen",
    "error.plant_not_found": "Plante ikke funnet i aktiv hage",
    "error.plot_not_found": "Rute ikke funnet i aktiv hage",
    "error.task_not_found": "Oppgave ikke funnet",
    "error.issue_not_found": "Problem ikke funnet",
    "error.harvest_not_found": "Høstoppføring ikke funnet",
    "error.missing_garden": "Ingen aktiv hage valgt",
    "error.request_failed": ({ status }) => `Forespørsel feilet (${status})`,
    "error.unknown": "En uventet feil oppstod",
  },
};

function normalizeLocale(value: string | null | undefined): Locale | null {
  if (!value) return null;
  const normalized = value.toLowerCase();
  if (normalized === "no" || normalized === "nb" || normalized.startsWith("no-") || normalized.startsWith("nb-")) {
    return "no";
  }
  if (normalized === "en" || normalized.startsWith("en-")) {
    return "en";
  }
  return null;
}

function readStoredLocale(): Locale {
  try {
    return normalizeLocale(localStorage.getItem(LOCALE_STORAGE_KEY)) ?? FALLBACK_LOCALE;
  } catch {
    return FALLBACK_LOCALE;
  }
}

function applyLocaleToDocument(locale: Locale): void {
  document.documentElement.lang = locale === "no" ? "no" : "en";
}

let currentLocale: Locale = readStoredLocale();
applyLocaleToDocument(currentLocale);

export function getLocale(): Locale {
  return currentLocale;
}

export function setLocale(locale: Locale, options?: { persist?: boolean }): void {
  currentLocale = locale;
  applyLocaleToDocument(locale);
  if (options?.persist !== false) {
    try {
      localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    } catch {
      // ignore storage issues
    }
  }
}

export function t(key: string, params?: TranslationParams): string {
  const entry = translations[currentLocale][key] ?? translations[FALLBACK_LOCALE][key];
  if (entry === undefined) return key;
  const mergedParams: TranslationParams = { ...(params ?? {}), appName: appName() };
  if (typeof entry === "function") return entry(mergedParams);
  return entry.replace(/\{([a-zA-Z0-9_]+)\}/g, (_match, name: string) => {
    const value = mergedParams[name];
    return value == null ? "" : String(value);
  });
}
