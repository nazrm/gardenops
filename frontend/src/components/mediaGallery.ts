import { t } from "../core/i18n";
import type { MediaAsset } from "../services/api";
import { trapFocus } from "./dialogCore";

const _pendingPreviewUrls = new WeakMap<HTMLElement, string[]>();

function clearObjectUrls(container: HTMLElement): void {
  const urls = _pendingPreviewUrls.get(container) ?? [];
  for (const url of urls) {
    URL.revokeObjectURL(url);
  }
  _pendingPreviewUrls.delete(container);
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (bytes >= 1024) {
    return `${Math.round(bytes / 1024)} KB`;
  }
  return `${bytes} B`;
}

export function openMediaLightbox(src: string, label: string): void;
export function openMediaLightbox(
  items: Array<{ src: string; label: string }>,
  startIndex: number,
): void;
export function openMediaLightbox(
  srcOrItems: string | Array<{ src: string; label: string }>,
  labelOrIndex: string | number,
): void {
  const items: Array<{ src: string; label: string }> =
    typeof srcOrItems === "string"
      ? [{ src: srcOrItems, label: labelOrIndex as string }]
      : srcOrItems;
  let currentIndex = typeof labelOrIndex === "number" ? labelOrIndex : 0;
  const isGallery = items.length > 1;

  if (items.length === 0) return;
  const initial = items[currentIndex] ?? items[0]!;

  const prevOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";

  const overlay = document.createElement("div");
  overlay.className = "media-lightbox";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute(
    "aria-label",
    initial.label || t("media.lightbox_title"),
  );

  const frame = document.createElement("div");
  frame.className = "media-lightbox-frame";

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "media-lightbox-close";
  closeBtn.textContent = "\u00d7";
  closeBtn.setAttribute("aria-label", t("media.close_viewer"));

  const img = document.createElement("img");
  img.className = "media-lightbox-image";
  img.src = initial.src;
  img.alt = initial.label || t("media.lightbox_title");

  const caption = document.createElement("div");
  caption.className = "media-lightbox-caption";
  caption.textContent = initial.label || t("media.lightbox_title");

  // Navigation buttons (only for gallery mode)
  const prevBtn = document.createElement("button");
  prevBtn.type = "button";
  prevBtn.className = "media-lightbox-prev";
  prevBtn.setAttribute("aria-label", "Previous");
  prevBtn.textContent = "\u2039"; // ‹
  prevBtn.hidden = !isGallery;

  const nextBtn = document.createElement("button");
  nextBtn.type = "button";
  nextBtn.className = "media-lightbox-next";
  nextBtn.setAttribute("aria-label", "Next");
  nextBtn.textContent = "\u203A"; // ›
  nextBtn.hidden = !isGallery;

  // Counter
  const counter = document.createElement("span");
  counter.className = "media-lightbox-counter";

  frame.append(closeBtn, prevBtn, img, nextBtn, caption);
  caption.appendChild(counter);
  overlay.appendChild(frame);
  document.body.appendChild(overlay);

  const releaseFocusTrap = trapFocus(overlay);

  function goTo(index: number): void {
    if (index < 0 || index >= items.length) return;
    const item = items[index];
    if (!item) return;
    currentIndex = index;
    img.src = item.src;
    img.alt = item.label || t("media.lightbox_title");
    caption.textContent = item.label || t("media.lightbox_title");
    caption.appendChild(counter);
    prevBtn.hidden = !isGallery || currentIndex === 0;
    nextBtn.hidden = !isGallery || currentIndex === items.length - 1;
    counter.textContent = isGallery
      ? `${currentIndex + 1} / ${items.length}`
      : "";
  }

  prevBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    goTo(currentIndex - 1);
  });
  nextBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    goTo(currentIndex + 1);
  });

  goTo(currentIndex);

  const close = () => {
    if (img.src.startsWith("blob:")) {
      URL.revokeObjectURL(img.src);
    }
    releaseFocusTrap();
    document.body.style.overflow = prevOverflow;
    overlay.remove();
    window.removeEventListener("keydown", onKey);
  };
  const onKey = (event: KeyboardEvent) => {
    if (event.key === "Escape") close();
    if (event.key === "ArrowRight" && isGallery) goTo(currentIndex + 1);
    if (event.key === "ArrowLeft" && isGallery) goTo(currentIndex - 1);
  };
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });
  window.addEventListener("keydown", onKey);

  let touchStartX = 0;
  let touchStartY = 0;
  let lockedAxis: "x" | "y" | null = null;
  let touchDeltaX = 0;
  let touchDeltaY = 0;

  overlay.addEventListener("touchstart", (e) => {
    const touch = e.touches[0];
    if (!touch) return;
    touchStartX = touch.clientX;
    touchStartY = touch.clientY;
    lockedAxis = null;
    touchDeltaX = 0;
    touchDeltaY = 0;
    frame.style.transition = "none";
  }, { passive: true });

  overlay.addEventListener("touchmove", (e) => {
    const touch = e.touches[0];
    if (!touch) return;
    const dx = touch.clientX - touchStartX;
    const dy = touch.clientY - touchStartY;
    if (!lockedAxis && (Math.abs(dx) > 10 || Math.abs(dy) > 10)) {
      lockedAxis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
    }
    if (lockedAxis === "y" && dy > 0) {
      e.preventDefault();
      touchDeltaY = dy;
      frame.style.transform = `translateY(${dy}px)`;
      frame.style.opacity = String(Math.max(0, 1 - dy / 300));
    }
    if (lockedAxis === "x") {
      e.preventDefault();
      touchDeltaX = dx;
      frame.style.transform = `translateX(${dx}px)`;
    }
  }, { passive: false });

  overlay.addEventListener("touchend", () => {
    frame.style.transition = "";
    if (lockedAxis === "y" && touchDeltaY > 100) {
      close();
    } else if (lockedAxis === "x" && isGallery) {
      if (touchDeltaX < -80) goTo(currentIndex + 1);
      else if (touchDeltaX > 80) goTo(currentIndex - 1);
    }
    frame.style.transform = "";
    frame.style.opacity = "";
    lockedAxis = null;
  });
}

