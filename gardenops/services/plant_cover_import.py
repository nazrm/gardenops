from __future__ import annotations

import ipaddress
import os
import re
import socket
import urllib.error
import urllib.request
import urllib.response
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from fastapi import HTTPException

from gardenops.branding import app_user_agent
from gardenops.services.media_store import (
    PreparedMediaAsset,
    allowed_media_mime_types,
    media_upload_max_bytes,
    prepare_media_asset,
)

_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
_ALLOWED_IMAGE_CONTENT_TYPES = set(allowed_media_mime_types())
_BLOCKED_IMAGE_HINTS = ("logo", "sprite", "icon", "avatar", "placeholder")
_TRUSTED_RELAXED_SOURCE_HOST_SUFFIXES = ("rhs.org", "rhs.org.uk")
_ALLOWED_SOURCE_HOST_SUFFIXES = (
    "rhs.org",
    "rhs.org.uk",
    "wikipedia.org",
    "wikimedia.org",
    "snl.no",
    "plantasjen.no",
    "planteportalen.no",
    "hageglede.no",
    "vdberk.no",
    "primaferdighekk.no",
    "impecta.no",
    "rolv.no",
)
_USER_AGENT = app_user_agent("cover-importer")


@dataclass(frozen=True)
class HtmlImageCandidate:
    url: str
    score: int


@dataclass(frozen=True)
class PreparedPlantCoverImport:
    prepared_asset: PreparedMediaAsset
    source_page_url: str
    source_image_url: str
    source_title: str


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def plant_cover_import_timeout_seconds() -> int:
    return _env_int("PLANT_COVER_IMPORT_TIMEOUT_SECONDS", 8)


def plant_cover_import_max_html_bytes() -> int:
    return _env_int("PLANT_COVER_IMPORT_MAX_HTML_BYTES", 750_000)


def plant_cover_import_max_redirects() -> int:
    return _env_int("PLANT_COVER_IMPORT_MAX_REDIRECTS", 4)


def normalize_latin_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower().replace("×", " x ")).strip()
    return re.sub(r"\s+", " ", normalized)


def latin_name_variants(latin_name: str) -> list[str]:
    tokens = normalize_latin_name(latin_name).split()
    if not tokens:
        return []
    variants: list[str] = []

    def add_variant(parts: list[str]) -> None:
        candidate = " ".join(part for part in parts if part).strip()
        if candidate and candidate not in variants:
            variants.append(candidate)

    add_variant(tokens)
    if len(tokens) >= 2:
        add_variant(tokens[:2])
    if len(tokens) >= 3:
        add_variant(tokens[:3])
        if tokens[1] == "x":
            add_variant([tokens[0], tokens[2]])
    return variants


def latin_name_matches_text(latin_name: str, candidates: Iterable[str]) -> bool:
    variants = latin_name_variants(latin_name)
    if not variants:
        return False
    for candidate in candidates:
        if not candidate:
            continue
        normalized_candidate = normalize_latin_name(candidate)
        if any(variant in normalized_candidate for variant in variants):
            return True
    return False


def _is_trusted_relaxed_source(raw_url: str) -> bool:
    hostname = (urlsplit(raw_url.strip()).hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return False
    return any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in _TRUSTED_RELAXED_SOURCE_HOST_SUFFIXES
    )


def _is_allowed_source_host(hostname: str) -> bool:
    normalized = hostname.strip().lower().rstrip(".")
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in _ALLOWED_SOURCE_HOST_SUFFIXES
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


class _PlantLinkHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.og_title = ""
        self.twitter_title = ""
        self.meta_description = ""
        self.canonical_href = ""
        self.og_image = ""
        self.twitter_image = ""
        self.h1_parts: list[str] = []
        self.text_parts: list[str] = []
        self.images: list[dict[str, str]] = []
        self._in_title = False
        self._in_h1 = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): (value or "") for key, value in attrs}
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "h1":
            self._in_h1 = True
            return
        if tag == "meta":
            prop = attrs_dict.get("property", "").lower()
            name = attrs_dict.get("name", "").lower()
            content = attrs_dict.get("content", "")
            if prop == "og:title":
                self.og_title = content
            elif prop == "og:image":
                self.og_image = content
            elif name == "twitter:title":
                self.twitter_title = content
            elif name == "twitter:image":
                self.twitter_image = content
            elif name == "description":
                self.meta_description = content
            return
        if tag == "link":
            rel = attrs_dict.get("rel", "").lower()
            if "canonical" in rel:
                self.canonical_href = attrs_dict.get("href", "")
            return
        if tag == "img":
            src = attrs_dict.get("src", "")
            if not src:
                return
            self.images.append(
                {
                    "src": src,
                    "alt": attrs_dict.get("alt", ""),
                    "width": attrs_dict.get("width", ""),
                    "height": attrs_dict.get("height", ""),
                    "class": attrs_dict.get("class", ""),
                }
            )

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title = f"{self.title} {text}".strip()
        if self._in_h1:
            self.h1_parts.append(text)
        if len(" ".join(self.text_parts)) < 10000:
            self.text_parts.append(text)


