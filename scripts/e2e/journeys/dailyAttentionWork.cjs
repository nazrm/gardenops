"use strict";

const {
  assertBrowserProfileContract,
  assertDiagnosticsClean,
  authenticate,
  createApiRecorder,
  createGuardedContext,
} = require("../completeJourneyBrowser.cjs");
const {
  assert,
  assertFocusInside,
  assertPageStructure,
  visible,
  waitFor,
} = require("../completeJourneyAssertions.cjs");

const EDITOR_PASSWORD = "CompleteJourneysEditorE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const VIEWER_PASSWORD = "CompleteJourneysViewerE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture

function taskTitle(fixture, key) {
  const taskId = fixture.phase_two?.task_ids?.[key];
  const title = fixture.phase_two?.task_titles?.[taskId];
  assert(taskId && title, `Missing Phase 2 task fixture: ${key}`);
  return { id: taskId, title };
}

function taskCard(page, title) {
  return page.locator("#tasks-list .task-card").filter({ hasText: title }).first();
}

async function freezeBrowserClock(context, nowMs) {
  await context.addInitScript((frozenNowMs) => {
    const RealDate = Date;
    function FrozenDate(...args) {
      if (new.target) {
        return args.length === 0 ? new RealDate(frozenNowMs) : new RealDate(...args);
      }
      return new RealDate(frozenNowMs).toString();
    }
    Object.setPrototypeOf(FrozenDate, RealDate);
    FrozenDate.prototype = RealDate.prototype;
    FrozenDate.now = () => frozenNowMs;
    FrozenDate.parse = RealDate.parse;
    FrozenDate.UTC = RealDate.UTC;
    globalThis.Date = FrozenDate;
  }, nowMs);
}

async function closeMobileUtility(page) {
  if (!await page.locator("body.mobile-utility-open").count()) return;
  await page.locator("#mobile-utility-close-btn").click();
  await waitFor(
    async () => !await page.locator("body.mobile-utility-open").count(),
    "mobile utility to close",
  );
}

async function selectGarden(page, profile, gardenId, { waitForSettle = true } = {}) {
  if (profile === "mobile") {
    if (!await page.locator("body.mobile-utility-open").count()) {
      await page.locator("#mobile-utility-btn").click();
    }
  }
  const selector = page.locator(profile === "mobile" ? "#mobile-garden-select" : "#garden-select");
  await visible(selector, `${profile} garden selector`);
  await selector.selectOption(String(gardenId));
  await waitFor(
    async () => await selector.inputValue() === String(gardenId),
    `${profile} active garden ${gardenId}`,
  );
  if (profile === "mobile") await closeMobileUtility(page);
  if (waitForSettle) {
    await waitFor(
      async () => !await page.locator("body.garden-switch-pending").count(),
      `${profile} garden switch ${gardenId} to settle`,
    );
  }
}

async function openPrimary(page, profile, tab) {
  const button = page.locator(profile === "mobile" ? `#mobile-tab-${tab}` : `#top-tab-${tab}`);
  await visible(button, `${profile} ${tab} tab`);
  await button.click();
}

async function openSubMode(page, profile, tab, subMode, contentSelector) {
  await openPrimary(page, profile, tab);
  const button = page.locator(
    `#sub-mode-${subMode}:visible, [data-sub-mode='${subMode}']:visible`,
  ).first();
  await visible(button, `${subMode} sub-mode`);
  await button.click();
  await visible(page.locator(contentSelector), `${subMode} content`);
}

async function openTasks(page, profile) {
  await openSubMode(page, profile, "activity", "tasks", "#tasks-tab-content");
  await visible(page.locator("#tasks-list"), "task list");
}

async function startCalendar(page, profile) {
  await openPrimary(page, profile, "activity");
  const button = page.locator("#sub-mode-calendar:visible, [data-sub-mode='calendar']:visible").first();
  await visible(button, "calendar sub-mode");
  await button.click();
  await visible(page.locator("#calendar-root"), "calendar root");
}

async function openCalendar(page, profile) {
  await startCalendar(page, profile);
  await page.locator("#calendar-loading").waitFor({ state: "hidden" });
}

async function openCalendarAgenda(page, profile) {
  await openCalendar(page, profile);
  const agendaView = page.locator("[data-calendar-view='agenda']:visible").first();
  await visible(agendaView, "calendar agenda view control");
  if (!await agendaView.evaluate((element) => element.classList.contains("active"))) {
    await agendaView.click();
    await waitFor(
      async () => await agendaView.evaluate((element) => element.classList.contains("active")),
      "calendar agenda view",
    );
    await page.locator("#calendar-loading").waitFor({ state: "hidden" });
  }
}

async function openCare(page, profile) {
  await openSubMode(page, profile, "insights", "care", "#care-view");
  await visible(page.locator("#weather-dashboard"), "weather dashboard");
}

async function openToday(page, profile) {
  await openPrimary(page, profile, "map");
  if (profile === "mobile") {
    const handle = page.locator("#attention-today-mobile-handle");
    await visible(handle, "mobile Today handle");
    await handle.click();
    const sheet = page.locator("#attention-today-mobile-sheet");
    await visible(sheet, "mobile Today sheet");
    return sheet;
  }
  const panel = page.locator("#attention-today-panel");
  await visible(panel, "desktop Today panel");
  return panel;
}

async function assertMapFirstGeometry(page, profile) {
  const geometry = await page.evaluate((isMobile) => {
    const map = document.querySelector("#map-grid");
    if (!(map instanceof HTMLElement)) throw new Error("map-first surface is missing");
    const mapRect = map.getBoundingClientRect();
    const viewport = { height: window.innerHeight, width: window.innerWidth };
    const center = {
      x: Math.round(mapRect.left + mapRect.width / 2),
      y: Math.round(mapRect.top + mapRect.height / 2),
    };
    const centerTarget = document.elementFromPoint(center.x, center.y);
    if (!centerTarget || !map.contains(centerTarget)) {
      throw new Error("map-first center point is obscured");
    }
    const blockingSelectors = [
      ".drawer",
      ".bottom-sheet",
      "[role='dialog']:not([hidden])",
      ".modal:not([hidden])",
      ".plot-popover",
    ];
    const overlapping = blockingSelectors.flatMap((selector) => (
      [...document.querySelectorAll(selector)].filter((element) => {
        if (!(element instanceof HTMLElement) || element.contains(map)) return false;
        const style = window.getComputedStyle(element);
        if (style.display === "none" || style.visibility === "hidden") return false;
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0
          && rect.left < mapRect.right && rect.right > mapRect.left
          && rect.top < mapRect.bottom && rect.bottom > mapRect.top;
      }).map((element) => element.id || element.className || selector)
    ));
    if (overlapping.length > 0) throw new Error(`map-first overlay overlap: ${overlapping.join(",")}`);
    const closedSheets = isMobile ? [
      "#attention-today-mobile-sheet",
      "#mobile-utility-sheet",
      "#mobile-quick-actions",
    ].map((selector) => {
      const element = document.querySelector(selector);
      if (!(element instanceof HTMLElement)) throw new Error(`mobile sheet is missing: ${selector}`);
      const style = window.getComputedStyle(element);
      const hidden = element.hidden || element.getAttribute("aria-hidden") === "true"
        || style.display === "none" || style.visibility === "hidden";
      if (!hidden || !element.inert) throw new Error(`closed mobile sheet is not hidden and inert: ${selector}`);
      return selector;
    }) : [];
    return {
      center_owned_by_map: true,
      closed_mobile_sheets: closedSheets.length,
      map_height: Math.round(mapRect.height),
      map_width: Math.round(mapRect.width),
      overlay_count: overlapping.length,
      viewport_height: viewport.height,
      viewport_width: viewport.width,
    };
  }, profile === "mobile");
  assert(geometry.map_width >= geometry.viewport_width * (profile === "mobile" ? 0.8 : 0.45),
    `${profile} map-first width was too small`);
  assert(geometry.map_height >= geometry.viewport_height * (profile === "mobile" ? 0.42 : 0.35),
    `${profile} map-first height was too small`);
  assert(geometry.overlay_count === 0 && geometry.center_owned_by_map === true,
    `${profile} map-first surface had an overlapping overlay`);
  if (profile === "mobile") {
    assert(geometry.closed_mobile_sheets === 3,
      "Mobile map-first surface did not keep every closed sheet inert");
  }
  return geometry;
}

async function completeBloomTask(page, card, plantName, notSeen) {
  await card.getByRole("button", { name: /^Complete$/i }).click();
  const dialog = page.locator(".task-completion-dialog").last();
  await visible(dialog, "bloom completion dialog");
  const checkbox = dialog.getByLabel(plantName, { exact: true });
  await visible(checkbox, `bloom plant ${plantName}`);
  assert(await checkbox.isChecked(), `${plantName} was not selected by default`);
  if (notSeen) {
    await dialog.locator(".task-completion-not-seen").click();
  } else {
    await dialog.locator(".confirm-yes").click();
  }
  await dialog.waitFor({ state: "hidden" });
  await card.waitFor({ state: "hidden" });
}

async function completeGroupedFertilize(page, fixture) {
  const task = taskTitle(fixture, "fertilize_grouped");
  const card = taskCard(page, task.title);
  await visible(card, "grouped fertilize task");
  await card.getByRole("button", { name: /^Complete$/i }).click();
  const dialog = page.locator(".task-completion-dialog").last();
  await visible(dialog, "grouped fertilize completion dialog");
  const plantA = fixture.phase_two.plant_names.fertilize_a;
  const plantB = fixture.phase_two.plant_names.fertilize_b;
  const first = dialog.getByLabel(plantA, { exact: true });
  const second = dialog.getByLabel(plantB, { exact: true });
  assert(await first.isChecked() && await second.isChecked(), "Grouped plants were not selected");
  await second.uncheck();
  await dialog.locator(".confirm-yes").click();
  await dialog.waitFor({ state: "hidden" });
  await visible(
    taskCard(page, `Fertilize: ${plantB}`),
    "remaining grouped fertilize task",
  );
}

async function assertGroupedCompletionSelectionRequired(dialog, fixture, surface) {
  await visible(dialog, `${surface} grouped completion dialog`);
  const first = dialog.getByLabel(fixture.phase_two.plant_names.fertilize_a, { exact: true });
  const second = dialog.getByLabel(fixture.phase_two.plant_names.fertilize_b, { exact: true });
  assert(await first.isChecked() && await second.isChecked(),
    `${surface} grouped completion did not start with both plants selected`);
  await dialog.locator(".task-completion-clear").click();
  const confirm = dialog.locator(".confirm-yes");
  assert(await confirm.isDisabled(), `${surface} allowed grouped completion without a plant`);
  await visible(dialog.locator(".task-completion-feedback").filter({ hasText: /Select at least one plant/i }),
    `${surface} grouped completion did not explain its selection restriction`);
  await dialog.locator(".confirm-no").click();
  await dialog.waitFor({ state: "hidden" });
}

async function exerciseGroupedCompletionRestrictions(page, fixture) {
  const grouped = taskTitle(fixture, "fertilize_grouped");
  await openTasks(page, "desktop");
  const taskSurfaceCard = taskCard(page, grouped.title);
  await visible(taskSurfaceCard, "grouped task on Tasks surface");
  await taskSurfaceCard.getByRole("button", { name: /^Complete$/i }).click();
  await assertGroupedCompletionSelectionRequired(
    page.locator(".task-completion-dialog").last(),
    fixture,
    "Tasks",
  );

  await openCalendarAgenda(page, "desktop");
  const calendarEvent = page.locator(".fc-event:visible").filter({ hasText: grouped.title }).first();
  await visible(calendarEvent, "grouped task on Calendar surface");
  await calendarEvent.click();
  await page.locator("#calendar-detail-panel").getByRole("button", { name: /^Complete$/i }).click();
  await assertGroupedCompletionSelectionRequired(
    page.locator(".task-completion-dialog").last(),
    fixture,
    "Calendar",
  );

  await openPrimary(page, "desktop", "map");
  const plot = page.locator(`.plot[data-plot-id='${fixture.phase_two.plot_ids.alpha}']`);
  await visible(plot, "grouped task plot on map");
  await plot.click();
  const details = page.locator(`[data-view-plot-details='${fixture.phase_two.plot_ids.alpha}']`);
  await visible(details, "grouped task plot details command");
  await details.click();
  const preview = page.locator(".drawer-tasks-preview:visible, .sheet-tasks-preview:visible");
  const plotCard = preview.locator(".drawer-task-card").filter({ hasText: grouped.title });
  await visible(plotCard, "grouped task on plot drawer surface");
  await plotCard.locator(".action-complete").click();
  await assertGroupedCompletionSelectionRequired(
    page.locator(".task-completion-dialog").last(),
    fixture,
    "Plot drawer",
  );
  await page.locator(".drawer .close-btn").click();
  await page.locator(".drawer").waitFor({ state: "hidden" });
}