export function createMediaThumbnailButton(
  asset: MediaAsset,
  options: {
    className?: string;
    imageClassName?: string;
    label?: string;
  } = {},
): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = options.className || "media-inline-thumb";
  const label = options.label || asset.original_filename || t("media.lightbox_title");
  button.setAttribute("aria-label", label);
  button.addEventListener("click", () => {
    openMediaLightbox(asset.original_url, label);
  });

  const img = document.createElement("img");
  img.className = options.imageClassName || "media-inline-thumb-image";
  img.src = asset.preview_url;
  img.alt = label;
  img.loading = "lazy";
  button.appendChild(img);
  return button;
}

function appendProgress(container: HTMLElement, progressPct: number | null | undefined): void {
  if (progressPct === null || progressPct === undefined) return;
  const row = document.createElement("div");
  row.className = "media-progress";
  const bar = document.createElement("div");
  bar.className = "media-progress-bar";
  bar.style.width = `${Math.max(0, Math.min(100, progressPct))}%`;
  const label = document.createElement("span");
  label.className = "media-progress-label";
  label.textContent = t("media.upload_progress", { percent: Math.round(progressPct) });
  row.append(bar, label);
  container.appendChild(row);
}

function createFileIntake(options: {
  canUpload: boolean;
  onFilesSelected?: (files: File[]) => void;
}): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "media-intake";
  if (!options.canUpload) {
    wrap.hidden = true;
    return wrap;
  }
  const dropZone = document.createElement("label");
  dropZone.className = "media-dropzone";
  dropZone.tabIndex = 0;

  const title = document.createElement("span");
  title.className = "media-dropzone-title";
  title.textContent = t("media.add_photos");

  const hint = document.createElement("span");
  hint.className = "media-dropzone-hint";
  hint.textContent = t("media.drop_hint");

  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/*";
  input.multiple = true;
  input.className = "media-file-input";
  input.addEventListener("change", () => {
    const files = Array.from(input.files ?? []);
    if (files.length > 0) options.onFilesSelected?.(files);
    input.value = "";
  });

  const prevent = (event: DragEvent) => {
    event.preventDefault();
    event.stopPropagation();
  };
  dropZone.addEventListener("dragover", (event) => {
    prevent(event);
    dropZone.classList.add("media-dropzone-active");
  });
  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("media-dropzone-active");
  });
  dropZone.addEventListener("drop", (event) => {
    prevent(event);
    dropZone.classList.remove("media-dropzone-active");
    const files = Array.from(event.dataTransfer?.files ?? []);
    if (files.length > 0) options.onFilesSelected?.(files);
  });
  dropZone.append(title, hint, input);
  wrap.appendChild(dropZone);
  return wrap;
}

export function renderMediaGallery(
  container: HTMLElement,
  options: {
    assets: MediaAsset[];
    emptyText: string;
    canUpload: boolean;
    onFilesSelected?: (files: File[]) => void;
    onSetCoverAsset?: (asset: MediaAsset) => void;
    setCoverLabel?: string;
    onDeleteAsset?: (asset: MediaAsset) => void;
    deleteLabel?: string;
    onDeleteEverywhereAsset?: (asset: MediaAsset) => void;
    deleteEverywhereLabel?: string;
    uploadProgressPct?: number | null;
  },
): void {
  clearObjectUrls(container);
  container.replaceChildren();
  container.classList.add("media-gallery");
  const intakeOptions: {
    canUpload: boolean;
    onFilesSelected?: (files: File[]) => void;
  } = { canUpload: options.canUpload };
  if (options.onFilesSelected) {
    intakeOptions.onFilesSelected = options.onFilesSelected;
  }
  container.appendChild(createFileIntake(intakeOptions));
  appendProgress(container, options.uploadProgressPct);

  if (options.assets.length === 0) {
    const empty = document.createElement("p");
    empty.className = "media-empty";
    empty.textContent = options.emptyText;
    container.appendChild(empty);
    return;
  }

  const lightboxItems = options.assets.map((a) => ({
    src: a.original_url,
    label: a.original_filename || t("media.lightbox_title"),
  }));

  const grid = document.createElement("div");
  grid.className = "media-grid";
  options.assets.forEach((asset, assetIndex) => {
    const card = document.createElement("div");
    card.className = "media-card";

    const thumbBtn = document.createElement("button");
    thumbBtn.type = "button";
    thumbBtn.className = "media-thumb-button";
    thumbBtn.addEventListener("click", () => {
      openMediaLightbox(lightboxItems, assetIndex);
    });

    const img = document.createElement("img");
    img.className = "media-thumb";
    img.src = asset.preview_url;
    img.alt = asset.original_filename || t("media.lightbox_title");
    img.loading = "lazy";
    thumbBtn.appendChild(img);

    const meta = document.createElement("div");
    meta.className = "media-meta";

    const name = document.createElement("div");
    name.className = "media-name";
    name.textContent = asset.original_filename || t("media.untitled");

    const detail = document.createElement("div");
    detail.className = "media-detail";
    detail.textContent = `${asset.width}\u00d7${asset.height} • ${formatBytes(asset.bytes)}`;

    meta.append(name, detail);
    if (asset.is_cover) {
      const coverNote = document.createElement("div");
      coverNote.className = "media-cover-note";
      coverNote.textContent = t("media.cover_badge");
      meta.appendChild(coverNote);
    }
    if (asset.targets.length > 1) {
      const shareNote = document.createElement("div");
      shareNote.className = "media-share-note";
      shareNote.textContent = t("media.shared_count", { count: asset.targets.length });
      meta.appendChild(shareNote);
    }
    card.append(thumbBtn, meta);

    if (
      options.onSetCoverAsset
      || options.onDeleteAsset
      || (options.onDeleteEverywhereAsset && asset.targets.length > 1)
    ) {
      const actions = document.createElement("div");
      actions.className = "media-card-actions";
      if (options.onSetCoverAsset && !asset.is_cover) {
        const setCoverBtn = document.createElement("button");
        setCoverBtn.type = "button";
        setCoverBtn.className = "media-set-cover";
        setCoverBtn.textContent = options.setCoverLabel || t("media.set_cover");
        setCoverBtn.addEventListener("click", () => options.onSetCoverAsset?.(asset));
        actions.appendChild(setCoverBtn);
      }
      if (options.onDeleteAsset) {
        const deleteBtn = document.createElement("button");
        deleteBtn.type = "button";
        deleteBtn.className = "media-delete";
        deleteBtn.textContent = options.deleteLabel || t("common.remove");
        deleteBtn.addEventListener("click", () => options.onDeleteAsset?.(asset));
        actions.appendChild(deleteBtn);
      }
      if (options.onDeleteEverywhereAsset && asset.targets.length > 1) {
        const deleteEverywhereBtn = document.createElement("button");
        deleteEverywhereBtn.type = "button";
        deleteEverywhereBtn.className = "media-delete media-delete-everywhere";
        deleteEverywhereBtn.textContent = options.deleteEverywhereLabel || t("media.delete_everywhere");
        deleteEverywhereBtn.addEventListener("click", () => options.onDeleteEverywhereAsset?.(asset));
        actions.appendChild(deleteEverywhereBtn);
      }
      card.appendChild(actions);
    }

    grid.appendChild(card);
  });
  container.appendChild(grid);
}

export function renderPendingMediaPicker(
  container: HTMLElement,
  options: {
    files: File[];
    emptyText: string;
    onFilesSelected?: (files: File[]) => void;
    onRemoveFile?: (index: number) => void;
    uploadProgressPct?: number | null;
  },
): void {
  clearObjectUrls(container);
  container.replaceChildren();
  container.classList.add("media-gallery");
  const intakeOptions: {
    canUpload: boolean;
    onFilesSelected?: (files: File[]) => void;
  } = { canUpload: true };
  if (options.onFilesSelected) {
    intakeOptions.onFilesSelected = options.onFilesSelected;
  }
  container.appendChild(createFileIntake(intakeOptions));
  appendProgress(container, options.uploadProgressPct);

  if (options.files.length === 0) {
    const empty = document.createElement("p");
    empty.className = "media-empty";
    empty.textContent = options.emptyText;
    container.appendChild(empty);
    return;
  }

  const urls: string[] = options.files.map((f) => URL.createObjectURL(f));
  const pendingLightboxItems = options.files.map((file, i) => ({
    src: urls[i]!,
    label: file.name || t("media.lightbox_title"),
  }));

  const grid = document.createElement("div");
  grid.className = "media-grid";
  options.files.forEach((file, index) => {
    const url = urls[index]!;
    const card = document.createElement("div");
    card.className = "media-card";

    const thumbBtn = document.createElement("button");
    thumbBtn.type = "button";
    thumbBtn.className = "media-thumb-button";
    thumbBtn.addEventListener("click", () => {
      openMediaLightbox(pendingLightboxItems, index);
    });

    const img = document.createElement("img");
    img.className = "media-thumb";
    img.src = url;
    img.alt = file.name || t("media.lightbox_title");
    thumbBtn.appendChild(img);

    const meta = document.createElement("div");
    meta.className = "media-meta";

    const name = document.createElement("div");
    name.className = "media-name";
    name.textContent = file.name || t("media.untitled");

    const detail = document.createElement("div");
    detail.className = "media-detail";
    detail.textContent = formatBytes(file.size);

    meta.append(name, detail);
    card.append(thumbBtn, meta);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "media-delete";
    removeBtn.textContent = t("media.remove_pending");
    removeBtn.addEventListener("click", () => options.onRemoveFile?.(index));
    card.appendChild(removeBtn);

    grid.appendChild(card);
  });
  _pendingPreviewUrls.set(container, urls);
  container.appendChild(grid);
}
