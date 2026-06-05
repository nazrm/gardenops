import { t } from "../core/i18n";
import type { MediaAsset } from "../services/api";

type MediaGalleryModule = typeof import("./mediaGallery");
type LightboxItem = { src: string; label: string };
type RenderMediaGalleryOptions = Parameters<MediaGalleryModule["renderMediaGallery"]>[1];
type RenderPendingMediaPickerOptions = Parameters<MediaGalleryModule["renderPendingMediaPicker"]>[1];

let mediaGalleryModulePromise: Promise<MediaGalleryModule> | null = null;

function loadMediaGalleryModule(): Promise<MediaGalleryModule> {
  mediaGalleryModulePromise ??= import("./mediaGallery")
    .catch((err) => {
      mediaGalleryModulePromise = null;
      throw err;
    });
  return mediaGalleryModulePromise;
}

export function openMediaLightboxLazy(src: string, label: string): void;
export function openMediaLightboxLazy(
  items: LightboxItem[],
  startIndex: number,
): void;
export function openMediaLightboxLazy(
  srcOrItems: string | LightboxItem[],
  labelOrIndex: string | number,
): void {
  void loadMediaGalleryModule()
    .then((mod) => {
      if (typeof srcOrItems === "string") {
        mod.openMediaLightbox(srcOrItems, labelOrIndex as string);
        return;
      }
      mod.openMediaLightbox(srcOrItems, labelOrIndex as number);
    })
    .catch((err) => {
      console.error("Failed to load media viewer", err);
    });
}

export function createLazyMediaThumbnailButton(
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
    openMediaLightboxLazy(asset.original_url, label);
  });

  const img = document.createElement("img");
  img.className = options.imageClassName || "media-inline-thumb-image";
  img.src = asset.preview_url;
  img.alt = label;
  img.loading = "lazy";
  button.appendChild(img);
  return button;
}

export async function renderMediaGalleryLazy(
  container: HTMLElement,
  options: RenderMediaGalleryOptions,
): Promise<void> {
  const mod = await loadMediaGalleryModule();
  mod.renderMediaGallery(container, options);
}

export async function renderPendingMediaPickerLazy(
  container: HTMLElement,
  options: RenderPendingMediaPickerOptions,
): Promise<void> {
  const mod = await loadMediaGalleryModule();
  mod.renderPendingMediaPicker(container, options);
}
