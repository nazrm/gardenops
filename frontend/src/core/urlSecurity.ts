const INVITE_PARAM = "invite";

let primedInviteToken = "";

function hashParams(rawHash: string): URLSearchParams {
  const value = rawHash.startsWith("#") ? rawHash.slice(1) : rawHash;
  return new URLSearchParams(value);
}

function currentUrl(baseHref: string): URL {
  return new URL(baseHref, window.location.origin);
}

function hasInviteTokenInLocation(
  locationLike: Pick<Location, "search" | "hash"> = window.location,
): boolean {
  return hashParams(locationLike.hash).has(INVITE_PARAM)
    || new URLSearchParams(locationLike.search).has(INVITE_PARAM);
}

function scrubInviteTokenFromHref(rawHref: string): string {
  const url = currentUrl(rawHref);
  url.searchParams.delete(INVITE_PARAM);
  const params = hashParams(url.hash);
  params.delete(INVITE_PARAM);
  const nextHash = params.toString();
  url.hash = nextHash ? `#${nextHash}` : "";
  return `${url.pathname}${url.search}${url.hash}`;
}

export function buildInvitationLink(inviteToken: string, baseHref = window.location.href): string {
  const url = currentUrl(baseHref);
  url.searchParams.delete(INVITE_PARAM);
  const params = hashParams(url.hash);
  params.set(INVITE_PARAM, inviteToken);
  url.hash = params.toString();
  return url.toString();
}

export function readInviteTokenFromLocation(
  locationLike: Pick<Location, "search" | "hash"> = window.location,
): string {
  return hashParams(locationLike.hash).get(INVITE_PARAM)?.trim() ?? "";
}

export function primeInviteTokenFromLocation(): string {
  const shouldScrub = hasInviteTokenInLocation();
  if (!primedInviteToken) {
    primedInviteToken = readInviteTokenFromLocation();
  }
  if (shouldScrub) {
    window.history.replaceState(
      {},
      document.title,
      scrubInviteTokenFromHref(window.location.href),
    );
  }
  return primedInviteToken;
}

export function peekPrimedInviteToken(): string {
  return primedInviteToken || readInviteTokenFromLocation();
}

export function clearPrimedInviteToken(): void {
  primedInviteToken = "";
}

export function redactedLocationPath(rawHref: string): string {
  try {
    const url = currentUrl(rawHref);
    return url.pathname || "/";
  } catch {
    return window.location.pathname || "/";
  }
}
