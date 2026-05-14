export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function sanitizeUrl(value: string): string {
  const raw = value.trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw, window.location.origin);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
    return parsed.toString();
  } catch {
    return "";
  }
}

type ReviewedHtmlReviewClass = "escaped-dynamic" | "static-template";

type TrustedHtmlPolicy = {
  createHTML: (input: string) => unknown;
};

type TrustedTypesFactory = {
  createPolicy: (
    name: string,
    rules: { createHTML: (input: string) => string },
  ) => TrustedHtmlPolicy;
  getPolicy?: (name: string) => TrustedHtmlPolicy | null;
};

let reviewedHtmlPolicy: TrustedHtmlPolicy | null | undefined;

function reviewedHtmlValue(html: string): string | unknown {
  const trustedTypesFactory = (
    window as Window & { trustedTypes?: TrustedTypesFactory }
  ).trustedTypes;
  if (!trustedTypesFactory) return html;
  if (reviewedHtmlPolicy === undefined) {
    reviewedHtmlPolicy = trustedTypesFactory.getPolicy?.("gardenops-html") ?? null;
    if (!reviewedHtmlPolicy) {
      try {
        reviewedHtmlPolicy = trustedTypesFactory.createPolicy("gardenops-html", {
          createHTML: (value) => value,
        });
      } catch {
        reviewedHtmlPolicy = null;
      }
    }
  }
  return reviewedHtmlPolicy ? reviewedHtmlPolicy.createHTML(html) : html;
}

function setReviewedHtml(
  element: Element,
  html: string,
  _reviewClass: ReviewedHtmlReviewClass,
): void {
  // Centralized sink: callers must choose a reviewed category explicitly.
  element.innerHTML = reviewedHtmlValue(html) as string;
}

export function setEscapedHtml(element: Element, html: string): void {
  setReviewedHtml(element, html, "escaped-dynamic");
}

export function setStaticTemplateHtml(element: Element, html: string): void {
  setReviewedHtml(element, html, "static-template");
}

const INLINE_MARKDOWN_TOKEN_RE = /(\*\*[^*\r\n]+?\*\*|\*[^*\r\n]+?\*|`[^`\r\n]+?`)/g;

function appendMarkdownLine(target: DocumentFragment, line: string): void {
  let cursor = 0;
  for (const match of line.matchAll(INLINE_MARKDOWN_TOKEN_RE)) {
    const token = match[0];
    const index = match.index ?? 0;
    if (index > cursor) {
      target.append(document.createTextNode(line.slice(cursor, index)));
    }

    const text = token.startsWith("**")
      ? token.slice(2, -2)
      : token.slice(1, -1);
    if (token.startsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = text;
      target.append(strong);
    } else if (token.startsWith("*")) {
      const em = document.createElement("em");
      em.textContent = text;
      target.append(em);
    } else {
      const code = document.createElement("code");
      code.textContent = text;
      target.append(code);
    }
    cursor = index + token.length;
  }

  if (cursor < line.length) {
    target.append(document.createTextNode(line.slice(cursor)));
  }
}

export function renderMarkdownInto(element: Element, text: string): void {
  const fragment = document.createDocumentFragment();
  const lines = text.split(/\r?\n/);
  lines.forEach((line, index) => {
    if (index > 0) {
      fragment.append(document.createElement("br"));
    }
    appendMarkdownLine(fragment, line);
  });
  element.replaceChildren(fragment);
}

export function clearChildren(element: Element): void {
  element.replaceChildren();
}