async function exerciseImmediateSnoozeCorrection(page, fixture) {
  const task = taskTitle(fixture, "snooze_correction");
  await openCalendar(page, "mobile");
  const week = page.locator("[data-calendar-view='week']:visible").first();
  await visible(week, "calendar week view for immediate snooze correction");
  if (!await week.evaluate((element) => element.classList.contains("active"))) {
    await week.click();
    await waitFor(async () => await week.evaluate((element) => element.classList.contains("active")),
      "calendar week view for immediate snooze correction");
  }
  await page.locator("#calendar-loading").waitFor({ state: "hidden" });

  let event = page.locator(".fc-event:visible").filter({ hasText: task.title }).first();
  if (!await event.count()) {
    const more = page.locator(".fc-daygrid-more-link:visible").last();
    await visible(more, "mobile Calendar all-day overflow");
    await more.click();
    event = page.locator(".fc-popover .fc-event:visible").filter({ hasText: task.title }).first();
  }
  await visible(event, "dedicated immediate snooze correction Calendar event");
  await event.click();
  const detail = page.locator("#calendar-detail-panel");
  await visible(detail.getByText(task.title, { exact: true }), "immediate snooze correction Calendar detail");
  const snoozeButton = detail.getByRole("button", { name: /snooze 1 week/i });
  await visible(snoozeButton, "Calendar one-week snooze action");
  const snoozeDates = [];
  const requestListener = (request) => {
    if (request.method() !== "POST"
      || new URL(request.url()).pathname !== `/api/tasks/${task.id}/action`) return;
    try {
      const body = request.postDataJSON();
      if (body?.action === "snooze" && typeof body.snooze_until === "string") {
        snoozeDates.push(body.snooze_until);
      }
    } catch {
      // The final database assertion is authoritative if browser request observation is unavailable.
    }
  };
  page.on("request", requestListener);
  try {
    await snoozeButton.click();
    const correctionToast = page.locator("#toast-container .toast").filter({
      hasText: fixture.phase_two.snooze_correction.default_date,
    }).last();
    const correctionAction = correctionToast.getByRole("button", { name: /^Change date$/i });
    await visible(correctionAction, "2s Change date correction action after immediate snooze");
    await waitFor(() => snoozeDates.includes(fixture.phase_two.snooze_correction.default_date),
      "immediate one-week snooze mutation");

    await event.waitFor({ state: "hidden" });
    assert(
      await page.locator(".fc-event:visible").filter({ hasText: task.title }).count() === 0,
      "Calendar correction task remained in the visible week after its +1 week snooze",
    );

    await correctionAction.click();
    const dialog = page.locator(".confirm-dialog").filter({
      has: page.locator("input[type='date']"),
    }).last();
    await visible(dialog, "immediate snooze correction date dialog before expiry");
    const warning = dialog.locator(".task-date-dialog-warning");
    assert(await warning.isHidden(), "Immediate correction task unexpectedly opened a care-window warning");
    const input = dialog.locator("input[type='date']");
    assert(await input.inputValue() === fixture.phase_two.snooze_correction.default_date,
      "Immediate snooze correction did not carry forward the one-week date");
    await input.fill(fixture.phase_two.manual_date);
    await dialog.locator(".confirm-yes").click();
    await dialog.waitFor({ state: "hidden" });
    await waitFor(() => snoozeDates.includes(fixture.phase_two.manual_date),
      "manual snooze correction mutation");
  } finally {
    page.off("request", requestListener);
  }
}

async function snoozePruneWithManualDate(page, fixture) {
  const task = taskTitle(fixture, "prune_desktop");
  const card = taskCard(page, task.title);
  await visible(card, "prune snooze task");
  await card.getByRole("button", { name: /1 week/i }).click();
  const dialog = page.locator(".confirm-dialog").filter({
    has: page.locator("input[type='date']"),
  }).last();
  await visible(dialog, "prune snooze date dialog");
  const warning = dialog.locator(".task-date-dialog-warning");
  await visible(warning, "prune window warning");
  assert((await warning.textContent()).trim().length > 0, "Prune window warning was empty");
  const input = dialog.locator("input[type='date']");
  assert(await input.inputValue() === "2026-07-19", "Prune +1 week default was incorrect");
  await input.fill(fixture.phase_two.manual_date);
  await dialog.locator(".confirm-yes").click();
  await card.waitFor({ state: "hidden" });
}

async function completeBatch(page, fixture) {
  const first = taskTitle(fixture, "batch_a");
  const second = taskTitle(fixture, "batch_b");
  const firstCard = taskCard(page, first.title);
  const secondCard = taskCard(page, second.title);
  await visible(firstCard, "first batch task");
  await visible(secondCard, "second batch task");
  await firstCard.getByRole("checkbox", { name: new RegExp(first.title, "i") }).check();
  await secondCard.getByRole("checkbox", { name: new RegExp(second.title, "i") }).check();
  const bar = page.locator("#tasks-batch-bar");
  await visible(bar, "task batch bar");
  await bar.getByRole("button", { name: /^Complete$/i }).click();
  await firstCard.waitFor({ state: "hidden" });
  await secondCard.waitFor({ state: "hidden" });
}

async function exerciseTaskFormKeyboard(page, fixture) {
  await openTasks(page, "desktop");
  const add = page.locator("#tasks-add-btn");
  await add.focus();
  await add.press("Enter");
  let dialog = page.locator(".modal").filter({ has: page.locator(".task-form-dialog") }).last();
  await visible(dialog, "task create dialog");
  assert(await dialog.getAttribute("role") === "dialog"
    && await dialog.getAttribute("aria-modal") === "true",
  "Task create form did not expose modal dialog semantics");
  await assertFocusInside(dialog, "task create dialog");
  assert(await dialog.locator("label[for='task-form-type']").count() === 1
    && await dialog.locator("label[for='task-form-name']").count() === 1
    && await dialog.locator("label[for='task-form-due']").count() === 1,
  "Task create form controls were not explicitly labelled");
  assert(await dialog.locator("#task-form-type").evaluate(
    (element) => document.activeElement === element,
  ), "Task create dialog did not focus its first form control");
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
  await waitFor(async () => await add.evaluate((element) => document.activeElement === element),
    "task create dialog focus return");

  const existing = taskCard(page, taskTitle(fixture, "stale_manual_water").title);
  const edit = existing.getByRole("button", { name: /^Edit$/i });
  await visible(edit, "task edit command");
  await edit.focus();
  await edit.press("Enter");
  dialog = page.locator(".modal").filter({ has: page.locator(".task-form-dialog") }).last();
  await visible(dialog, "task edit dialog");
  const cancel = dialog.getByRole("button", { name: /^Cancel$/i });
  await cancel.focus();
  await cancel.press("Enter");
  await dialog.waitFor({ state: "detached" });
  await waitFor(async () => await edit.evaluate((element) => document.activeElement === element),
    "task edit cancel focus return");
}

async function completePlotDrawerTask(page, fixture) {
  await openPrimary(page, "desktop", "map");
  const plot = page.locator(`.plot[data-plot-id='${fixture.phase_two.plot_ids.alpha}']`);
  await visible(plot, "Phase 2 plot on map");
  await plot.click();
  const details = page.locator(
    `[data-view-plot-details='${fixture.phase_two.plot_ids.alpha}']`,
  );
  await visible(details, "Phase 2 plot details command");
  await details.click();
  const drawer = page.locator(".drawer");
  await visible(drawer, "plot drawer dialog");
  assert(await drawer.getAttribute("role") === "dialog"
    && await drawer.getAttribute("aria-modal") === "true",
  "Plot drawer did not expose modal dialog semantics");
  await assertFocusInside(drawer, "plot drawer");
  const preview = page.locator(".drawer-tasks-preview:visible, .sheet-tasks-preview:visible");
  await visible(preview, "plot task preview");
  const taskSectionToggle = preview.locator("button.drawer-section-header").first();
  await visible(taskSectionToggle, "plot task collapsible control");
  assert(await taskSectionToggle.evaluate((element) => (
    element instanceof HTMLButtonElement && element.type === "button"
  )), "Plot task collapsible control was not a non-submitting native button");
  assert(await taskSectionToggle.getAttribute("aria-expanded") === "true",
    "Plot task section did not expose its expanded state");
  const drawerUrl = page.url();
  await taskSectionToggle.click();
  await waitFor(async () => await taskSectionToggle.getAttribute("aria-expanded") === "false",
    "plot task section keyboard collapse boundary");
  assert(page.url() === drawerUrl, "Plot task collapse unexpectedly navigated away from the drawer");
  await taskSectionToggle.focus();
  assert(await taskSectionToggle.evaluate((element) => document.activeElement === element),
    "Plot task collapsible control did not retain keyboard focus");
  await page.keyboard.press("Enter");
  await waitFor(async () => await taskSectionToggle.getAttribute("aria-expanded") === "true",
    "plot task section keyboard expand boundary");
  assert(page.url() === drawerUrl && await drawer.isVisible(),
    "Plot task keyboard expansion lost the drawer state boundary");
  const staleGenerated = taskTitle(fixture, "stale_generated_water");
  const staleManual = taskTitle(fixture, "stale_manual_water");
  assert(await preview.getByText(staleGenerated.title, { exact: true }).count() === 0,
    "Expired generated watering leaked into plot tasks");
  await visible(preview.getByText(staleManual.title, { exact: true }), "manual overdue plot task");
  const plotTask = taskTitle(fixture, "plot_drawer");
  const card = preview.locator(".drawer-task-card").filter({ hasText: plotTask.title });
  await visible(card, "plot drawer action task");
  await card.locator(".action-complete").click();
  const completedCard = preview.locator(".drawer-task-card.task-completed")
    .filter({ hasText: plotTask.title });
  await visible(completedCard, "completed plot drawer task history");
  assert(await completedCard.locator(".drawer-task-actions").count() === 0,
    "Completed plot drawer task retained write controls");
  await page.keyboard.press("Escape");
  await drawer.waitFor({ state: "hidden" });
  await waitFor(async () => await page.evaluate(({ plotId }) => {
    const active = document.activeElement;
    const detailsTrigger = document.querySelector(`[data-view-plot-details='${plotId}']`);
    const plotTrigger = document.querySelector(`.plot[data-plot-id='${plotId}']`);
    return active === detailsTrigger || active === plotTrigger;
  }, { plotId: fixture.phase_two.plot_ids.alpha }),
    "plot drawer focus return");
}

async function selectChip(dialog, containerSelector, value) {
  const container = dialog.locator(containerSelector);
  const input = container.locator(".chip-input__field");
  await input.fill(value);
  const option = container.locator(".chip-input__option").filter({ hasText: value }).first();
  await visible(option, `chip option ${value}`);
  await option.click();
  await visible(container.locator(".chip-input__chip").filter({ hasText: value }), `chip ${value}`);
}

