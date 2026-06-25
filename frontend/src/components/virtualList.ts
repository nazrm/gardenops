interface VirtualListOptions<T> {
  container: HTMLElement;
  items: T[];
  estimateItemHeight: number;
  overscan?: number;
  createItem: (item: T, index: number) => HTMLElement;
  renderEmpty: () => void;
}

const virtualListCleanups = new WeakMap<HTMLElement, () => void>();

function cleanupVirtualList(container: HTMLElement): void {
  virtualListCleanups.get(container)?.();
  virtualListCleanups.delete(container);
}

function spacer(height: number): HTMLElement {
  const element = document.createElement("div");
  element.className = "virtual-list-spacer";
  element.setAttribute("aria-hidden", "true");
  element.style.height = `${Math.max(0, Math.round(height))}px`;
  return element;
}

function visibleRange(
  scrollTop: number,
  viewportHeight: number,
  itemHeight: number,
  itemCount: number,
  overscan: number,
): { start: number; end: number } {
  const start = Math.max(0, Math.floor(scrollTop / itemHeight) - overscan);
  const end = Math.min(
    itemCount,
    Math.ceil((scrollTop + Math.max(1, viewportHeight)) / itemHeight) + overscan,
  );
  return { start, end: Math.max(start, end) };
}

export function clearVirtualList(container: HTMLElement): void {
  cleanupVirtualList(container);
  container.classList.remove("virtual-list-wrap");
  container.removeAttribute("data-render-mode");
  container.removeAttribute("data-render-ready");
  container.removeAttribute("data-render-complete");
  container.removeAttribute("data-rendered-items");
  container.removeAttribute("data-virtual-start");
  container.removeAttribute("data-virtual-end");
  container.replaceChildren();
}

export function renderVirtualList<T>(options: VirtualListOptions<T>): void {
  const {
    container,
    items,
    estimateItemHeight,
    overscan = 4,
    createItem,
    renderEmpty,
  } = options;
  cleanupVirtualList(container);
  container.classList.add("virtual-list-wrap");
  container.dataset["renderMode"] = "virtual";
  container.dataset["renderReady"] = "false";
  container.dataset["renderComplete"] = "true";

  if (items.length === 0) {
    container.replaceChildren();
    renderEmpty();
    container.dataset["renderReady"] = "true";
    container.dataset["renderedItems"] = "0";
    container.dataset["virtualStart"] = "0";
    container.dataset["virtualEnd"] = "0";
    return;
  }

  let animationFrame = 0;
  const itemHeight = Math.max(1, estimateItemHeight);

  const render = (): void => {
    animationFrame = 0;
    const { start, end } = visibleRange(
      container.scrollTop,
      container.clientHeight,
      itemHeight,
      items.length,
      overscan,
    );
    const elements: HTMLElement[] = [];
    const topHeight = start * itemHeight;
    const bottomHeight = (items.length - end) * itemHeight;
    if (topHeight > 0) elements.push(spacer(topHeight));
    for (let index = start; index < end; index += 1) {
      const element = createItem(items[index]!, index);
      element.dataset["virtualItem"] = "true";
      elements.push(element);
    }
    if (bottomHeight > 0) elements.push(spacer(bottomHeight));
    container.replaceChildren(...elements);
    container.dataset["renderReady"] = "true";
    container.dataset["renderedItems"] = String(end - start);
    container.dataset["virtualStart"] = String(start);
    container.dataset["virtualEnd"] = String(end);
  };

  const scheduleRender = (): void => {
    if (animationFrame !== 0) return;
    animationFrame = window.requestAnimationFrame(render);
  };

  container.addEventListener("scroll", scheduleRender, { passive: true });

  let resizeObserver: ResizeObserver | null = null;
  if (typeof globalThis.ResizeObserver !== "undefined") {
    resizeObserver = new globalThis.ResizeObserver(() => scheduleRender());
    resizeObserver.observe(container);
  } else {
    globalThis.addEventListener("resize", scheduleRender);
  }

  virtualListCleanups.set(container, () => {
    container.removeEventListener("scroll", scheduleRender);
    if (animationFrame !== 0) {
      window.cancelAnimationFrame(animationFrame);
      animationFrame = 0;
    }
    if (resizeObserver) {
      resizeObserver.disconnect();
    } else {
      globalThis.removeEventListener("resize", scheduleRender);
    }
  });

  render();
}