def _reject_non_public_host(hostname: str, port: int) -> None:
    try:
        addrinfo = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not resolve remote host {hostname}",
        ) from exc
    if not addrinfo:
        raise HTTPException(
            status_code=422,
            detail=f"Could not resolve remote host {hostname}",
        )
    for family, _, _, _, sockaddr in addrinfo:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global:
            raise HTTPException(
                status_code=422,
                detail="Remote host resolves to a non-public address",
            )


def _sanitize_remote_url(raw_url: str) -> str:
    split = urlsplit(raw_url.strip())
    if split.scheme.lower() != "https":
        raise HTTPException(
            status_code=422,
            detail="Only https plant links can be imported",
        )
    if not split.hostname:
        raise HTTPException(status_code=422, detail="Plant link host is missing")
    if split.username or split.password:
        raise HTTPException(
            status_code=422,
            detail="Plant links with embedded credentials are not allowed",
        )
    if not _is_allowed_source_host(split.hostname):
        raise HTTPException(
            status_code=422,
            detail="Plant link host is not in the trusted cover-source allowlist",
        )
    port = split.port
    if port is not None and port != 443:
        raise HTTPException(
            status_code=422,
            detail="Only standard HTTPS port 443 is allowed for cover import",
        )
    _reject_non_public_host(split.hostname, port or 443)
    try:
        hostname = split.hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise HTTPException(
            status_code=422,
            detail="Plant link hostname could not be normalized",
        ) from exc
    netloc = hostname
    if port is not None:
        netloc = f"{hostname}:{port}"
    path = quote(split.path or "/", safe="/%:@()+,;=-._~")
    query = quote(split.query, safe="=&;%:@()+,/%s-._~%")
    return urlunsplit((split.scheme.lower(), netloc, path, query, ""))


def _original_filename_from_url(raw_url: str, fallback: str) -> str:
    filename = unquote(urlsplit(raw_url).path.split("/")[-1]).strip()
    return filename or fallback


def _read_limited(response: urllib.response.addinfourl, limit_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        part = response.read(64 * 1024)
        if not part:
            return b"".join(chunks)
        total += len(part)
        if total > limit_bytes:
            raise HTTPException(status_code=413, detail="Remote response exceeds the allowed size")
        chunks.append(part)


def _fetch_remote_response(
    raw_url: str,
    *,
    max_bytes: int,
    accept_prefixes: tuple[str, ...],
) -> tuple[str, str, bytes]:
    current_url = _sanitize_remote_url(raw_url)
    opener = urllib.request.build_opener(_NoRedirectHandler())
    for _ in range(plant_cover_import_max_redirects() + 1):
        request = urllib.request.Request(
            current_url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": ", ".join(accept_prefixes),
            },
        )
        try:
            with opener.open(request, timeout=plant_cover_import_timeout_seconds()) as response:
                content_type = (
                    (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                )
                if accept_prefixes and not any(
                    content_type.startswith(prefix) for prefix in accept_prefixes
                ):
                    raise HTTPException(
                        status_code=415,
                        detail=f"Unexpected remote content type: {content_type or 'unknown'}",
                    )
                body = _read_limited(response, max_bytes)
                return current_url, content_type, body
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location", "").strip()
                if not location:
                    raise HTTPException(
                        status_code=422,
                        detail="Remote redirect did not include a Location header",
                    ) from exc
                current_url = _sanitize_remote_url(urljoin(current_url, location))
                continue
            raise HTTPException(
                status_code=422,
                detail=f"Remote fetch failed with HTTP {exc.code}",
            ) from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=422, detail="Remote fetch failed") from exc
    raise HTTPException(status_code=422, detail="Remote fetch exceeded redirect limit")


def _score_image_candidate(
    candidate_url: str,
    *,
    source: str,
    alt: str,
    width: str,
    height: str,
    latin_name: str,
) -> int:
    score = 0
    if source == "og":
        score += 300
    elif source == "twitter":
        score += 260
    else:
        score += 100
    normalized_url = normalize_latin_name(candidate_url)
    normalized_alt = normalize_latin_name(alt)
    variants = latin_name_variants(latin_name)
    if any(variant in normalized_url for variant in variants):
        score += 90
    if any(variant in normalized_alt for variant in variants):
        score += 60
    dims = 0
    try:
        dims = int(width or 0) * int(height or 0)
    except ValueError:
        dims = 0
    if dims >= 80_000:
        score += 30
    lowered = candidate_url.lower()
    if any(hint in lowered for hint in _BLOCKED_IMAGE_HINTS):
        score -= 120
    return score