async function readDownload(download) {
  const stream = await download.createReadStream();
  assert(stream, "Calendar export stream was unavailable");
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

function unfoldIcs(ics) {
  assert(ics.includes("\r\n"), "Calendar export did not use CRLF line endings");
  const physicalLines = ics.split("\r\n");
  assert(
    physicalLines.every((line) => !line.includes("\r") && !line.includes("\n")),
    "Calendar export contained a non-CRLF line ending",
  );
  for (const line of physicalLines) {
    assert(Buffer.byteLength(line, "utf8") <= 75, "Calendar export exceeded 75 UTF-8 octets");
  }
  const unfolded = [];
  for (const line of physicalLines) {
    if (/^[ \t]/.test(line) && unfolded.length > 0) {
      unfolded[unfolded.length - 1] += line.slice(1);
    } else if (line) {
      unfolded.push(line);
    }
  }
  return unfolded;
}

function unescapeIcsText(value) {
  return value.replace(/\\([nN,;\\])/g, (_match, character) => {
    if (character === "n" || character === "N") return "\n";
    return character;
  });
}

function parseIcsEvents(ics) {
  const events = [];
  let event = null;
  for (const line of unfoldIcs(ics)) {
    if (line === "BEGIN:VEVENT") {
      event = {};
      continue;
    }
    if (line === "END:VEVENT") {
      if (event) events.push(event);
      event = null;
      continue;
    }
    if (!event) continue;
    const separator = line.indexOf(":");
    if (separator < 1) continue;
    const name = line.slice(0, separator).split(";", 1)[0].toUpperCase();
    if (!event[name]) event[name] = [];
    event[name].push(line.slice(separator + 1));
  }
  return events;
}

function nextIsoDate(date) {
  const next = new Date(`${date}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  return next.toISOString().slice(0, 10).replaceAll("-", "");
}

function assertCalendarExportIcs(ics, fixture) {
  assert(ics.includes("BEGIN:VCALENDAR") && ics.includes("END:VCALENDAR"),
    "Calendar export was not a complete ICS document");
  const events = parseIcsEvents(ics);
  assert(events.length > 0, "Calendar export contained no VEVENT records");
  const calendar = fixture.phase_two.calendar;
  const seeded = events.find((event) => (
    (event.SUMMARY || []).some((value) => unescapeIcsText(value) === calendar.seeded_title)
  ));
  assert(seeded, "Calendar export omitted seeded event");
  assert((seeded.DTSTART || []).includes(calendar.seeded_event_on.replaceAll("-", "")),
    "Calendar export changed the seeded all-day start date");
  assert((seeded.DTEND || []).includes(nextIsoDate(calendar.seeded_event_on)),
    "Calendar export changed the seeded all-day end date");
  const expectedDescription = [
    calendar.seeded_description,
    `Plants: ${fixture.phase_two.plant_ids.bloom_desktop}`,
    `Plots: ${fixture.phase_two.plot_ids.alpha}`,
  ].join("\n");
  assert((seeded.DESCRIPTION || []).some(
    (value) => unescapeIcsText(value) === expectedDescription,
  ), "Calendar export did not preserve the seeded description and linked context");
  assert(!events.some((event) => (
    (event.SUMMARY || []).some((value) => (
      unescapeIcsText(value) === fixture.phase_two.task_titles[fixture.phase_two.task_ids.rain_outdoor]
    ))
  )), "Calendar export leaked a Beta garden task into the Alpha garden scope");
  assert(!/(?:authorization|bearer|csrf|api[_-]?key|password|secret|token)/i.test(ics),
    "Calendar export leaked credential material");
  assert(!/\/calendar\/subscriptions\/[A-Za-z0-9_-]+\.ics/i.test(ics),
    "Calendar export leaked a subscription feed URL");
}

async function exerciseCalendarSubscriptionFeed(page, diagnostics, onCreated = null) {
  const label = "Phase 2 Admin Feed";
  const createResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/calendar/subscriptions"
  ));
  await page.locator("#calendar-new-feed-btn").click();
  const labelDialog = page.locator(".modal").filter({
    has: page.locator(".prompt-dialog-input"),
  });
  await visible(labelDialog, "calendar subscription label dialog");
  await labelDialog.locator(".prompt-dialog-input").fill(label);
  await labelDialog.locator(".confirm-yes").click();
  const created = await createResponse;
  assert(created.status() === 201, `Calendar subscription creation returned ${created.status()}`);
  const createdPayload = await created.json();
  const feedPath = typeof createdPayload?.feed_path === "string" ? createdPayload.feed_path : "";
  assert(/^\/calendar\/subscriptions\/[A-Za-z0-9_-]+\.ics$/.test(feedPath),
    "Calendar subscription creation did not return a bounded feed path");
  const feedUrl = new URL(feedPath, page.url()).toString();

  const subscription = page.locator(".calendar-subscription-item").filter({ hasText: label });
  await visible(subscription, "created calendar subscription");
  if (onCreated) await onCreated({ label, subscription });

  // Keep the opaque feed URL in this function only. This page-origin feed fetch is captured by
  // the global browser route guard before the expected revoked-feed failure is consumed below.
  const activeFeed = await page.evaluate(async (url) => {
    const response = await fetch(url, { credentials: "include" });
    return {
      body: await response.text(),
      contentType: response.headers.get("content-type") || "",
      status: response.status,
    };
  }, feedUrl);
  assert(activeFeed.status === 200,
    "New calendar feed URL was not a successful subscription request");
  assert(/text\/calendar/i.test(activeFeed.contentType)
    && activeFeed.body.includes("BEGIN:VCALENDAR") && activeFeed.body.includes("END:VCALENDAR"),
    "New calendar feed did not return a calendar document");

  await subscription.getByRole("button").click();
  const revoke = page.locator("[role='alertdialog']");
  await visible(revoke, "calendar subscription revoke confirmation");
  await revoke.locator(".confirm-yes").click();
  await subscription.waitFor({ state: "hidden" });

  const httpMark = diagnostics.httpErrors.length;
  const consoleMark = diagnostics.consoleErrors.length;
  const revokedStatus = await page.evaluate(async (url) => {
    const response = await fetch(url, { credentials: "include" });
    return response.status;
  }, feedUrl);
  assert(revokedStatus === 404,
    "Revoked calendar feed URL remained usable");
  await waitFor(
    () => diagnostics.httpErrors.length === httpMark + 1,
    "revoked calendar feed HTTP diagnostic",
  );
  assert(
    diagnostics.httpErrors.length === httpMark + 1,
    "Revoked calendar feed produced unexpected HTTP diagnostics",
  );
  diagnostics.httpErrors.splice(httpMark, 1);
  await waitFor(
    () => diagnostics.consoleErrors.length === consoleMark + 1,
    "revoked calendar feed console diagnostic",
  );
  assert(
    diagnostics.consoleErrors.length === consoleMark + 1,
    "Revoked calendar feed produced unexpected console diagnostics",
  );
  diagnostics.consoleErrors.splice(consoleMark, 1);
}

async function exerciseCalendarLifecycle(
  page,
  profile,
  fixture,
  diagnostics,
  {
    includeExportAndSubscription = profile === "desktop",
    onSubscriptionCreated = null,
  } = {},
) {
  await openCalendar(page, profile);
  for (const mode of ["week", "agenda", "month"]) {
    const button = page.locator(`[data-calendar-view='${mode}']`);
    await button.click();
    await waitFor(async () => await button.evaluate((element) => element.classList.contains("active")),
      `calendar ${mode} view`);
  }
  await openCalendarAgenda(page, profile);
  const seededEvent = page.locator(".fc-event").filter({ hasText: fixture.phase_two.calendar.seeded_title }).first();
  await visible(seededEvent, "seeded calendar event");
  await seededEvent.click();
  const detail = page.locator("#calendar-detail-panel");
  await visible(detail.getByText(fixture.phase_two.plant_names.bloom_desktop, { exact: false }),
    "calendar plant link");
  await visible(detail.getByText(fixture.phase_two.plot_ids.alpha, { exact: false }),
    "calendar plot link");

  if (includeExportAndSubscription) {
    const failureMark = diagnostics.requestFailures.length;
    const expectedDownloadAborts = [];
    const downloadFailureListener = (request) => {
      const failure = request.failure()?.errorText || "";
      const parsed = new URL(request.url());
      if (
        request.method() === "GET"
        && parsed.pathname === "/api/calendar/export.ics"
        && failure.includes("ERR_ABORTED")
      ) {
        expectedDownloadAborts.push({ failure, path: parsed.pathname });
      }
    };
    page.on("requestfailed", downloadFailureListener);
    try {
      const downloadPromise = page.waitForEvent("download");
      const exportRequestPromise = page.waitForRequest((request) => (
        request.method() === "GET"
          && new URL(request.url()).pathname === "/api/calendar/export.ics"
      ));
      await page.locator("#calendar-export-btn").click();
      const [download, exportRequest] = await Promise.all([
        downloadPromise,
        exportRequestPromise,
      ]);
      const downloadUrl = new URL(exportRequest.url());
      assert(downloadUrl.pathname === "/api/calendar/export.ics",
        "Calendar export download did not use the calendar export endpoint");
      assert(downloadUrl.searchParams.get("garden_id") === String(fixture.gardens.alpha.id),
        "Calendar export download did not retain the selected Alpha garden");
      const ics = await readDownload(download);
      assertCalendarExportIcs(ics, fixture);
      await download.delete();
    } finally {
      page.off("requestfailed", downloadFailureListener);
    }
    const failuresAdded = diagnostics.requestFailures.length - failureMark;
    assert(failuresAdded === expectedDownloadAborts.length,
      "Calendar export produced an unaccounted request failure");
    assert(expectedDownloadAborts.length <= 1,
      "Calendar export produced duplicate browser download aborts");
    diagnostics.requestFailures.splice(failureMark, failuresAdded);
  }

  const title = `Phase 2 Browser ${profile === "mobile" ? "Mobile" : "Desktop"} Calendar Event`;
  const editedTitle = `${title} Edited`;
  const mutationEvidence = [];
  const captureMutation = async (response, expected) => {
    const request = response.request();
    const pathname = new URL(response.url()).pathname;
    assert(request.method() === expected.method && pathname === expected.path,
      `Calendar lifecycle response did not match ${expected.method} ${expected.path}`);
    assert(response.status() === expected.statusCode,
      `Calendar lifecycle ${expected.method} returned ${response.status()}`);
    const requestId = response.headers()["x-request-id"] || "";
    assert(/^[A-Za-z0-9._-]{1,64}$/.test(requestId),
      `Calendar lifecycle ${expected.method} response lacked a safe request ID`);
    const payload = await response.json();
    assert(payload && typeof payload === "object" && payload.status === expected.status,
      `Calendar lifecycle ${expected.method} response body was unexpected`);
    if (expected.eventId) {
      assert(payload.event?.target_id === expected.eventId,
        `Calendar lifecycle ${expected.method} response event identity was unexpected`);
    }
    if (expected.deletedId) {
      assert(payload.id === expected.deletedId,
        "Calendar lifecycle DELETE response identity was unexpected");
    }
    mutationEvidence.push({
      method: expected.method,
      path: expected.path,
      request_id: requestId,
      status_code: expected.statusCode,
    });
    return payload;
  };
  await page.locator("#calendar-new-event-btn").click();
  let dialog = page.locator("#calendar-manual-event-form").last();
  await visible(dialog, "calendar create form");
  await dialog.locator("input[name='title']").fill(title);
  await dialog.locator("input[name='event_on']").fill("2026-07-15");
  await dialog.locator("textarea[name='description']").fill("Phase 2 browser lifecycle.");
  await selectChip(dialog, ".calendar-manual-event-plant-input", fixture.phase_two.plant_names.bloom_desktop);
  await selectChip(dialog, ".calendar-manual-event-plot-input", fixture.phase_two.plot_ids.alpha);
  const createResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/calendar/manual-events"
  ));
  await dialog.locator("button[type='submit']").click();
  const createdPayload = await captureMutation(await createResponsePromise, {
    method: "POST",
    path: "/api/calendar/manual-events",
    status: "created",
    statusCode: 201,
  });
  const eventId = createdPayload.event?.target_id;
  assert(typeof eventId === "string" && /^calevt_[a-z0-9]+$/.test(eventId),
    "Calendar lifecycle create response returned an invalid event ID");
  const event = page.locator(".fc-event").filter({ hasText: title }).first();
  await visible(event, "created browser calendar event");
  await event.click();
  await page.locator("[data-calendar-detail-edit-manual-event='true']").click();
  dialog = page.locator("#calendar-manual-event-form").last();
  await visible(dialog, "calendar edit form");
  await dialog.locator("input[name='title']").fill(editedTitle);
  const updateResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "PATCH"
      && new URL(response.url()).pathname === `/api/calendar/manual-events/${eventId}`
  ));
  await dialog.locator("button[type='submit']").click();
  await captureMutation(await updateResponsePromise, {
    eventId,
    method: "PATCH",
    path: `/api/calendar/manual-events/${eventId}`,
    status: "updated",
    statusCode: 200,
  });
  const edited = page.locator(".fc-event").filter({ hasText: editedTitle }).first();
  await visible(edited, "edited browser calendar event");
  await edited.click();
  await page.locator("[data-calendar-detail-delete-manual-event='true']").click();
  const confirm = page.locator("[role='alertdialog']");
  await visible(confirm, "calendar delete confirmation");
  const deleteResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "DELETE"
      && new URL(response.url()).pathname === `/api/calendar/manual-events/${eventId}`
  ));
  await confirm.locator(".confirm-yes").click();
  await captureMutation(await deleteResponsePromise, {
    deletedId: eventId,
    method: "DELETE",
    path: `/api/calendar/manual-events/${eventId}`,
    status: "deleted",
    statusCode: 200,
  });
  await edited.waitFor({ state: "hidden" });

  if (includeExportAndSubscription) {
    await exerciseCalendarSubscriptionFeed(page, diagnostics, onSubscriptionCreated);
  }
  return { event_id: eventId, mutations: mutationEvidence };
}

async function openNotifications(page, profile) {
  let trigger;
  if (profile === "mobile") {
    if (!await page.locator("body.mobile-utility-open").count()) {
      await page.locator("#mobile-utility-btn").click();
    }
    await visible(page.locator("#mobile-utility-sheet"), "mobile utility for notifications");
    trigger = page.locator("#mobile-notification-btn");
  } else {
    trigger = page.locator("#notification-bell");
  }
  await trigger.click();
  const panel = page.locator("#notification-panel");
  await visible(panel, "notification panel");
  await assertFocusInside(panel, "notification panel");
  return { panel, trigger };
}

async function closeNotificationSettingsWithKeyboard(page, panel, label) {
  await page.keyboard.press("Escape");
  await visible(panel.locator(".notification-settings-btn"), `${label} notification list`);
  assert(await panel.isVisible(), `${label} Escape closed the full notification panel from settings`);
  await waitFor(
    async () => await panel.locator(".notification-settings-btn").evaluate(
      (element) => document.activeElement === element,
    ),
    `${label} notification settings focus return`,
  );
  await page.keyboard.press("Escape");
  await panel.waitFor({ state: "hidden" });
}

async function issueCreatedRuleControls(prefs) {
  const row = prefs.locator(".notification-prefs-rule-row").filter({ hasText: /New issues/i }).first();
  await visible(row, "issue-created notification preference");
  const inbox = row.getByRole("button", { name: "New issues: App", exact: true });
  const digest = row.getByRole("button", { name: "New issues: Email", exact: true });
  const severity = row.locator(".notification-prefs-severity");
  await visible(inbox, "issue-created inbox notification preference");
  await visible(digest, "issue-created digest notification preference");
  await visible(severity, "issue-created notification severity preference");
  return { digest, inbox, severity };
}

async function assertMobilePreferenceTouchGeometry(prefs) {
  const controls = prefs.locator(
    ".notification-prefs-toggle:visible, .notification-prefs-severity:visible, .btn-primary:visible",
  );
  const measurements = [];
  const count = await controls.count();
  assert(count >= 4, "Mobile notification preferences did not expose enough touch controls");
  for (let index = 0; index < count; index += 1) {
    const box = await controls.nth(index).boundingBox();
    assert(box && box.width >= 44 && box.height >= 44,
      `Mobile notification preference control ${index + 1} is smaller than 44px`);
    measurements.push({ height: Math.round(box.height), width: Math.round(box.width) });
  }
  return measurements;
}

async function openNotificationPreferences(page, profile, fixture, label) {
  await openPrimary(page, profile, "map");
  await visible(page.locator("#map-grid"), `${label} map before notification preferences`);
  await selectGarden(page, profile, fixture.gardens.alpha.id);
  const { panel } = await openNotifications(page, profile);
  await panel.locator(".notification-settings-btn").click();
  const prefs = panel.locator(".notification-prefs-form");
  await visible(prefs, `${label} notification preferences`);
  return { panel, prefs };
}

async function saveNotificationPreferenceSeverity(page, prefs, severity, label) {
  assert(typeof severity === "string" && severity.length > 0,
    `${label} notification preference severity was invalid`);
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "PUT"
      && new URL(response.url()).pathname === "/api/notifications/preferences"
  ));
  await prefs.locator(".btn-primary").click();
  const response = await responsePromise;
  assert(response.status() === 200, `${label} notification preference save failed`);
  const requestId = response.headers()["x-request-id"] || "";
  assert(/^[A-Za-z0-9._-]{1,64}$/.test(requestId),
    `${label} notification preference save lacked a request ID`);
  return { request_id: requestId, status_code: response.status() };
}

async function closeNotificationPreferencePanel(page, panel, profile, label) {
  await closeNotificationSettingsWithKeyboard(page, panel, label);
  if (profile === "mobile") await closeMobileUtility(page);
}

async function exercisePersonalNotificationPreferencePersistence(page, profile, role, fixture, oracle) {
  const contract = oracle?.phase_two?.fixture?.notification_persistence?.[`${role}:${profile}`];
  assert(contract && typeof contract === "object", `Missing ${role}:${profile} preference oracle`);
  let { panel, prefs } = await openNotificationPreferences(page, profile, fixture, `${role} ${profile}`);
  let issue = await issueCreatedRuleControls(prefs);
  assert(await issue.severity.inputValue() === contract.initial_severity,
    `${role} ${profile} notification preference did not begin at its oracle severity`);
  const touchTargets = profile === "mobile" ? await assertMobilePreferenceTouchGeometry(prefs) : [];
  await issue.severity.selectOption(contract.saved_severity);
  const saveResponses = [await saveNotificationPreferenceSeverity(
    page,
    prefs,
    contract.saved_severity,
    `${role} ${profile} saved`,
  )];
  await closeNotificationPreferencePanel(page, panel, profile, `${role} ${profile} saved`);

  await page.reload({ waitUntil: "domcontentloaded" });
  ({ panel, prefs } = await openNotificationPreferences(page, profile, fixture, `${role} ${profile} reload`));
  issue = await issueCreatedRuleControls(prefs);
  assert(await issue.severity.inputValue() === contract.saved_severity,
    `${role} ${profile} notification preference did not persist after reload`);

  let restoredSeverity = null;
  if (contract.restored_severity) {
    restoredSeverity = contract.restored_severity;
    await issue.severity.selectOption(restoredSeverity);
    saveResponses.push(await saveNotificationPreferenceSeverity(
      page,
      prefs,
      restoredSeverity,
      `${role} ${profile} restored`,
    ));
    await closeNotificationPreferencePanel(page, panel, profile, `${role} ${profile} restored`);
    await page.reload({ waitUntil: "domcontentloaded" });
    ({ panel, prefs } = await openNotificationPreferences(page, profile, fixture, `${role} ${profile} restore reload`));
    issue = await issueCreatedRuleControls(prefs);
    assert(await issue.severity.inputValue() === restoredSeverity,
      `${role} ${profile} notification preference restoration did not persist after reload`);
  }
  await closeNotificationPreferencePanel(page, panel, profile, `${role} ${profile} final`);
  return {
    initial_severity: contract.initial_severity,
    reloaded_saved_severity: contract.saved_severity,
    restored_severity: restoredSeverity,
    save_responses: saveResponses,
    touch_targets: touchTargets,
  };
}

async function notificationBadgeState(page, profile) {
  const badge = page.locator(profile === "mobile" ? "#mobile-notification-badge" : "#notification-badge");
  return badge.evaluate((element) => ({
    hidden: element.hidden,
    text: element.textContent?.trim() || "",
  }));
}

function assertSaneNotificationBadge(badge, label) {
  assert(typeof badge.hidden === "boolean", `${label} notification badge hidden state was unavailable`);
  assert(
    badge.hidden || /^(?:[1-9]\d*|99\+)$/.test(badge.text),
    `${label} notification badge did not expose a valid unread count`,
  );
}

async function assertPostSavePreferenceDelivery(page, fixture, evidence) {
  const delivery = fixture.phase_two.preference_delivery;
  assert(evidence && typeof evidence === "object", "Post-save preference delivery evidence was missing");
  assert(evidence.triggered_at_ms === delivery.occurred_at_ms,
    "Post-save preference delivery did not use the frozen fixture timestamp");
  assert(Number.isSafeInteger(evidence.delivery_badge_count) && evidence.delivery_badge_count > 0,
    "Post-save preference delivery did not retain an eligible inbox badge");
  await page.reload({ waitUntil: "domcontentloaded" });
  await openPrimary(page, "desktop", "map");
  await visible(page.locator("#map-grid"), "map after post-save preference delivery");
  await selectGarden(page, "desktop", fixture.gardens.alpha.id);

  const today = await openToday(page, "desktop");
  await visible(today.getByText(delivery.eligible.title, { exact: true }),
    "eligible delivery notification in Today");
  assert(await today.getByText(delivery.ineligible.title, { exact: true }).count() === 0,
    "ineligible delivery notification leaked into Today");

  const badge = await notificationBadgeState(page, "desktop");
  assertSaneNotificationBadge(badge, "post-save preference delivery");
  assert(!badge.hidden && Number(badge.text) >= 1,
    "post-save preference delivery badge did not expose eligible inbox work");

  const notification = await openNotifications(page, "desktop");
  await visible(notification.panel.getByText(delivery.eligible.body, { exact: true }),
    "eligible delivery notification in inbox");
  assert(await notification.panel.getByText(delivery.ineligible.body, { exact: true }).count() === 0,
    "ineligible delivery notification leaked into inbox");
  await page.keyboard.press("Escape");
  await notification.panel.waitFor({ state: "hidden" });
}

async function exerciseNotificationSettingsRace(page, context, fixture) {
  let { panel } = await openNotifications(page, "desktop");
  await panel.locator(".notification-settings-btn").click();
  let prefs = panel.locator(".notification-prefs-form");
  await visible(prefs, "initial Alpha notification settings");
  assert(await prefs.locator("#notification-prefs-email-address").inputValue()
    === "complete-phase-2@example.invalid", "Initial Alpha notification settings were unavailable");
  await closeNotificationSettingsWithKeyboard(page, panel, "initial Alpha");

  const betaGardenId = fixture.gardens.beta.id;
  let betaSettingsHeld = false;
  let betaSettingsReleased = false;
  let releaseBetaSettings;
  const holdBetaSettings = new Promise((resolve) => {
    releaseBetaSettings = resolve;
  });
  const handler = async (route) => {
    const request = route.request();
    const gardenId = Number(request.headers()["x-garden-id"]);
    if (request.method() === "GET" && gardenId === betaGardenId) {
      betaSettingsHeld = true;
      await holdBetaSettings;
      betaSettingsReleased = true;
    }
    await route.fallback();
  };
  await context.route("**/api/notifications/preferences", handler);
  try {
    await selectGarden(page, "desktop", betaGardenId);
    ({ panel } = await openNotifications(page, "desktop"));
    await panel.locator(".notification-settings-btn").click();
    await waitFor(() => betaSettingsHeld, "held Beta notification settings response");
    await selectGarden(page, "desktop", fixture.gardens.alpha.id);
    await waitFor(async () => await panel.isHidden(), "Alpha garden switch to close Beta notification settings");
    releaseBetaSettings();
    await waitFor(() => betaSettingsReleased, "released Beta notification settings response");
  } finally {
    if (!betaSettingsReleased) releaseBetaSettings();
    await context.unroute("**/api/notifications/preferences", handler);
  }

  ({ panel } = await openNotifications(page, "desktop"));
  await panel.locator(".notification-settings-btn").click();
  prefs = panel.locator(".notification-prefs-form");
  await visible(prefs, "Alpha notification settings after A/B/A race");
  assert(await prefs.locator("#notification-prefs-email-address").inputValue()
    === "complete-phase-2@example.invalid", "Delayed Beta settings replaced Alpha notification settings");
  await closeNotificationSettingsWithKeyboard(page, panel, "Alpha after A/B/A race");
}

async function exerciseDelayedGardenRequestRace(page, context, fixture, {
  assertAlphaDom,
  alphaStart,
  betaStart,
  endpointPattern,
  label,
}) {
  const betaGardenId = fixture.gardens.beta.id;
  let betaRequestHeld = false;
  let betaResponseReleased = false;
  let releaseBetaResponse;
  const heldResponse = new Promise((resolve) => {
    releaseBetaResponse = resolve;
  });
  const handler = async (route) => {
    const request = route.request();
    const gardenId = Number(request.headers()["x-garden-id"]);
    if (request.method() === "GET" && gardenId === betaGardenId) {
      betaRequestHeld = true;
      await heldResponse;
      betaResponseReleased = true;
    }
    await route.fallback();
  };
  await context.route(endpointPattern, handler);
  try {
    await betaStart();
    await waitFor(() => betaRequestHeld, `held Beta ${label} response`);
    await selectGarden(page, "desktop", fixture.gardens.alpha.id);
    if (alphaStart) await alphaStart();
    await assertAlphaDom();
    releaseBetaResponse();
    await waitFor(() => betaResponseReleased, `released Beta ${label} response`);
    await assertAlphaDom();
  } finally {
    if (!betaResponseReleased) releaseBetaResponse();
    await context.unroute(endpointPattern, handler);
  }
}

async function exerciseTasksCalendarRace(page, context, fixture) {
  const alphaTask = taskTitle(fixture, "viewer_read_only");
  const betaTask = taskTitle(fixture, "rain_outdoor");
  await selectGarden(page, "desktop", fixture.gardens.alpha.id);
  await openTasks(page, "desktop");
  await visible(taskCard(page, alphaTask.title), "Alpha task before Tasks A/B/A race");
  await exerciseDelayedGardenRequestRace(page, context, fixture, {
    alphaStart: async () => openTasks(page, "desktop"),
    assertAlphaDom: async () => {
      await visible(taskCard(page, alphaTask.title), "Alpha task after held Beta Tasks response");
      assert(await taskCard(page, betaTask.title).count() === 0,
        "Stale Beta task DOM replaced Alpha after Tasks A/B/A race");
    },
    betaStart: async () => {
      await selectGarden(page, "desktop", fixture.gardens.beta.id, { waitForSettle: false });
    },
    endpointPattern: "**/api/tasks**",
    label: "Tasks",
  });

  await selectGarden(page, "desktop", fixture.gardens.alpha.id);
  await openCalendar(page, "desktop");
  await visible(page.locator(".fc-event").filter({
    hasText: fixture.phase_two.calendar.seeded_title,
  }).first(), "Alpha calendar event before Calendar A/B/A race");
  await exerciseDelayedGardenRequestRace(page, context, fixture, {
    alphaStart: async () => openCalendar(page, "desktop"),
    assertAlphaDom: async () => {
      await visible(page.locator(".fc-event").filter({
        hasText: fixture.phase_two.calendar.seeded_title,
      }).first(), "Alpha calendar event after held Beta Calendar response");
      assert(await page.locator(".fc-event").filter({ hasText: betaTask.title }).count() === 0,
        "Stale Beta calendar DOM replaced Alpha after Calendar A/B/A race");
    },
    betaStart: async () => {
      await selectGarden(page, "desktop", fixture.gardens.beta.id, { waitForSettle: false });
    },
    endpointPattern: "**/api/calendar/events**",
    label: "Calendar",
  });
}

async function exerciseCalendarSubscriptionRace(page, context, fixture, label) {
  await exerciseDelayedGardenRequestRace(page, context, fixture, {
    alphaStart: async () => openCalendar(page, "desktop"),
    assertAlphaDom: async () => {
      const alphaSubscription = page.locator(".calendar-subscription-item").filter({ hasText: label });
      await visible(alphaSubscription, "Alpha calendar subscription after held Beta response");
      assert(await alphaSubscription.count() === 1,
        "Stale subscription DOM duplicated or removed the Alpha subscription after A/B/A race");
    },
    betaStart: async () => {
      await selectGarden(page, "desktop", fixture.gardens.beta.id, { waitForSettle: false });
    },
    endpointPattern: "**/api/calendar/subscriptions**",
    label: "Calendar subscriptions",
  });
}

async function exerciseNotifications(page, fixture, oracle, onPreferencesSaved) {
  const contract = oracle?.phase_two?.fixture?.notification_persistence?.["admin:desktop"];
  assert(contract && typeof contract === "object", "Missing admin desktop notification preference oracle");
  let { panel, trigger } = await openNotifications(page, "desktop");
  await visible(panel.getByText("Alpha phase 2 scoped notification.", { exact: true }),
    "Alpha scoped notification");
  assert(await panel.getByText("Beta phase 2 scoped notification.", { exact: true }).count() === 0,
    "Beta notification leaked into Alpha inbox");
  const notificationMain = panel.locator(".notification-item-main").first();
  await visible(notificationMain, "keyboard notification navigation control");
  assert(await notificationMain.evaluate((element) => element.tagName === "BUTTON"),
    "Notification navigation was not exposed as a native button");
  await notificationMain.focus();
  assert(await notificationMain.evaluate((element) => document.activeElement === element),
    "Notification navigation button did not receive keyboard focus");
  const inboxTab = panel.locator("#notification-tab-inbox");
  await inboxTab.focus();
  await inboxTab.press("ArrowRight");
  await waitFor(async () => await panel.locator("#notification-tab-log").getAttribute("aria-selected") === "true",
    "notification keyboard tab change");
  await panel.locator("#notification-tab-log").press("Home");
  await waitFor(async () => await inboxTab.getAttribute("aria-selected") === "true",
    "notification keyboard Home tab change");
  await page.keyboard.press("Escape");
  await panel.waitFor({ state: "hidden" });
  await waitFor(async () => await trigger.evaluate((element) => document.activeElement === element),
    "notification trigger focus return");

  ({ panel } = await openNotifications(page, "desktop"));
  await panel.locator(".notification-settings-btn").click();
  let prefs = panel.locator(".notification-prefs-form");
  await visible(prefs, "notification preferences");
  const cancel = prefs.locator(".notification-prefs-cancel");
  await cancel.focus();
  await cancel.press("Enter");
  await visible(panel.locator("#notification-tab-inbox"), "notification list after settings cancel");
  await waitFor(
    async () => await panel.locator(".notification-settings-btn").evaluate(
      (element) => document.activeElement === element,
    ),
    "notification settings cancel focus return",
  );
  await panel.locator(".notification-settings-btn").click();
  prefs = panel.locator(".notification-prefs-form");
  await visible(prefs, "reopened notification preferences");
  assert(await prefs.locator("#notification-prefs-email-address").inputValue()
    === "complete-phase-2@example.invalid", "Notification email preference was lost");
  const globalToggles = prefs.locator(".notification-prefs-toggle");
  assert(await globalToggles.count() === 2, "Notification capability toggles were incomplete");
  const emailCapability = globalToggles.nth(1);
  assert(await emailCapability.getAttribute("aria-pressed") === "false",
    "Email delivery unexpectedly started enabled before the UI save");
  await emailCapability.click();
  assert(await emailCapability.getAttribute("aria-pressed") === "true",
    "Email delivery toggle did not change before preference save");
  const issue = await issueCreatedRuleControls(prefs);
  assert(await issue.inbox.getAttribute("aria-pressed") === "true",
    "Initial canonical issue-created attention rule was not projected into notification settings");
  assert(await issue.digest.getAttribute("aria-pressed") === "false",
    "Initial issue-created digest channel did not start disabled");
  assert(await issue.severity.inputValue() === contract.initial_severity,
    "Initial issue-created severity did not start at low");
  await issue.digest.click();
  assert(await issue.digest.getAttribute("aria-pressed") === "true",
    "Issue-created digest channel did not toggle before preference save");
  await issue.severity.selectOption(contract.saved_severity);
  assert(await issue.severity.inputValue() === contract.saved_severity,
    "Issue-created severity did not change before preference save");
  await prefs.locator("#notification-prefs-digest-frequency").selectOption("weekly");
  const quietInputs = prefs.locator("input[type='time']");
  assert(await quietInputs.nth(0).inputValue() === "22:15", "Quiet start minute was not preserved");
  assert(await quietInputs.nth(1).inputValue() === "07:45", "Quiet end minute was not preserved");
  await quietInputs.nth(0).fill("22:30");
  await quietInputs.nth(1).fill("07:15");
  const preferenceSave = page.waitForResponse((response) => (
    response.request().method() === "PUT"
      && new URL(response.url()).pathname === "/api/notifications/preferences"
  ));
  await prefs.locator(".btn-primary").click();
  const saved = await preferenceSave;
  assert(saved.status() === 200, `Notification preference save returned ${saved.status()}`);
  const savedRequestId = saved.headers()["x-request-id"] || "";
  assert(/^[A-Za-z0-9._-]{1,64}$/.test(savedRequestId),
    "Notification preference save lacked a request ID");
  await visible(panel.locator("#notification-tab-inbox"), "notification inbox after preference save");
  if (onPreferencesSaved) {
    await assertPostSavePreferenceDelivery(page, fixture, await onPreferencesSaved());
    ({ panel } = await openNotifications(page, "desktop"));
  }
  // Phase 2 explicit notification fixture only; preexisting inbox rows must remain untouched.
  const scopedItem = panel.locator(".notification-item").filter({
    hasText: fixture.phase_two.notification_fixture.body,
  });
  await visible(scopedItem, "Phase 2 explicit notification fixture before mute");
  await scopedItem.locator(".notification-item-mute").click();
  await scopedItem.waitFor({ state: "hidden" });
  await page.keyboard.press("Escape");
  await panel.waitFor({ state: "hidden" });
  await page.reload({ waitUntil: "domcontentloaded" });
  ({ panel } = await openNotificationPreferences(page, "desktop", fixture, "admin desktop preference reload"));
  const reloadedIssue = await issueCreatedRuleControls(panel.locator(".notification-prefs-form"));
  assert(await reloadedIssue.severity.inputValue() === contract.saved_severity,
    "Admin desktop notification preference did not persist after reload");
  await closeNotificationPreferencePanel(page, panel, "desktop", "admin desktop preference reload");
  return {
    initial_severity: contract.initial_severity,
    reloaded_saved_severity: contract.saved_severity,
    restored_severity: null,
    save_responses: [{ request_id: savedRequestId, status_code: saved.status() }],
    touch_targets: [],
  };
}

async function exercisePostMutationReload(page, fixture) {
  await page.reload({ waitUntil: "domcontentloaded" });
  await openPrimary(page, "desktop", "map");
  await visible(page.locator("#map-grid"), "map after Phase 2 reload");
  await selectGarden(page, "desktop", fixture.gardens.alpha.id);

  await openSubMode(page, "desktop", "activity", "journal", "#journal-tab-content");
  const journal = page.locator("#journal-list");
  await waitFor(async () => await journal.locator(".journal-card").count() > 0,
    "journal entries after Phase 2 reload");
  const completedBloomJournal = journal.locator(".journal-card").filter({
    hasText: fixture.phase_two.plant_names.bloom_desktop,
  }).first();
  await visible(completedBloomJournal, "completed desktop bloom journal card after reload");
  await visible(completedBloomJournal.locator(".journal-card-type").filter({ hasText: "Bloomed" }),
    "completed desktop bloom journal type after reload");
  const today = await openToday(page, "desktop");
  const completedBloom = taskTitle(fixture, "bloom_desktop");
  const active = today.locator('[data-testid="attention-today-section-needs_attention"]');
  assert(await active.getByText(completedBloom.title, { exact: true }).count() === 0,
    "Reloaded Today still treated the completed desktop bloom task as active");
  const noAction = today.locator('[data-testid="attention-today-section-no_action_needed"]');
  assert(!await noAction.evaluate((element) => element.hasAttribute("open")),
    "Reloaded Today expanded no-action history without user intent");
  assert(await noAction.getByText(completedBloom.title, { exact: true }).count() === 1,
    "Reloaded Today omitted the completed desktop bloom task from no-action history");
  await noAction.locator("summary").click();
  await visible(noAction.getByText(completedBloom.title, { exact: true }),
    "completed desktop bloom task in expanded no-action history");

  await openCalendar(page, "desktop");
  await visible(
    page.locator(".fc-event").filter({ hasText: fixture.phase_two.calendar.seeded_title }).first(),
    "seeded calendar event after reload",
  );

  const badge = await notificationBadgeState(page, "desktop");
  assertSaneNotificationBadge(badge, "reloaded desktop");
  const notification = await openNotifications(page, "desktop");
  const panel = notification.panel;
  assert(await panel.getByText("Alpha phase 2 scoped notification.", { exact: true }).count() === 0,
    "Muted legacy issue-created notification returned after reload");
  await panel.locator(".notification-settings-btn").click();
  const prefs = panel.locator(".notification-prefs-form");
  await visible(prefs, "notification settings after reload");
  const issue = await issueCreatedRuleControls(prefs);
  assert(await issue.inbox.getAttribute("aria-pressed") === "false",
    "Reloaded notification settings did not project the muted issue-created attention rule");
  assert(await issue.digest.getAttribute("aria-pressed") === "true"
    && await issue.severity.inputValue() === "normal",
  "Reloaded notification settings did not retain the issue-created digest and severity projection");
  const quietInputs = prefs.locator("input[type='time']");
  assert(await quietInputs.nth(0).inputValue() === "22:30"
    && await quietInputs.nth(1).inputValue() === "07:15",
  "Reloaded notification settings did not retain canonical digest quiet hours");
  await closeNotificationSettingsWithKeyboard(page, panel, "reloaded preferences");
}

async function exerciseMobileCalendarAndNotifications(page, fixture) {
  await openCalendar(page, "mobile");
  const calendarContent = page.locator("#calendar-tab-content");
  assert(await calendarContent.evaluate((element) => !element.hidden && !element.hasAttribute("inert")),
    "Mobile calendar content was not interactive");
  for (const mode of ["week", "agenda", "month"]) {
    const button = page.locator(`[data-calendar-view='${mode}']`);
    await visible(button, `mobile calendar ${mode} view control`);
    await button.click();
    await waitFor(async () => await button.evaluate((element) => element.classList.contains("active")),
      `mobile calendar ${mode} view`);
  }
  const seededEvent = page.locator(".fc-event").filter({ hasText: fixture.phase_two.calendar.seeded_title }).first();
  await visible(seededEvent, "mobile seeded calendar event");
  await seededEvent.click();
  await visible(page.locator("#calendar-detail-panel").getByText(
    fixture.phase_two.plant_names.bloom_desktop,
    { exact: false },
  ), "mobile calendar event detail");
  await openPrimary(page, "mobile", "map");
  assert(await calendarContent.isHidden(),
    "Inactive mobile calendar content remained effectively visible");
  const sharedContent = page.locator("#plants-view");
  assert(await sharedContent.evaluate((element) => (
    element.hidden
      && element.hasAttribute("inert")
      && element.getAttribute("aria-hidden") === "true"
  )), "Inactive mobile shared-data view remained exposed to assistive technology");

  const { panel, trigger } = await openNotifications(page, "mobile");
  assert(await panel.getAttribute("role") === "dialog" && await panel.getAttribute("aria-modal") === "true",
    "Mobile notification panel did not expose dialog semantics");
  assert(await panel.getAttribute("aria-hidden") === "false" && !await panel.evaluate((element) => element.hasAttribute("inert")),
    "Open mobile notification panel retained hidden or inert semantics");
  await assertFocusInside(panel, "mobile notification panel");
  const utility = page.locator("#mobile-utility-sheet");
  assert(await utility.getAttribute("aria-hidden") === "true"
    && await utility.evaluate((element) => element.hasAttribute("inert")),
  "Mobile utility remained active behind notification panel");
  await page.keyboard.press("Escape");
  await panel.waitFor({ state: "hidden" });
  assert(await panel.getAttribute("aria-hidden") === "true"
    && await panel.evaluate((element) => element.hasAttribute("inert")),
  "Closed mobile notification panel did not become inert");
  await waitFor(async () => (
    await utility.getAttribute("aria-hidden") === "false"
      && !await utility.evaluate((element) => element.hasAttribute("inert"))
  ), "mobile utility restoration after notification close");
  await waitFor(async () => await trigger.evaluate((element) => document.activeElement === element),
    "mobile notification trigger focus return");
  await closeMobileUtility(page);
}

async function exerciseMobilePartialGroupedAndSnooze(page, fixture) {
  await openTasks(page, "mobile");
  await completeGroupedFertilize(page, fixture);
  await exerciseImmediateSnoozeCorrection(page, fixture);
}

async function exerciseMobileNotificationPreferenceMutation(page, fixture, oracle) {
  return exercisePersonalNotificationPreferencePersistence(page, "mobile", "admin", fixture, oracle);
}

async function exerciseMobileHistoryReload(page, fixture) {
  await page.reload({ waitUntil: "domcontentloaded" });
  await openPrimary(page, "mobile", "map");
  await visible(page.locator("#map-grid"), "mobile map after history reload");
  await selectGarden(page, "mobile", fixture.gardens.alpha.id);
  await openSubMode(page, "mobile", "activity", "journal", "#journal-tab-content");
  const journal = page.locator("#journal-list");
  await waitFor(async () => await journal.locator(".journal-card").count() > 0,
    "mobile journal entries after reload");
  await visible(journal.locator(".journal-card").filter({
    hasText: fixture.phase_two.plant_names.fertilize_a,
  }).first(), "mobile grouped fertilize journal after reload");
  await visible(journal.locator(".journal-card").filter({
    hasText: fixture.phase_two.plant_names.bloom_mobile,
  }).first(), "mobile bloom journal after reload");

  const today = await openToday(page, "mobile");
  const noAction = today.locator('[data-testid="attention-today-section-no_action_needed"]');
  const completedBloom = taskTitle(fixture, "bloom_mobile");
  assert(await noAction.getByText(completedBloom.title, { exact: true }).count() === 1,
    "Mobile history reload omitted the completed bloom task");
  await noAction.locator("summary").click();
  await visible(noAction.getByText(completedBloom.title, { exact: true }),
    "mobile expanded no-action history after reload");
  await page.locator("[data-testid='attention-today-mobile-close']").click();
}

async function exerciseToday(page, profile, fixture) {
  const surface = await openToday(page, profile);
  const manual = taskTitle(fixture, "stale_manual_water");
  const generated = taskTitle(fixture, "stale_generated_water");
  const active = surface.locator('[data-testid="attention-today-section-needs_attention"]');
  await visible(active.getByText(manual.title, { exact: true }), `${profile} manual overdue Today item`);
  assert(await active.getByText(generated.title, { exact: true }).count() === 0,
    `${profile} stale generated watering remained active in Today`);
  const noAction = surface.locator('[data-testid="attention-today-section-no_action_needed"]');
  await visible(noAction.locator("summary"), `${profile} no-action-needed row`);
  assert(!await noAction.evaluate((element) => element.hasAttribute("open")),
    `${profile} no-action-needed row was expanded by default`);
  assert(!await noAction.getByText(generated.title, { exact: true }).isVisible(),
    `${profile} expired watering history was visible while collapsed`);
  await noAction.locator("summary").click();
  await visible(
    noAction.getByText(generated.title, { exact: true }),
    `${profile} expired watering history`,
  );
  await noAction.locator("summary").click();
  if (profile === "mobile") {
    await page.locator("[data-testid='attention-today-mobile-close']").click();
  }
}

async function runWeatherCheck(page) {
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/weather/check"
  ));
  await page.locator("#weather-dashboard .weather-check-btn").click();
  const response = await responsePromise;
  assert(response.status() === 200, `Weather check returned ${response.status()}`);
  return response.json();
}

function assertDeduplicatedWeatherCheck(result, label) {
  assert(result?.alerts_created === 0 && result?.alerts_skipped >= 1,
    `${label} created or failed to deduplicate a logical weather alert`);
}

async function runConcurrentWeatherChecks(page, profile, garden, recorder) {
  const peer = await page.context().newPage();
  recorder.attachPage(peer);
  try {
    await peer.goto(page.url(), { waitUntil: "domcontentloaded" });
    await openPrimary(peer, profile, "map");
    await visible(peer.locator("#map-grid"), "concurrent weather peer map surface");
    await selectGarden(peer, profile, garden.id);
    await openCare(peer, profile);
    const weatherPath = "/api/weather/check";
    const waitForCheck = (browserPage) => browserPage.waitForResponse((response) => (
      response.request().method() === "POST"
        && new URL(response.url()).pathname === weatherPath
    ));
    const currentResponse = waitForCheck(page);
    const peerResponse = waitForCheck(peer);
    await Promise.all([
      page.locator("#weather-dashboard .weather-check-btn").click(),
      peer.locator("#weather-dashboard .weather-check-btn").click(),
    ]);
    const responses = await Promise.all([currentResponse, peerResponse]);
    const results = await Promise.all(responses.map(async (response) => ({
      ...(await response.json()),
      status: response.status(),
    })));
    assert(results.length === 2 && results.every((result) => (
      result.status === 200 && result.alerts_created === 0 && result.alerts_skipped >= 1
    )), "Concurrent visible weather checks created or failed to deduplicate a logical alert");
  } finally {
    await peer.close();
  }
}

async function exerciseWeather(page, profile, fixture, recorder) {
  await selectGarden(page, profile, fixture.gardens.beta.id);
  await openCare(page, profile);
  const firstCheck = await runWeatherCheck(page);
  assert(firstCheck.alerts_created === 1 && firstCheck.alerts_skipped === 0,
    "Initial weather check did not create exactly one logical rain alert");
  const rain = page.locator(".weather-alert-card").filter({ hasText: /Heavy rain:.*expected/i }).first();
  await visible(rain, `${profile} rain alert`);

  const today = await openToday(page, profile);
  const weatherWarnings = today.locator('[data-testid="attention-today-section-warnings"]');
  await visible(weatherWarnings.getByText(/Heavy rain/i).first(),
    `${profile} weather refresh in Today`);
  if (profile === "mobile") {
    await page.locator("[data-testid='attention-today-mobile-close']").click();
  }
  await openTasks(page, profile);
  const week = page.locator("[data-tasks-view='week']").first();
  await visible(week, `${profile} tasks week view`);
  await week.click();
  await waitFor(async () => await week.evaluate((element) => element.classList.contains("active")),
    `${profile} tasks week view refresh`);
  await visible(taskCard(page, taskTitle(fixture, "rain_outdoor").title),
    `${profile} weather refresh in tasks`);
  await openCalendarAgenda(page, profile);
  await visible(page.locator(".fc-event").filter({ hasText: /Heavy rain/i }).first(),
    `${profile} weather refresh in calendar`);
  const badgeAfterWeather = await notificationBadgeState(page, profile);
  assertSaneNotificationBadge(badgeAfterWeather, `${profile} weather`);
  const notification = await openNotifications(page, profile);
  await visible(notification.panel.locator(".notification-item").filter({ hasText: /Heavy rain/i }).first(),
    `${profile} weather refresh in notifications`);
  await page.keyboard.press("Escape");
  await notification.panel.waitFor({ state: "hidden" });
  if (profile === "mobile") await closeMobileUtility(page);

  await openCare(page, profile);
  const rainAfterRefresh = page.locator(".weather-alert-card").filter({ hasText: /Heavy rain:.*expected/i }).first();
  await visible(rainAfterRefresh, `${profile} rain alert after surface refresh`);
  await rainAfterRefresh.locator(".weather-alert-dismiss").click();
  await rainAfterRefresh.waitFor({ state: "hidden" });
  const todayAfterDismiss = await openToday(page, profile);
  const warningsAfterDismiss = todayAfterDismiss.locator(
    '[data-testid="attention-today-section-warnings"]',
  );
  assert(await warningsAfterDismiss.getByText(/Heavy rain/i).count() === 0,
    `${profile} dismissed weather alert remained an active Today warning`);
  if (profile === "mobile") {
    await page.locator("[data-testid='attention-today-mobile-close']").click();
  }
  await openCare(page, profile);
  const repeatedCheck = await runWeatherCheck(page);
  assertDeduplicatedWeatherCheck(repeatedCheck, "Repeated weather check");
  assert(await page.locator(".weather-alert-card").filter({ hasText: /Heavy rain/i }).count() === 0,
    "Repeated weather check resurrected a dismissed rain alert");
  await runConcurrentWeatherChecks(page, profile, fixture.gardens.beta, recorder);
  await selectGarden(page, profile, fixture.gardens.alpha.id);
}

async function exerciseMobilePlotSheetKeyboard(page, fixture) {
  await openPrimary(page, "mobile", "map");
  const plot = page.locator(`.plot[data-plot-id='${fixture.phase_two.plot_ids.alpha}']`);
  await visible(plot, "mobile plot for keyboard sheet");
  await plot.click();
  const sheet = page.locator(".bottom-sheet");
  await visible(sheet, "mobile plot sheet dialog");
  assert(await sheet.getAttribute("role") === "dialog"
    && await sheet.getAttribute("aria-modal") === "true",
  "Mobile plot sheet did not expose modal dialog semantics");
  await assertFocusInside(sheet, "mobile plot sheet");

  const handle = sheet.locator(".sheet-handle-bar");
  assert(await handle.evaluate((element) => element.tagName === "BUTTON"),
    "Mobile plot sheet handle was not keyboard operable");
  const initialSnap = await sheet.getAttribute("data-snap-state");
  await handle.focus();
  await handle.press("Enter");
  await waitFor(async () => await sheet.getAttribute("data-snap-state") !== initialSnap,
    "mobile plot sheet keyboard resize");

  const sectionToggle = sheet.locator(".drawer-section-header").first();
  await visible(sectionToggle, "mobile plot collapsible control");
  await sectionToggle.focus();
  await sectionToggle.press("Enter");
  assert(await sectionToggle.getAttribute("aria-expanded") === "false",
    "Mobile plot section did not collapse from the keyboard");
  await page.keyboard.press("Escape");
  await sheet.waitFor({ state: "hidden" });
  await waitFor(async () => await plot.evaluate((element) => document.activeElement === element),
    "mobile plot sheet focus return");
}

async function completeMobileQuickActions(page, fixture) {
  await openPrimary(page, "mobile", "map");
  const fab = page.locator("#mobile-fab");
  await fab.click();
  const sheet = page.locator("#mobile-quick-actions");
  await visible(sheet, "mobile Quick Actions");
  assert(await sheet.getAttribute("role") === "dialog" && await sheet.getAttribute("aria-modal") === "true",
    "Mobile Quick Actions did not expose dialog semantics");
  assert(await sheet.getAttribute("aria-hidden") === "false"
    && !await sheet.evaluate((element) => element.hasAttribute("inert")),
  "Open mobile Quick Actions retained hidden or inert semantics");
  await waitFor(
    async () => await sheet.evaluate((element) => element.contains(document.activeElement)),
    "mobile Quick Actions focus",
  );
  await assertFocusInside(sheet, "mobile Quick Actions");
  const background = page.locator("main.content-shell");
  assert(await background.evaluate((element) => element.hasAttribute("inert")),
    "Mobile Quick Actions did not inert the main background surface");
  await page.keyboard.press("Escape");
  await waitFor(async () => (
    await sheet.getAttribute("aria-hidden") === "true"
      && await sheet.evaluate((element) => element.hasAttribute("inert"))
  ), "mobile Quick Actions close semantics");
  await waitFor(async () => !await background.evaluate((element) => element.hasAttribute("inert")),
    "mobile Quick Actions background restoration");
  await waitFor(async () => await fab.evaluate((element) => document.activeElement === element),
    "mobile Quick Actions FAB focus restoration");
  await fab.click();
  await visible(sheet, "reopened mobile Quick Actions");
  await sheet.locator("[data-quick-action='complete-task']").click();
  const grouped = taskTitle(fixture, "fertilize_grouped");
  await sheet.locator(".quick-action-task-search").fill(grouped.title);
  await sheet.locator(".quick-action-task-item").filter({ hasText: grouped.title }).click();
  await assertGroupedCompletionSelectionRequired(
    page.locator(".task-completion-dialog").last(),
    fixture,
    "Quick Actions",
  );
  const fertilize = taskTitle(fixture, "fertilize_mobile");
  await sheet.locator(".quick-action-back").click();
  await sheet.locator("[data-quick-action='snooze-task']").click();
  await sheet.locator(".quick-action-task-search").fill(fertilize.title);
  const changeFertilizeDate = sheet.getByRole("button", {
    name: new RegExp(`${fertilize.title}: Change date`, "i"),
  });
  await visible(changeFertilizeDate, "mobile fertilize Quick Action manual date");
  await changeFertilizeDate.click();
  let dateDialog = page.locator(".confirm-dialog").filter({
    has: page.locator("input[type='date']"),
  }).last();
  await visible(dateDialog, "mobile Quick Actions date dialog");
  assert(await sheet.getAttribute("aria-hidden") === "true"
    && await sheet.evaluate((element) => element.hasAttribute("inert")),
  "Quick Actions sheet remained exposed behind its date dialog");
  await dateDialog.locator(".confirm-no").click();
  await dateDialog.waitFor({ state: "hidden" });
  await waitFor(async () => (
    await sheet.getAttribute("aria-hidden") === "false"
      && !await sheet.evaluate((element) => element.hasAttribute("inert"))
  ), "Quick Actions date-dialog parent restoration after cancel");
  await assertFocusInside(sheet, "Quick Actions after date-dialog cancel");

  await changeFertilizeDate.click();
  dateDialog = page.locator(".confirm-dialog").filter({
    has: page.locator("input[type='date']"),
  }).last();
  await visible(dateDialog, "mobile Quick Actions date dialog before submit");
  await dateDialog.locator("input[type='date']").fill(fixture.phase_two.date);
  await dateDialog.locator(".confirm-yes").click();
  await dateDialog.waitFor({ state: "hidden" });
  await waitFor(async () => (
    await sheet.getAttribute("aria-hidden") === "false"
      && !await sheet.evaluate((element) => element.hasAttribute("inert"))
  ), "Quick Actions date-dialog parent restoration after submit");
  await assertFocusInside(sheet, "Quick Actions after date-dialog submit");
  await sheet.locator(".quick-action-back").click();
  await sheet.locator("[data-quick-action='complete-task']").click();
  await sheet.locator(".quick-action-task-search").fill(fertilize.title);
  const fertilizeAction = sheet.locator(".quick-action-task-item").filter({ hasText: fertilize.title });
  await visible(fertilizeAction, "mobile fertilize Quick Action");
  await fertilizeAction.click();
  await fertilizeAction.waitFor({ state: "hidden" });
  await sheet.locator(".quick-action-back").click();
  await sheet.locator("[data-quick-action='complete-task']").click();
  const bloom = taskTitle(fixture, "bloom_mobile");
  await sheet.locator(".quick-action-task-search").fill(bloom.title);
  const bloomAction = sheet.locator(".quick-action-task-item").filter({ hasText: bloom.title });
  await bloomAction.click();
  let dialog = page.locator(".task-completion-dialog").last();
  await visible(dialog, "mobile bloom Quick Action dialog");
  assert(await sheet.getAttribute("aria-hidden") === "true"
    && await sheet.evaluate((element) => element.hasAttribute("inert")),
  "Quick Actions sheet remained exposed behind its completion dialog");
  await dialog.locator(".confirm-no").click();
  await dialog.waitFor({ state: "hidden" });
  await waitFor(async () => (
    await sheet.getAttribute("aria-hidden") === "false"
      && !await sheet.evaluate((element) => element.hasAttribute("inert"))
  ), "Quick Actions completion-dialog parent restoration after cancel");
  await assertFocusInside(sheet, "Quick Actions after completion-dialog cancel");

  await bloomAction.click();
  dialog = page.locator(".task-completion-dialog").last();
  await visible(dialog, "mobile bloom Quick Action dialog before submit");
  await dialog.locator(".task-completion-not-seen").click();
  await waitFor(async () => (
    await sheet.getAttribute("aria-hidden") === "false"
      && !await sheet.evaluate((element) => element.hasAttribute("inert"))
  ), "Quick Actions completion-dialog parent restoration after submit");
  await waitFor(
    async () => await sheet.locator(".quick-action-task-item").filter({ hasText: bloom.title }).count() === 0,
    "mobile bloom Quick Action refresh",
  );
  await page.keyboard.press("Escape");
}

async function exerciseEditorCalendar(page, fixture) {
  await openCalendarAgenda(page, "desktop");
  const task = taskTitle(fixture, "editor_prune");
  const event = page.locator(".fc-event").filter({ hasText: task.title }).first();
  await visible(event, "editor calendar task");
  await event.click();
  const detail = page.locator("#calendar-detail-panel");
  await detail.getByRole("button", { name: /^Complete$/i }).click();
  await waitFor(
    async () => await page.locator(".fc-event").filter({ hasText: task.title }).count() === 0,
    "editor calendar completion",
  );
}

async function exerciseEditorWeatherDeduplication(page, fixture) {
  await selectGarden(page, "desktop", fixture.gardens.beta.id);
  await openCare(page, "desktop");
  const repeatedCheck = await runWeatherCheck(page);
  assertDeduplicatedWeatherCheck(repeatedCheck, "Editor desktop weather check");
  const rain = page.locator(".weather-alert-card").filter({ hasText: /Heavy rain:.*expected/i }).first();
  await visible(rain, "editor user-scoped rain alert after administrator dismissal");

  const today = await openToday(page, "desktop");
  const weatherWarnings = today.locator('[data-testid="attention-today-section-warnings"]');
  await visible(weatherWarnings.getByText(/Heavy rain/i).first(),
    "editor weather refresh in Today");
  await openTasks(page, "desktop");
  const week = page.locator("[data-tasks-view='week']").first();
  await visible(week, "editor tasks week view");
  await week.click();
  await waitFor(async () => await week.evaluate((element) => element.classList.contains("active")),
    "editor tasks week view refresh");
  await visible(taskCard(page, taskTitle(fixture, "rain_outdoor").title),
    "editor weather refresh in tasks");
  await openCalendarAgenda(page, "desktop");
  await visible(page.locator(".fc-event:visible").filter({ hasText: /Heavy rain/i }).first(),
    "editor weather refresh in calendar");
  const badge = await notificationBadgeState(page, "desktop");
  assertSaneNotificationBadge(badge, "editor desktop weather");
  const notification = await openNotifications(page, "desktop");
  await visible(notification.panel.locator(".notification-item").filter({ hasText: /Heavy rain/i }).first(),
    "editor weather refresh in notifications");
  await page.keyboard.press("Escape");
  await notification.panel.waitFor({ state: "hidden" });
}

async function queuedOfflineTaskOperations(page) {
  return page.evaluate(async () => {
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open("gardenops-offline");
      request.onerror = () => reject(request.error || new Error("Offline queue could not open"));
      request.onsuccess = () => resolve(request.result);
    });
    try {
      const drafts = await new Promise((resolve, reject) => {
        const transaction = database.transaction("drafts", "readonly");
        const request = transaction.objectStore("drafts").getAll();
        request.onerror = () => reject(request.error || new Error("Offline drafts could not load"));
        request.onsuccess = () => resolve(request.result || []);
      });
      return drafts
        .filter((draft) => typeof draft?.type === "string" && draft.type.startsWith("task_"))
        .map((draft) => ({
          operation_id: String(draft.operation_id || ""),
          task_id: String(draft.payload?.task_id || ""),
          type: String(draft.type),
        }))
        .sort((left, right) => `${left.type}:${left.task_id}`.localeCompare(`${right.type}:${right.task_id}`));
    } finally {
      database.close();
    }
  });
}

async function markOfflineTaskDraftFailed(page, taskId, errorMessage) {
  return page.evaluate(async ({ targetTaskId, message }) => {
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open("gardenops-offline");
      request.onerror = () => reject(request.error || new Error("Offline queue could not open"));
      request.onsuccess = () => resolve(request.result);
    });
    try {
      const draftId = await new Promise((resolve, reject) => {
        const transaction = database.transaction("drafts", "readwrite");
        const store = transaction.objectStore("drafts");
        const request = store.getAll();
        let matchedId = null;
        request.onerror = () => reject(request.error || new Error("Offline drafts could not load"));
        request.onsuccess = () => {
          const draft = (request.result || []).find((candidate) => (
            String(candidate?.payload?.task_id || "") === targetTaskId
          ));
          if (!draft) {
            transaction.abort();
            reject(new Error(`Offline task draft ${targetTaskId} was not found`));
            return;
          }
          draft.status = "failed";
          draft.retry_count = 5;
          draft.last_error = message;
          matchedId = draft.id;
          store.put(draft);
        };
        transaction.onerror = () => reject(
          transaction.error || new Error("Offline task failure state could not be saved"),
        );
        transaction.oncomplete = () => resolve(matchedId);
      });
      window.dispatchEvent(new CustomEvent("gardenops:offline-queue-changed"));
      return draftId;
    } finally {
      database.close();
    }
  }, { targetTaskId: taskId, message: errorMessage });
}

async function exerciseOfflineTask(page, fixture) {
  const completion = taskTitle(fixture, "editor_offline");
  const staleManual = taskTitle(fixture, "stale_manual_water");
  const prune = taskTitle(fixture, "prune_desktop");
  const remainingGroupedTitle = `Fertilize: ${fixture.phase_two.plant_names.fertilize_b}`;

  await page.context().setOffline(true);
  await openSubMode(page, "mobile", "activity", "tasks", "#tasks-tab-content");
  await visible(page.locator("#tasks-list .offline-data-state--unavailable"),
    "cold offline Tasks unavailable state");
  assert(await page.locator("#tasks-list .task-card").count() === 0,
    "Cold offline Tasks rendered false empty task data");

  await openPrimary(page, "mobile", "activity");
  const calendarButton = page.locator(
    "#sub-mode-calendar:visible, [data-sub-mode='calendar']:visible",
  ).first();
  await calendarButton.click();
  await visible(page.locator("#calendar-data-state.offline-data-state--unavailable"),
    "cold offline Calendar unavailable state");
  assert(await page.locator("#calendar-root").isHidden(),
    "Cold offline Calendar exposed a false empty calendar");

  await openPrimary(page, "mobile", "map");
  const fab = page.locator("#mobile-fab");
  await fab.click();
  const quickSheet = page.locator("#mobile-quick-actions");
  await quickSheet.locator("[data-quick-action='complete-task']").click();
  await visible(quickSheet.locator(".offline-data-state--unavailable"),
    "cold offline Quick Actions unavailable state");
  assert(await quickSheet.locator(".quick-action-task-item").count() === 0,
    "Cold offline Quick Actions rendered false empty task choices");
  await page.keyboard.press("Escape");
  await page.context().setOffline(false);
  await waitFor(async () => page.evaluate(() => navigator.onLine),
    "editor browser to return online after cold-state checks");

  await openTasks(page, "mobile");
  const typeFilter = page.locator("#tasks-filter-type");
  const filteredResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
      && new URL(response.url()).pathname === "/api/tasks"
      && new URL(response.url()).searchParams.get("task_type") === "prune"
  ));
  await typeFilter.selectOption("prune");
  await filteredResponse;
  await waitFor(async () => await page.locator("#tasks-list .task-card").count() > 0,
    "warm filtered task cache");
  assert(await page.locator("#tasks-list .task-card-type").evaluateAll(
    (elements) => elements.every((element) => element.textContent?.trim() === "Prune"),
  ), "Online task filter returned mixed task types");
  const unfilteredResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
      && new URL(response.url()).pathname === "/api/tasks"
      && !new URL(response.url()).searchParams.has("task_type")
  ));
  await typeFilter.selectOption("");
  await unfilteredResponse;

  const completionCard = taskCard(page, completion.title);
  const staleManualCard = taskCard(page, staleManual.title);
  const groupedCard = taskCard(page, remainingGroupedTitle);
  await visible(completionCard, "offline editor completion task");
  await visible(staleManualCard, "offline manual watering snooze task");
  await visible(groupedCard, "offline grouped fertilize reschedule task");
  await openCalendarAgenda(page, "mobile");
  await visible(
    page.locator(".fc-event:visible").filter({ hasText: prune.title }).first(),
    "calendar skip task before going offline",
  );
  await openTasks(page, "mobile");

  const replayedActions = [];
  const replayListener = (request) => {
    const match = /^\/api\/tasks\/([^/]+)\/action$/.exec(new URL(request.url()).pathname);
    if (request.method() !== "POST" || !match) return;
    try {
      const body = request.postDataJSON();
      if (typeof body?.action === "string") {
        replayedActions.push({
          action: body.action,
          operation_id: request.headers()["x-offline-operation-id"] || "",
          task_id: match[1],
        });
      }
    } catch {
      // The post body is only observational evidence; the durable operation check remains authoritative.
    }
  };
  page.on("request", replayListener);
  try {
    await page.context().setOffline(true);
    await typeFilter.selectOption("prune");
    await visible(page.locator("#tasks-list .offline-data-state--cached"),
      "warm offline filtered Tasks cache state");
    assert(await page.locator("#tasks-list .task-card-type").evaluateAll(
      (elements) => elements.length > 0
        && elements.every((element) => element.textContent?.trim() === "Prune"),
    ), "Warm offline task cache ignored the active type filter");
    await typeFilter.selectOption("");
    await visible(completionCard, "unfiltered warm offline task cache");

    await completionCard.getByRole("button", { name: /^Complete$/i }).click();
    await waitFor(async () => await completionCard.getAttribute("data-offline-task-state") === "queued",
      "offline completion queued state");
    assert(await completionCard.locator(".task-card-actions button").count() === 0,
      "Queued offline completion retained conflicting task controls");
    await staleManualCard.getByRole("button", { name: /^Snooze$/i }).click();
    await waitFor(async () => await staleManualCard.getAttribute("data-offline-task-state") === "queued",
      "offline snooze queued state");
    await groupedCard.getByRole("button", { name: /^Reschedule$/i }).click();
    const rescheduleDialog = page.locator(".confirm-dialog").filter({
      has: page.locator("input[type='date']"),
    }).last();
    await visible(rescheduleDialog, "offline reschedule date dialog");
    await rescheduleDialog.locator("input[type='date']").fill(fixture.phase_two.offline.reschedule_date);
    await rescheduleDialog.locator(".confirm-yes").click();
    await waitFor(async () => await groupedCard.getAttribute("data-offline-task-state") === "queued",
      "offline reschedule queued state");

    await openCalendarAgenda(page, "mobile");
    const pruneEvent = page.locator(".fc-event:visible").filter({ hasText: prune.title }).first();
    await visible(pruneEvent, "offline calendar skip task");
    await pruneEvent.click();
    await page.locator("#calendar-detail-panel").getByRole("button", { name: /^Skip$/i }).click();
    await waitFor(async () => await pruneEvent.getAttribute("data-offline-task-state") === "queued",
      "offline Calendar skip queued state");
    await visible(page.locator("#calendar-detail-panel .task-offline-state--queued"),
      "offline Calendar queued task detail");
    await waitFor(async () => {
      const count = await page.locator("#offline-indicator .offline-indicator-count").textContent();
      return /\b4\b/.test(count || "");
    }, "four queued offline task drafts");
    assert(replayedActions.length === 0, "Offline task actions reached the server before connectivity returned");
    await visible(page.locator("#toast-container").filter({ hasText: /saved/i }), "offline draft toast");

    await markOfflineTaskDraftFailed(
      page,
      fixture.phase_two.task_ids.editor_offline,
      "Deliberate journey sync failure",
    );
    await openTasks(page, "mobile");
    const failedCompletionCard = taskCard(page, completion.title);
    await waitFor(
      async () => await failedCompletionCard.getAttribute("data-offline-task-state") === "failed",
      "terminal offline task failure state",
    );
    await visible(page.locator("#offline-indicator .offline-failures"),
      "global failed offline work recovery");
    await visible(failedCompletionCard.locator(".task-offline-error").filter({
      hasText: "Deliberate journey sync failure",
    }), "per-task offline failure reason");
    await failedCompletionCard.locator(".task-offline-retry").click();
    await waitFor(
      async () => await failedCompletionCard.getAttribute("data-offline-task-state") === "queued",
      "offline task retry queued state",
    );

    await markOfflineTaskDraftFailed(
      page,
      fixture.phase_two.task_ids.stale_manual_water,
      "Deliberate discard recovery failure",
    );
    const failedSnoozeCard = taskCard(page, staleManual.title);
    await waitFor(
      async () => await failedSnoozeCard.getAttribute("data-offline-task-state") === "failed",
      "offline task discard failure state",
    );
    await failedSnoozeCard.locator(".task-offline-discard").click();
    const discardConfirmation = page.locator("[role='alertdialog']").last();
    await visible(discardConfirmation, "offline task discard confirmation");
    await discardConfirmation.locator(".confirm-yes").click();
    await waitFor(
      async () => await failedSnoozeCard.getAttribute("data-offline-task-state") === null,
      "offline task discard recovery",
    );
    await failedSnoozeCard.getByRole("button", { name: /^Snooze$/i }).click();
    await waitFor(
      async () => await failedSnoozeCard.getAttribute("data-offline-task-state") === "queued",
      "discarded offline task re-enqueue",
    );
    assert((await queuedOfflineTaskOperations(page)).length === 4,
      "Retry and discard recovery changed the atomic four-task queue size");

    const queuedOperations = await queuedOfflineTaskOperations(page);
    const expectedQueuedOperations = [
      { task_id: fixture.phase_two.task_ids.editor_offline, type: "task_complete" },
      { task_id: fixture.phase_two.task_ids.prune_desktop, type: "task_skip" },
      { task_id: fixture.phase_two.task_ids.stale_manual_water, type: "task_snooze" },
      { task_id: fixture.phase_two.task_ids.fertilize_grouped, type: "task_reschedule" },
    ].sort((left, right) => `${left.type}:${left.task_id}`.localeCompare(`${right.type}:${right.task_id}`));
    assert(queuedOperations.length === expectedQueuedOperations.length,
      "Offline task queue did not retain exactly four operation IDs before replay");
    assert(
      JSON.stringify(queuedOperations.map(({ operation_id: _operationId, ...operation }) => operation))
        === JSON.stringify(expectedQueuedOperations),
      "Offline task queue changed the expected action or target before replay",
    );
    assert(queuedOperations.every((operation) => (
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
        operation.operation_id,
      )
    )), "Offline task queue did not assign UUIDv4 operation IDs");
    assert(new Set(queuedOperations.map((operation) => operation.operation_id)).size === queuedOperations.length,
      "Offline task queue reused an operation ID before replay");

    await page.context().setOffline(false);
    const expectedActions = [
      { action: "complete", task_id: fixture.phase_two.task_ids.editor_offline },
      { action: "skip", task_id: fixture.phase_two.task_ids.prune_desktop },
      { action: "snooze", task_id: fixture.phase_two.task_ids.stale_manual_water },
      { action: "reschedule", task_id: fixture.phase_two.task_ids.fertilize_grouped },
    ].sort((left, right) => `${left.action}:${left.task_id}`.localeCompare(`${right.action}:${right.task_id}`));
    await waitFor(() => replayedActions.length === expectedActions.length, "offline task action replay");
    const observedActions = replayedActions
      .map((action) => ({ action: action.action, task_id: action.task_id }))
      .sort((left, right) => `${left.action}:${left.task_id}`.localeCompare(`${right.action}:${right.task_id}`));
    assert(JSON.stringify(observedActions) === JSON.stringify(expectedActions),
      "Offline task replay did not preserve complete, skip, snooze, and reschedule actions");
    const queuedByTask = new Map(queuedOperations.map((operation) => [operation.task_id, operation]));
    assert(replayedActions.every((action) => action.operation_id === queuedByTask.get(action.task_id)?.operation_id),
      "Offline task replay did not preserve queued operation IDs");
    await waitFor(async () => await page.locator("#offline-indicator").evaluate((element) => element.hidden),
      "offline task queue to drain");
    const remainingOperations = await queuedOfflineTaskOperations(page);
    assert(remainingOperations.length === 0,
      "Offline task replay retained operation IDs after the queue drained");
    return {
      queued_operations: queuedOperations,
      remaining_operations: remainingOperations,
      replayed_operations: replayedActions.sort((left, right) => (
        `${left.action}:${left.task_id}`.localeCompare(`${right.action}:${right.task_id}`)
      )),
    };
  } finally {
    page.off("request", replayListener);
    await page.evaluate(() => {
      Object.defineProperty(navigator, "onLine", { configurable: true, get: () => true });
      window.dispatchEvent(new Event("online"));
    });
  }
}

async function attemptForbiddenViewerTaskWrite(page, diagnostics, profile, fixture, task) {
  const beforeHttpErrors = diagnostics.httpErrors.length;
  const beforeConsoleErrors = diagnostics.consoleErrors.length;
  const response = await page.evaluate(async ({ gardenId, taskId }) => {
    const result = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/action`, {
      body: JSON.stringify({ action: "complete" }),
      credentials: "include",
      headers: {
        "content-type": "application/json",
        "x-garden-id": String(gardenId),
      },
      method: "POST",
    });
    await result.text();
    return { status: result.status };
  }, { gardenId: fixture.gardens.alpha.id, taskId: task.id });
  assert(response.status === 403, `${profile} viewer direct task write was not forbidden`);
  await waitFor(() => diagnostics.httpErrors.length === beforeHttpErrors + 1,
    `${profile} viewer direct forbidden write response`);
  const expectedError = `403 /api/tasks/${task.id}/action`;
  const directErrors = diagnostics.httpErrors.splice(beforeHttpErrors);
  assert(JSON.stringify(directErrors) === JSON.stringify([expectedError]),
    `${profile} viewer direct write did not produce the expected forbidden response`);
  await waitFor(
    () => diagnostics.consoleErrors.length === beforeConsoleErrors + 1,
    `${profile} viewer direct forbidden write console response`,
  );
  diagnostics.consoleErrors.splice(beforeConsoleErrors, 1);

  await page.waitForLoadState("networkidle");
  await page.reload({ waitUntil: "domcontentloaded" });
  await openPrimary(page, profile, "map");
  await selectGarden(page, profile, fixture.gardens.alpha.id);
  await openTasks(page, profile);
  const reloaded = taskCard(page, task.title);
  await visible(reloaded, `${profile} viewer task after forbidden direct write`);
  assert(!await reloaded.evaluate((element) => element.classList.contains("task-completed")),
    `${profile} viewer direct task write changed task state`);
  assert(await reloaded.locator(".task-card-actions button").count() === 0,
    `${profile} viewer task changed write affordances after forbidden direct write`);
}

