interface VirtualTableOptions<T> {
  tbody: HTMLElement;
  items: T[];
  totalColumns: number;
  estimateRowHeight: number;
  overscan?: number;
  createRow: (item: T, index: number) => HTMLTableRowElement;
  emptyRow: () => HTMLTableRowElement;
}

const virtualTableCleanups = new WeakMap<HTMLElement, () => void>();

function findScrollElement(tbody: HTMLElement): HTMLElement {
  const wrapper = tbody.closest(".table-wrap");
  if (wrapper instanceof HTMLElement) return wrapper;
  return tbody.parentElement ?? tbody;
}

function cleanupVirtualTable(tbody: HTMLElement): void {
  virtualTableCleanups.get(tbody)?.();
  virtualTableCleanups.delete(tbody);
}

function spacerRow(height: number, totalColumns: number): HTMLTableRowElement {
  const row = document.createElement("tr");
  row.className = "virtual-table-spacer";
  row.setAttribute("aria-hidden", "true");
  const cell = document.createElement("td");
  cell.colSpan = totalColumns;
  cell.style.height = `${Math.max(0, Math.round(height))}px`;
  cell.style.padding = "0";
  cell.style.border = "0";
  row.appendChild(cell);
  return row;
}

function visibleRange(
  scrollTop: number,
  viewportHeight: number,
  rowHeight: number,
  itemCount: number,
  overscan: number,
): { start: number; end: number } {
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const end = Math.min(
    itemCount,
    Math.ceil((scrollTop + Math.max(1, viewportHeight)) / rowHeight) + overscan,
  );
  return { start, end: Math.max(start, end) };
}

export function clearVirtualTableBody(tbody: HTMLElement): void {
  cleanupVirtualTable(tbody);
  tbody.removeAttribute("data-render-mode");
  tbody.removeAttribute("data-render-ready");
  tbody.removeAttribute("data-render-complete");
  tbody.removeAttribute("data-rendered-rows");
  tbody.removeAttribute("data-render-token");
  tbody.removeAttribute("data-virtual-start");
  tbody.removeAttribute("data-virtual-end");
  tbody.replaceChildren();
}

export function renderVirtualTableBody<T>(
  options: VirtualTableOptions<T>,
): void {
  const {
    tbody,
    items,
    totalColumns,
    estimateRowHeight,
    overscan = 8,
    createRow,
    emptyRow,
  } = options;
  cleanupVirtualTable(tbody);

  const scrollElement = findScrollElement(tbody);
  scrollElement.classList.add("virtual-table-wrap");
  tbody.dataset["renderMode"] = "virtual";
  tbody.dataset["renderReady"] = "false";
  tbody.dataset["renderComplete"] = "true";

  if (items.length === 0) {
    tbody.replaceChildren(emptyRow());
    tbody.dataset["renderReady"] = "true";
    tbody.dataset["renderedRows"] = "1";
    return;
  }

  let animationFrame = 0;
  const rowHeight = Math.max(1, estimateRowHeight);

  const render = (): void => {
    animationFrame = 0;
    const { start, end } = visibleRange(
      scrollElement.scrollTop,
      scrollElement.clientHeight,
      rowHeight,
      items.length,
      overscan,
    );
    const rows: HTMLTableRowElement[] = [];
    const topHeight = start * rowHeight;
    const bottomHeight = (items.length - end) * rowHeight;
    if (topHeight > 0) rows.push(spacerRow(topHeight, totalColumns));
    for (let index = start; index < end; index += 1) {
      const row = createRow(items[index]!, index);
      row.dataset["virtualRow"] = "true";
      row.style.setProperty("--virtual-row-height", `${rowHeight}px`);
      rows.push(row);
    }
    if (bottomHeight > 0) rows.push(spacerRow(bottomHeight, totalColumns));
    tbody.replaceChildren(...rows);
    tbody.dataset["renderReady"] = "true";
    tbody.dataset["renderedRows"] = String(rows.length);
    tbody.dataset["virtualStart"] = String(start);
    tbody.dataset["virtualEnd"] = String(end);
  };

  const scheduleRender = (): void => {
    if (animationFrame !== 0) return;
    animationFrame = window.requestAnimationFrame(render);
  };

  const onScroll = (): void => scheduleRender();
  scrollElement.addEventListener("scroll", onScroll, { passive: true });

  let resizeObserver: ResizeObserver | null = null;
  if (typeof globalThis.ResizeObserver !== "undefined") {
    resizeObserver = new globalThis.ResizeObserver(() => scheduleRender());
    resizeObserver.observe(scrollElement);
  } else {
    globalThis.addEventListener("resize", scheduleRender);
  }

  virtualTableCleanups.set(tbody, () => {
    scrollElement.removeEventListener("scroll", onScroll);
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
