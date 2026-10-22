import type { CalendarViewMode } from "../core/models";
import { t } from "../core/i18n";
import { formatLocalDate } from "../features/taskSnoozePolicy";

interface RenderedEvent {
  id: string;
  title: string;
  start: string;
  end: string;
  className: string;
}

export interface CalendarEventDisplay {
  event: RenderedEvent;
  view: { type: CalendarViewMode };
}

export interface CalendarEventMount extends CalendarEventDisplay {
  el: HTMLElement;
}

interface CalendarOptions {
  initialView: CalendarViewMode;
  locale: string;
  eventOrder: (left: RenderedEvent, right: RenderedEvent) => number;
  eventContent: (event: CalendarEventDisplay) => { domNodes: Node[] } | { text: string };
  eventDidMount: (event: CalendarEventMount) => void;
  eventClick: (event: CalendarEventDisplay) => void;
  datesSet: (arg: { view: { type: CalendarViewMode; title: string } }) => void;
  events: (
    range: { startStr: string; endStr: string },
    success: (events: RenderedEvent[]) => void,
  ) => Promise<void>;
  noEventsContent: () => string;
}

function localDay(year: number, month: number, day: number): Date {
  // Noon and calendar arithmetic avoid UTC/DST shifting date-only garden events.
  return new Date(year, month, day, 12);
}

function offsetDay(date: Date, days: number): Date {
  return localDay(date.getFullYear(), date.getMonth(), date.getDate() + days);
}

/** Date-only garden views. Scheduling, filtering and mutations stay in calendarTab. */
export class GardenCalendar {
  private date = localDay(new Date().getFullYear(), new Date().getMonth(), new Date().getDate());
  private mode: CalendarViewMode;
  private events: RenderedEvent[] = [];
  private generation = 0;
  private focusedDay: string;

  constructor(private root: HTMLElement, private options: CalendarOptions) {
    this.mode = options.initialView;
    this.focusedDay = formatLocalDate(this.date);
  }

  get view(): { type: CalendarViewMode; activeStart: Date; activeEnd: Date; title: string } {
    const year = this.date.getFullYear();
    const month = this.date.getMonth();
    let start = localDay(year, month, 1);
    let end = localDay(year, month + 1, 1);
    if (this.mode === "week") {
      start = offsetDay(this.date, -((this.date.getDay() + 6) % 7));
      end = offsetDay(start, 7);
    } else if (this.mode === "month") {
      start = offsetDay(start, -((start.getDay() + 6) % 7));
      end = offsetDay(start, 42);
    }
    const formatter = new Intl.DateTimeFormat(this.options.locale, this.mode === "week"
      ? { day: "numeric", month: "short", year: "numeric" }
      : { month: "long", year: "numeric" });
    const title = this.mode === "week"
      ? formatter.formatRange(start, offsetDay(end, -1))
      : formatter.format(this.date);
    return { type: this.mode, activeStart: start, activeEnd: end, title };
  }

  getDate(): Date { return new Date(this.date); }

  render(): void { this.refresh(); }

  changeView(mode: CalendarViewMode): void {
    this.mode = mode;
    this.refresh();
  }

  setLocale(locale: string): void {
    this.options.locale = locale;
    this.options.datesSet({ view: this.view });
    this.paint();
  }

  prev(): void { this.navigate(-1); }
  next(): void { this.navigate(1); }

  today(): void {
    const now = new Date();
    this.date = localDay(now.getFullYear(), now.getMonth(), now.getDate());
    this.focusedDay = formatLocalDate(this.date);
    this.refresh();
  }

  private navigate(direction: number): void {
    if (this.mode === "week") this.date = offsetDay(this.date, direction * 7);
    else {
      const nextMonth = localDay(this.date.getFullYear(), this.date.getMonth() + direction, 1);
      const lastDay = localDay(nextMonth.getFullYear(), nextMonth.getMonth() + 1, 0).getDate();
      this.date = localDay(nextMonth.getFullYear(), nextMonth.getMonth(), Math.min(this.date.getDate(), lastDay));
    }
    this.focusedDay = formatLocalDate(this.date);
    this.refresh();
  }

  private refresh(): void {
    this.options.datesSet({ view: this.view });
    this.events = [];
    this.paint();
    this.refetchEvents();
  }

  refetchEvents(): void {
    const generation = ++this.generation;
    const view = this.view;
    void this.options.events({
      startStr: formatLocalDate(view.activeStart),
      endStr: formatLocalDate(view.activeEnd),
    }, (events) => {
      if (generation !== this.generation) return;
      this.events = events.slice().sort(this.options.eventOrder);
      this.paint();
    });
  }

  removeAllEvents(): void {
    ++this.generation;
    this.events = [];
    this.paint();
  }

  removeEvent(id: string): void {
    this.events = this.events.filter(event => event.id !== id);
    this.paint();
  }

  private eventButton(event: RenderedEvent): HTMLButtonElement {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `${event.className} garden-calendar-${this.mode === "agenda" ? "list" : "grid"}-event`;
    const info = { event, view: { type: this.mode } };
    const content = document.createElement("span");
    content.className = "garden-calendar-event-inner";
    const rendered = this.options.eventContent(info);
    if ("domNodes" in rendered) content.append(...rendered.domNodes);
    else content.textContent = rendered.text;
    button.append(content);
    button.addEventListener("click", () => this.options.eventClick(info));
    this.options.eventDidMount({ ...info, el: button });
    return button;
  }