async function exerciseViewer(page, diagnostics, profile, fixture) {
  await openTasks(page, profile);
  const task = taskTitle(fixture, "viewer_read_only");
  const card = taskCard(page, task.title);
  await visible(card, `${profile} viewer task`);
  assert(await card.locator(".task-card-actions button").count() === 0,
    `${profile} viewer received task write controls`);
  await attemptForbiddenViewerTaskWrite(page, diagnostics, profile, fixture, task);
  await openCalendarAgenda(page, profile);
  const event = page.locator(".fc-event:visible").filter({ hasText: task.title }).first();
  await visible(event, `${profile} viewer calendar task`);
  await event.click();
  assert(await page.locator("#calendar-detail-panel .calendar-detail-actions").count() === 0,
    `${profile} viewer received calendar task actions`);
  await page.waitForLoadState("networkidle");
  const personalCalendarView = profile === "desktop" ? "week" : "month";
  const calendarPreferenceSave = page.waitForResponse((response) => {
    if (response.request().method() !== "PATCH"
      || new URL(response.url()).pathname !== "/api/calendar/preferences") return false;
    try {
      return response.request().postDataJSON()?.default_view === personalCalendarView;
    } catch {
      return false;
    }
  });
  await page.locator(`[data-calendar-view='${personalCalendarView}']`).click();
  const calendarPreferenceResponse = await calendarPreferenceSave;
  assert(calendarPreferenceResponse.status() === 200,
    `${profile} viewer personal calendar preference save failed`);
  await page.reload({ waitUntil: "domcontentloaded" });
  await openPrimary(page, profile, "map");
  await selectGarden(page, profile, fixture.gardens.alpha.id);
  await openCalendar(page, profile);
  assert(await page.locator(`[data-calendar-view='${personalCalendarView}']`).evaluate(
    (element) => element.classList.contains("active"),
  ), `${profile} viewer personal calendar preference did not persist after reload`);
  const today = await openToday(page, profile);
  await visible(today.locator('[data-testid="attention-today-section-needs_attention"]'),
    `${profile} viewer Today affordance`);
  const settings = today.locator('[data-testid="attention-today-settings"]');
  assert(await settings.count() === 1,
    `${profile} viewer Today did not receive personal preference controls`);
  await settings.click();
  const preferences = page.getByRole("dialog", { name: /attention settings/i });
  await visible(preferences, `${profile} viewer personal attention preferences`);
  await preferences.getByTestId("attention-preferences-save").click();
  await preferences.waitFor({ state: "detached", timeout: 10000 });
  assert(await today.locator('[data-attention-action-kind="restore_attention_outcome"]').count() === 0,
    `${profile} viewer Today received restore controls`);
  const openTask = today.locator('[data-attention-action-kind="open_task"]').first();
  await visible(openTask, `${profile} viewer Today task navigation`);
  const targetTaskId = await openTask.getAttribute("data-attention-action-target-id");
  assert(targetTaskId, `${profile} viewer Today task navigation omitted its target`);
  await openTask.click();
  const navigatedCard = page.locator(`.task-card[data-task-id="${targetTaskId}"]`);
  await visible(navigatedCard, `${profile} viewer task after Today navigation`);
  assert(await navigatedCard.locator(".task-card-actions button").count() === 0,
    `${profile} viewer Today navigation exposed task write controls`);
  assert(await page.locator(".plot-popover, .drawer, .bottom-sheet").count() === 0,
    `${profile} viewer Today task navigation retained a map overlay`);
  if (profile === "mobile") {
    assert(await page.locator("#attention-today-mobile-sheet").getAttribute("aria-hidden") === "true",
      "Mobile Today task navigation retained the Today sheet");
  }
  await selectGarden(page, profile, fixture.gardens.beta.id);
  await openCare(page, profile);
  await visible(page.locator("#weather-dashboard"), `${profile} viewer Weather affordance`);
  assert(await page.locator("#weather-dashboard .weather-check-btn:visible").count() === 0,
    `${profile} viewer Weather received write controls`);
  assert(await page.locator("#weather-dashboard .weather-alert-dismiss:visible").count() === 0,
    `${profile} viewer Weather received alert dismissal controls`);
}