def _collect_image_candidates(
    page_url: str,
    parser: _PlantLinkHtmlParser,
    latin_name: str,
) -> list[HtmlImageCandidate]:
    scored: dict[str, int] = {}

    def add_candidate(
        raw_url: str,
        *,
        source: str,
        alt: str = "",
        width: str = "",
        height: str = "",
    ) -> None:
        if not raw_url:
            return
        absolute = urljoin(page_url, raw_url)
        score = _score_image_candidate(
            absolute,
            source=source,
            alt=alt,
            width=width,
            height=height,
            latin_name=latin_name,
        )
        current = scored.get(absolute)
        if current is None or score > current:
            scored[absolute] = score

    add_candidate(parser.og_image, source="og")
    add_candidate(parser.twitter_image, source="twitter")
    for image in parser.images:
        add_candidate(
            image.get("src", ""),
            source="img",
            alt=image.get("alt", ""),
            width=image.get("width", ""),
            height=image.get("height", ""),
        )
    candidates = [HtmlImageCandidate(url=url, score=score) for url, score in scored.items()]
    candidates.sort(key=lambda item: (-item.score, item.url))
    return candidates


def discover_cover_from_plant_link(plant_link: str, latin_name: str) -> PreparedPlantCoverImport:
    if not latin_name.strip():
        raise HTTPException(status_code=422, detail="Plant latin name is required for cover import")
    trusted_relaxed_source = _is_trusted_relaxed_source(plant_link)
    final_url, content_type, payload = _fetch_remote_response(
        plant_link,
        max_bytes=max(plant_cover_import_max_html_bytes(), media_upload_max_bytes()),
        accept_prefixes=(*_HTML_CONTENT_TYPES, *_ALLOWED_IMAGE_CONTENT_TYPES),
    )
    variants = latin_name_variants(latin_name)
    if content_type in _ALLOWED_IMAGE_CONTENT_TYPES:
        if not trusted_relaxed_source and (
            not variants
            or not any(variant in normalize_latin_name(final_url) for variant in variants)
        ):
            raise HTTPException(
                status_code=422,
                detail="Latin name did not match the linked image URL",
            )
        prepared = prepare_media_asset(
            payload=payload,
            declared_content_type=content_type,
            original_filename=_original_filename_from_url(final_url, latin_name),
        )
        return PreparedPlantCoverImport(
            prepared_asset=prepared,
            source_page_url=final_url,
            source_image_url=final_url,
            source_title=latin_name,
        )
    if content_type not in _HTML_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Plant link did not return HTML or an allowed image",
        )

    parser = _PlantLinkHtmlParser()
    try:
        parser.feed(payload.decode("utf-8", errors="ignore"))
        parser.close()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Failed to parse the plant link page") from exc

    page_match_candidates = [
        final_url,
        parser.title,
        parser.og_title,
        parser.twitter_title,
        parser.meta_description,
        parser.canonical_href,
        " ".join(parser.h1_parts),
    ]
    if not trusted_relaxed_source and not latin_name_matches_text(
        latin_name,
        page_match_candidates,
    ):
        raise HTTPException(status_code=422, detail="Latin name did not match the linked page")

    candidates = _collect_image_candidates(final_url, parser, latin_name)
    if not candidates:
        raise HTTPException(
            status_code=422,
            detail="No usable image candidates were found on the linked page",
        )

    last_error: HTTPException | None = None
    for candidate in candidates:
        try:
            image_url, image_content_type, image_payload = _fetch_remote_response(
                candidate.url,
                max_bytes=media_upload_max_bytes(),
                accept_prefixes=tuple(_ALLOWED_IMAGE_CONTENT_TYPES),
            )
            if image_content_type not in _ALLOWED_IMAGE_CONTENT_TYPES:
                continue
            prepared = prepare_media_asset(
                payload=image_payload,
                declared_content_type=image_content_type,
                original_filename=_original_filename_from_url(image_url, latin_name),
            )
            return PreparedPlantCoverImport(
                prepared_asset=prepared,
                source_page_url=final_url,
                source_image_url=image_url,
                source_title=parser.og_title or parser.twitter_title or parser.title or latin_name,
            )
        except HTTPException as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise HTTPException(status_code=422, detail="No usable image candidates could be imported")