  private eventsFor(day: string): RenderedEvent[] {
    return this.events.filter(event => event.start <= day && day < event.end);
  }

  private paint(): void {
    const active = document.activeElement instanceof HTMLElement && this.root.contains(document.activeElement)
      ? document.activeElement : null;
    const eventId = active?.dataset["calendarEventId"];
    const dayId = active?.dataset["calendarDay"];
    const view = this.view;
    if (this.focusedDay < formatLocalDate(view.activeStart)
      || this.focusedDay >= formatLocalDate(view.activeEnd)) {
      this.focusedDay = formatLocalDate(this.date);
    }
    const fragment = document.createDocumentFragment();
    const fullDate = new Intl.DateTimeFormat(this.options.locale, { dateStyle: "full" });
    if (this.mode === "agenda") {
      for (let day = view.activeStart; day < view.activeEnd; day = offsetDay(day, 1)) {
        const events = this.eventsFor(formatLocalDate(day));
        if (!events.length) continue;
        const heading = document.createElement("h3");
        heading.className = "garden-calendar-list-heading";
        heading.textContent = fullDate.format(day);
        fragment.append(heading, ...events.map(event => this.eventButton(event)));
      }
      if (!fragment.childNodes.length) {
        const empty = document.createElement("p");
        empty.textContent = this.options.noEventsContent();
        fragment.append(empty);
      }
    } else {
      const table = document.createElement("table");
      table.className = "garden-calendar-table";
      const caption = table.createCaption();
      caption.className = "sr-only";
      caption.textContent = view.title;
      const header = table.createTHead().insertRow();
      const weekday = new Intl.DateTimeFormat(this.options.locale, { weekday: "short" });
      for (let index = 0; index < 7; index++) {
        const cell = document.createElement("th");
        cell.scope = "col";
        cell.className = "garden-calendar-day-header garden-calendar-day-heading";
        cell.textContent = weekday.format(offsetDay(view.activeStart, index));
        header.append(cell);
      }
      const body = table.createTBody();
      let row = body.insertRow();
      let index = 0;
      for (let day = view.activeStart; day < view.activeEnd; day = offsetDay(day, 1), index++) {
        if (index && index % 7 === 0) row = body.insertRow();
        const cell = row.insertCell();
        const key = formatLocalDate(day);
        cell.dataset["date"] = key;
        cell.className = "garden-calendar-day-cell";
        cell.classList.toggle("calendar-other-month", day.getMonth() !== this.date.getMonth());
        if (key === formatLocalDate(new Date())) cell.classList.add("calendar-day-today");
        const dateButton = document.createElement("button");
        dateButton.type = "button";
        dateButton.className = "garden-calendar-day-number";
        dateButton.dataset["calendarDay"] = key;
        dateButton.textContent = String(day.getDate());
        dateButton.setAttribute("aria-label", fullDate.format(day));
        dateButton.setAttribute("aria-pressed", String(key === this.focusedDay));
        if (key === formatLocalDate(new Date())) dateButton.setAttribute("aria-current", "date");
        dateButton.tabIndex = key === this.focusedDay ? 0 : -1;
        const selectedDay = new Date(day);
        dateButton.addEventListener("click", () => {
          this.date = selectedDay;
          this.focusedDay = key;
          this.refresh();
        });
        dateButton.addEventListener("keydown", event => {
          const offset = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7,
            Home: -((selectedDay.getDay() + 6) % 7), End: 6 - ((selectedDay.getDay() + 6) % 7) }[event.key];
          if (offset === undefined) return;
          event.preventDefault();
          const next = offsetDay(selectedDay, offset);
          this.focusedDay = formatLocalDate(next);
          if (next < view.activeStart || next >= view.activeEnd) {
            this.date = next;
            this.refresh();
          } else this.paint();
          this.root.querySelector<HTMLElement>(`[data-calendar-day="${this.focusedDay}"]`)?.focus();
        });
        cell.append(dateButton);
        const events = this.eventsFor(key);
        const limit = window.matchMedia("(max-width: 760px)").matches ? 3 : 4;
        cell.append(...events.slice(0, limit).map(event => this.eventButton(event)));
        if (events.length > limit) {
          const more = document.createElement("details");
          const summary = document.createElement("summary");
          summary.textContent = t("calendar.more_events", { count: events.length - limit });
          summary.setAttribute("aria-label", `${summary.textContent}: ${fullDate.format(day)}`);
          more.append(summary, ...events.slice(limit).map(event => this.eventButton(event)));
          cell.append(more);
        }
      }
      fragment.append(table);
    }
    this.root.replaceChildren(fragment);
    if (active) {
      const selector = eventId ? `[data-calendar-event-id="${CSS.escape(eventId)}"]`
        : dayId ? `[data-calendar-day="${dayId}"]` : null;
      const target = selector ? this.root.querySelector<HTMLElement>(selector) : null;
      const details = target?.closest("details");
      if (details) details.open = true;
      target?.focus({ preventScroll: true });
    }
  }
}