async function runProfile(options) {
  const run = options;
  const { artifactDir, baseUrl, browser, devices, fixture } = options;
  const guarded = await createGuardedContext(
    browser,
    devices,
    run.profile,
    artifactDir,
    `phase-two-${run.profile}-${run.role}`,
    { baseUrl },
  );
  await freezeBrowserClock(guarded.context, fixture.clock.attention_now_ms);
  await guarded.context.grantPermissions(["clipboard-write"], { origin: baseUrl });
  const page = await guarded.context.newPage();
  const recorder = createApiRecorder(page, {
    authType: "session",
    role: run.role,
    username: run.username,
  });
  const result = {
    assertions: { failed: [], passed: [], skipped: [] },
    browser_profile: guarded.profile,
    checks: {},
    failure: null,
    profile: run.profile,
    requests: [],
    role: run.role,
    trace: null,
  };
  let caughtError = null;
  let status = "failed";
  try {
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await authenticate(page, run.username, run.password);
    guarded.markAuthenticated();
    result.browser_profile.user_agent = await page.evaluate(() => navigator.userAgent);
    result.browser_profile.max_touch_points = await page.evaluate(() => navigator.maxTouchPoints);
    result.browser_profile.has_touch = result.browser_profile.max_touch_points > 0;
    result.browser_profile.viewport = page.viewportSize();
    result.browser_profile.user_agent_contract = assertBrowserProfileContract(
      run.profile,
      result.browser_profile,
    );
    await visible(page.locator("#map-grid"), "Phase 2 map-first surface");
    result.checks.map_first_geometry = await assertMapFirstGeometry(page, run.profile);
    result.checks.map_first_geometry_and_inert_surfaces = true;
    await selectGarden(page, run.profile, fixture.gardens.alpha.id);
    result.checks.last_completed_step = "profile-setup";

    if (run.role === "admin" && run.profile === "desktop") {
      await exerciseToday(page, run.profile, fixture);
      result.checks.last_completed_step = "today-attention";
      await exerciseTaskFormKeyboard(page, fixture);
      result.checks.last_completed_step = "task-form-keyboard";
      await exerciseGroupedCompletionRestrictions(page, fixture);
      result.checks.last_completed_step = "grouped-completion-restrictions";
      await openTasks(page, run.profile);
      const bloom = taskTitle(fixture, "bloom_desktop");
      await completeBloomTask(
        page,
        taskCard(page, bloom.title),
        fixture.phase_two.plant_names.bloom_desktop,
        false,
      );
      result.checks.last_completed_step = "bloom-completion";
      await snoozePruneWithManualDate(page, fixture);
      result.checks.last_completed_step = "manual-snooze";
      await completeBatch(page, fixture);
      result.checks.last_completed_step = "batch-completion";
      await completePlotDrawerTask(page, fixture);
      result.checks.last_completed_step = "plot-drawer-task";
      await exerciseTasksCalendarRace(page, guarded.context, fixture);
      result.checks.last_completed_step = "tasks-calendar-race";
      result.checks.calendar_lifecycle_mutations = await exerciseCalendarLifecycle(
        page,
        run.profile,
        fixture,
        guarded.diagnostics,
        {
        onSubscriptionCreated: async ({ label }) => {
          await exerciseCalendarSubscriptionRace(page, guarded.context, fixture, label);
        },
        },
      );
      result.checks.last_completed_step = "calendar-lifecycle";
      await exerciseNotificationSettingsRace(page, guarded.context, fixture);
      result.checks.last_completed_step = "notification-settings-race";
      result.checks.personal_notification_preference_persistence = await exerciseNotifications(
        page,
        fixture,
        options.oracle,
        options.onPreferencesSaved,
      );
      result.checks.last_completed_step = "notification-preferences";
      await exercisePostMutationReload(page, fixture);
      result.checks.last_completed_step = "post-mutation-reload";
      result.checks.admin_daily_attention_workflow = true;
      result.checks.grouped_completion_restrictions_across_surfaces = true;
      result.checks.task_surface_parity = true;
      result.checks.calendar_lifecycle_export_subscription = true;
      result.checks.calendar_export_selected_garden_scope = true;
      result.checks.calendar_feed_token_revocation = true;
      result.checks.ics_export_integrity_scope_redaction = true;
      result.checks.notification_preferences_and_accessibility = true;
      result.checks.notification_attention_projection_after_refresh = true;
      result.checks.notification_settings_aba_race = true;
      result.checks.tasks_calendar_subscriptions_aba_race = true;
      result.checks.stale_dom_assertions = true;
      result.checks.post_mutation_reload_surfaces = true;
      result.checks.post_mutation_reload_journal_records = true;
    } else if (run.role === "admin" && run.profile === "mobile") {
      await exerciseToday(page, run.profile, fixture);
      result.checks.last_completed_step = "mobile-today";
      await exerciseMobilePlotSheetKeyboard(page, fixture);
      result.checks.last_completed_step = "mobile-plot-sheet-keyboard";
      await completeMobileQuickActions(page, fixture);
      result.checks.last_completed_step = "mobile-quick-actions";
      await exerciseMobilePartialGroupedAndSnooze(page, fixture);
      result.checks.last_completed_step = "mobile-partial-grouped-snooze";
      result.checks.calendar_lifecycle_mutations = await exerciseCalendarLifecycle(
        page,
        run.profile,
        fixture,
        guarded.diagnostics,
        {
        includeExportAndSubscription: false,
        },
      );
      result.checks.last_completed_step = "mobile-calendar-lifecycle";
      result.checks.personal_notification_preference_persistence = (
        await exerciseMobileNotificationPreferenceMutation(page, fixture, options.oracle)
      );
      result.checks.last_completed_step = "mobile-notification-preferences";
      await exerciseMobileHistoryReload(page, fixture);
      result.checks.last_completed_step = "mobile-history-reload";
      await exerciseWeather(page, run.profile, fixture, recorder);
      result.checks.last_completed_step = "mobile-weather";
      await exerciseMobileCalendarAndNotifications(page, fixture);
      result.checks.last_completed_step = "mobile-calendar-notifications";
      result.checks.mobile_today_quick_actions_weather = true;
      result.checks.mobile_quick_actions_accessibility = true;
      result.checks.weather_idempotency_cross_surface_refresh = true;
      result.checks.weather_concurrent_identity_deduplication = true;
      result.checks.mobile_calendar_notification_focus_inert = true;
      result.checks.mobile_calendar_month_week_list_navigation = true;
      result.checks.mobile_partial_grouped_task_work = true;
      result.checks.mobile_snooze_manual_date = true;
      result.checks.immediate_snooze_correction_action = true;
      result.checks.mobile_calendar_lifecycle = true;
      result.checks.mobile_notification_preference_mutation = true;
      result.checks.mobile_history_reload = true;
    } else if (run.role === "editor" && run.profile === "desktop") {
      await exerciseEditorCalendar(page, fixture);
      result.checks.last_completed_step = "editor-calendar";
      await exerciseEditorWeatherDeduplication(page, fixture);
      result.checks.last_completed_step = "editor-weather-deduplication";
      result.checks.personal_notification_preference_persistence = (
        await exercisePersonalNotificationPreferencePersistence(
          page,
          run.profile,
          run.role,
          fixture,
          options.oracle,
        )
      );
      result.checks.last_completed_step = "editor-notification-preferences";
      result.checks.editor_calendar_action_and_weather_scope = true;
      result.checks.editor_weather_deduplicated_surfaces = true;
    } else if (run.role === "editor") {
      result.checks.offline_task_operation_ids = await exerciseOfflineTask(page, fixture);
      result.checks.last_completed_step = "editor-offline-replay";
      result.checks.personal_notification_preference_persistence = (
        await exercisePersonalNotificationPreferencePersistence(
          page,
          run.profile,
          run.role,
          fixture,
          options.oracle,
        )
      );
      result.checks.last_completed_step = "editor-notification-preferences";
      result.checks.editor_offline_task_replay = true;
      result.checks.editor_offline_task_actions_replay = true;
    } else {
      await exerciseViewer(page, guarded.diagnostics, run.profile, fixture);
      result.checks.last_completed_step = "viewer-read-only";
      result.checks.personal_notification_preference_persistence = (
        await exercisePersonalNotificationPreferencePersistence(
          page,
          run.profile,
          run.role,
          fixture,
          options.oracle,
        )
      );
      result.checks.last_completed_step = "viewer-notification-preferences";
      result.checks.viewer_read_only_and_denial = true;
      result.checks.viewer_direct_forbidden_task_write = true;
      result.checks.viewer_today_weather_affordances = true;
    }

    result.structure = await assertPageStructure(
      page,
      `${run.profile} ${run.role} Phase 2`,
      { enforceControlNames: true },
    );
    assertDiagnosticsClean(guarded.diagnostics, `${run.profile} ${run.role} Phase 2`);
    result.checks.browser_diagnostics = true;
    result.assertions.passed.push(`phase-two-${run.role}-${run.profile}`);
    result.requests = recorder.records;
    status = "passed";
  } catch (error) {
    caughtError = error;
    result.failure = "Phase 2 profile journey failed; see top-level sanitized failure";
    result.assertions.failed.push(result.failure);
  } finally {
    result.diagnostics = guarded.diagnostics;
    try {
      result.trace = await guarded.close(status);
    } catch (error) {
      if (!caughtError) caughtError = error;
    }
  }
  return { error: caughtError, result };
}

async function runDailyAttentionWork(options, profileRunner = runProfile) {
  const runs = [
    { profile: "desktop", role: "admin", username: options.username, password: options.password },
    { profile: "mobile", role: "admin", username: options.username, password: options.password },
    { profile: "desktop", role: "editor", username: options.fixture.roles.editor, password: EDITOR_PASSWORD },
    { profile: "mobile", role: "editor", username: options.fixture.roles.editor, password: EDITOR_PASSWORD },
    { profile: "desktop", role: "viewer", username: options.fixture.roles.viewer, password: VIEWER_PASSWORD },
    { profile: "mobile", role: "viewer", username: options.fixture.roles.viewer, password: VIEWER_PASSWORD },
  ];
  for (const run of runs) {
    const outcome = await profileRunner({ ...options, ...run });
    if (options.onProfile) options.onProfile(outcome.result);
    if (outcome.error) throw outcome.error;
  }
}

module.exports = { runDailyAttentionWork };
