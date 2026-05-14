import type { Plant, Plot } from "../core/models";
import { formatPlantCategoryLabel, getLocaleTag, t } from "../core/i18n";
import type { StatisticsActions } from "../services/api";
import { parseMonth } from "./dataTables";

const ZONE_COLORS: Record<string, string> = {
  B: "var(--zone-b)",
  V: "var(--zone-v)",
  T: "var(--zone-t)",
  R: "var(--zone-r)",
  S: "var(--zone-s)",
  P: "var(--zone-p)",
  D: "var(--zone-d)",
  H: "var(--zone-h)",
  K: "#2f7d57",
  KASSE: "#4e8f63",
};

const CATEGORIES = ["løk", "frø", "busker", "baerbusker", "trær"];

type CardTone = "brand" | "accent" | "ink" | "earth" | "sage" | "sky";

interface SectionOptions {
  title: string;
  kicker: string;
  note: string;
  extraClass?: string;
}

function fmtNumber(value: number): string {
  return new Intl.NumberFormat(getLocaleTag()).format(value);
}

function fmtCompact(value: number, maximumFractionDigits = 1): string {
  return new Intl.NumberFormat(getLocaleTag(), { maximumFractionDigits }).format(value);
}

function monthLabel(index: number, width: "short" | "long" = "short"): string {
  const date = new Date(Date.UTC(2026, index, 1));
  return new Intl.DateTimeFormat(getLocaleTag(), { month: width, timeZone: "UTC" }).format(date);
}

function percent(value: number, total: number): number {
  if (total <= 0) return 0;
  return (value / total) * 100;
}

function percentLabel(value: number, total: number): string {
  const raw = percent(value, total);
  if (raw > 0 && raw < 1) return "<1%";
  return `${Math.round(raw)}%`;
}

function topEntry(counts: Map<string, number>): [string, number] | null {
  let best: [string, number] | null = null;
  for (const entry of counts.entries()) {
    if (!best || entry[1] > best[1]) best = entry;
  }
  return best;
}

function buildZoneNames(plots: Plot[]): Map<string, string> {
  const zoneNames = new Map<string, string>();
  for (const plot of plots) {
    if (zoneNames.has(plot.zone_code)) continue;
    const zoneName = plot.zone_name.trim();
    zoneNames.set(plot.zone_code, zoneName || plot.zone_code);
  }
  return zoneNames;
}

function zoneName(code: string, zoneNames: Map<string, string>): string {
  return zoneNames.get(code) ?? code;
}

function zoneLabel(code: string, zoneNames: Map<string, string>): string {
  const name = zoneName(code, zoneNames);
  return name === code ? code : `${code} — ${name}`;
}

function createElement<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function createChip(text: string): HTMLElement {
  return createElement("span", "stats-chip", text);
}

function createRingCard(
  title: string,
  ratio: number,
  detail: string,
  accent: string,
): HTMLElement {
  const card = createElement("div", "stat-ring-card");
  card.appendChild(createElement("span", "stat-ring-label", title));

  const ring = createElement("div", "stat-ring");
  ring.style.setProperty("--stat-ring-angle", `${ratio.toFixed(1)}%`);
  ring.style.setProperty("--stat-ring-accent", accent);

  const core = createElement("div", "stat-ring-core");
  const value = createElement("strong", "stat-ring-value", `${Math.round(ratio)}%`);
  core.appendChild(value);
  ring.appendChild(core);

  card.append(ring, createElement("p", "stat-ring-note", detail));
  return card;
}

function createCard(
  title: string,
  value: string | number,
  subtitle: string,
  tone: CardTone,
): HTMLElement {
  const card = createElement("div", `stat-card stat-card--${tone}`);
  card.append(
    createElement("span", "stat-card-value", String(value)),
    createElement("span", "stat-card-title", title),
    createElement("span", "stat-card-sub", subtitle),
  );
  return card;
}

function createStoryCard(
  kicker: string,
  value: string | number,
  note: string,
): HTMLElement {
  const card = createElement("div", "stat-story-card");
  card.append(
    createElement("span", "stat-story-kicker", kicker),
    createElement("strong", "stat-story-value", String(value)),
    createElement("span", "stat-story-note", note),
  );
  return card;
}

function createBar(
  label: string,
  value: number,
  maxValue: number,
  color: string,
  detail: string,
): HTMLElement {
  const pct = maxValue > 0 ? (value / maxValue) * 100 : 0;
  const row = createElement("div", "stat-bar-row");
  const copy = createElement("div", "stat-bar-copy");
  copy.append(
    createElement("span", "stat-bar-label", label),
    createElement("span", "stat-bar-detail", detail),
  );

  const track = createElement("div", "stat-bar-track");
  const fill = createElement("div", "stat-bar-fill");
  fill.style.width = `${pct.toFixed(1)}%`;
  fill.style.background = color;
  track.appendChild(fill);

  row.append(copy, track, createElement("span", "stat-bar-value", fmtNumber(value)));
  return row;
}

function createSection(options: SectionOptions, ...body: Node[]): HTMLElement {
  const section = createElement(
    "section",
    `stat-section${options.extraClass ? ` ${options.extraClass}` : ""}`,
  );

  const head = createElement("div", "stat-section-head");
  head.append(
    createElement("p", "stat-section-kicker", options.kicker),
    createElement("h3", "stat-section-title", options.title),
    createElement("p", "stat-section-note", options.note),
  );

  section.append(head, ...body);
  return section;
}

function createMiniSection(title: string, body: Node): HTMLElement {
  const section = createElement("div", "stat-mini-section");
  section.append(createElement("h4", "stat-mini-title", title), body);
  return section;
}

function createEmptyState(text: string): HTMLElement {
  return createElement("p", "stat-empty", text);
}

function createBarsContainer(items: HTMLElement[]): HTMLElement {
  const container = createElement("div", "stat-bars");
  container.append(...items);
  return container;
}

function createStoryGrid(cards: HTMLElement[]): HTMLElement {
  const grid = createElement("div", "stat-story-grid");
  grid.append(...cards);
  return grid;
}

function computeBloomMonths(plants: Plant[]): number[] {
  const counts = new Array<number>(12).fill(0);
  for (const plant of plants) {
    if (!plant.bloom_month) continue;
    const parts = plant.bloom_month
      .split(/[-–,]/)
      .map(parseMonth)
      .filter(Boolean);
    if (parts.length === 2 && parts[0]! <= parts[1]!) {
      for (let month = parts[0]!; month <= parts[1]!; month++) {
        counts[month - 1]!++;
      }
      continue;
    }
    for (const month of parts) {
      if (month >= 1 && month <= 12) counts[month - 1]!++;
    }
  }
  return counts;
}

export interface StatisticsCallbacks {
  onFilterPlants: (pltIds: string[], label: string) => void;
  onNavigateMap: (plotIds: string[]) => void;
  onNavigateCare: (pltIds: string[]) => void;
  onOpenBatchJournal: (pltIds: string[]) => void;
  onReviewBloomGap: (months: number[]) => void;
}

function createActionCard(
  icon: string,
  title: string,
  count: number,
  description: string,
  onClick: () => void,
): HTMLElement {
  const card = createElement("button", "action-card");
  card.type = "button";
  card.addEventListener("click", onClick);

  const iconEl = createElement("span", "action-card-icon", icon);
  const titleEl = createElement("strong", "action-card-title", title);
  const badge = createElement("span", "action-card-badge", String(count));
  const desc = createElement("p", "action-card-desc", description);

  const head = createElement("div", "action-card-head");
  head.append(iconEl, titleEl, badge);
  card.append(head, desc);
  return card;
}

function renderNeedsAttention(
  actions: StatisticsActions,
  cbs: StatisticsCallbacks,
): HTMLElement | null {
  const cards: HTMLElement[] = [];

  if (actions.unassigned_plants.length > 0) {
    const ids = actions.unassigned_plants.map((p) => p.plt_id);
    cards.push(createActionCard(
      "\uD83D\uDCCD",
      t("stats.action_unassigned_title"),
      ids.length,
      t("stats.action_unassigned_desc"),
      () => cbs.onFilterPlants(ids, "unassigned"),
    ));
  }

  if (actions.empty_plots_by_zone.length > 0) {
    const total = actions.empty_plots_by_zone.reduce(
      (s, z) => s + z.count, 0,
    );
    const plotIds = actions.empty_plots_by_zone.flatMap((z) => z.plot_ids);
    cards.push(createActionCard(
      "\uD83D\uDFE9",
      t("stats.action_empty_plots_title"),
      total,
      t("stats.action_empty_plots_desc", { count: actions.empty_plots_by_zone.length }),
      () => cbs.onNavigateMap(plotIds),
    ));
  }

  if (actions.bloom_gap_months.length > 0) {
    const labels = actions.bloom_gap_months
      .map((m) => monthLabel(m - 1))
      .join(", ");
    cards.push(createActionCard(
      "\uD83C\uDF38",
      t("stats.action_bloom_gaps_title"),
      actions.bloom_gap_months.length,
      t("stats.action_bloom_gaps_desc", { months: labels }),
      () => cbs.onReviewBloomGap(actions.bloom_gap_months),
    ));
  }

  if (actions.no_year_plants.length > 0) {
    const ids = actions.no_year_plants.map((p) => p.plt_id);
    cards.push(createActionCard(
      "\uD83D\uDCC5",
      t("stats.action_missing_year_title"),
      ids.length,
      t("stats.action_missing_year_desc"),
      () => cbs.onFilterPlants(ids, "no-year"),
    ));
  }

  if (actions.stale_plants.length > 0) {
    const ids = actions.stale_plants.map((p) => p.plt_id);
    cards.push(createActionCard(
      "\u23F3",
      t("stats.action_stale_title"),
      ids.length,
      t("stats.action_stale_desc"),
      () => cbs.onOpenBatchJournal(ids),
    ));
  }

  if (actions.missing_care_plants.length > 0) {
    const ids = actions.missing_care_plants.map((p) => p.plt_id);
    cards.push(createActionCard(
      "\u2753",
      t("stats.action_missing_care_title"),
      ids.length,
      t("stats.action_missing_care_desc"),
      () => cbs.onNavigateCare(ids),
    ));
  }

  if (cards.length === 0) {
    const allGood = createElement("div", "action-card action-card--good");
    allGood.append(
      createElement("span", "action-card-icon", "\u2705"),
      createElement("strong", "action-card-title", t("stats.action_all_good_title")),
      createElement(
        "p",
        "action-card-desc",
        t("stats.action_all_good_desc"),
      ),
    );
    const section = createSection(
      {
        kicker: t("stats.action_section_kicker"),
        title: t("stats.action_section_title"),
        note: t("stats.action_section_all_clear"),
      },
      allGood,
    );
    return section;
  }

  const grid = createElement("div", "action-card-grid");
  grid.append(...cards);
  return createSection(
    {
      kicker: t("stats.action_section_kicker"),
      title: t("stats.action_section_title"),
      note: t("stats.action_section_found", { count: cards.length }),
      extraClass: "stat-section--wide",
    },
    grid,
  );
}

export function renderStatistics(
  container: HTMLElement,
  plots: Plot[],
  plants: Plant[],
  actions?: StatisticsActions | null,
  cbs?: StatisticsCallbacks,
): void {
  const plantedPlots = plots.filter((plot) => plot.plant_count > 0);
  const emptyPlots = plots.length - plantedPlots.length;
  const uniqueSpecies = plants.length;
  const totalQuantity = plants.reduce((sum, plant) => sum + (plant.quantity ?? 0), 0);
  const treePlots = plots.filter((plot) => plot.has_tree).length;
  const bushPlots = plots.filter((plot) => plot.has_bush).length;
  const woodyPlots = plots.filter((plot) => plot.has_tree || plot.has_bush).length;
  const plantedPct = percent(plantedPlots.length, plots.length);
  const avgPerPlantedPlot = plantedPlots.length > 0 ? totalQuantity / plantedPlots.length : 0;

  const zoneCounts = new Map<string, number>();
  for (const plot of plots) {
    zoneCounts.set(plot.zone_code, (zoneCounts.get(plot.zone_code) ?? 0) + 1);
  }
  const zoneNames = buildZoneNames(plots);
  const zoneEntries = [...zoneCounts.entries()].sort((a, b) => b[1] - a[1]);
  const maxZone = Math.max(...zoneEntries.map(([, count]) => count), 1);
  const dominantZone = zoneEntries[0] ?? null;
  const dominantZoneName = dominantZone
    ? zoneName(dominantZone[0], zoneNames)
    : t("common.na");
  const zoneBars = zoneEntries.map(([code, count]) =>
    createBar(
      zoneLabel(code, zoneNames),
      count,
      maxZone,
      ZONE_COLORS[code] ?? "var(--brand)",
      t("stats.percent_of_plots", { percent: percentLabel(count, plots.length) }),
    ),
  );

  const categoryCounts = new Map<string, number>();
  for (const plant of plants) {
    if (!plant.category) continue;
    categoryCounts.set(plant.category, (categoryCounts.get(plant.category) ?? 0) + 1);
  }
  const categoryEntries = CATEGORIES.map((category) => [category, categoryCounts.get(category) ?? 0] as const);
  const maxCategory = Math.max(...categoryEntries.map(([, count]) => count), 1);
  const dominantCategory = topEntry(categoryCounts);
  const dominantCategoryShort = dominantCategory
    ? formatPlantCategoryLabel(dominantCategory[0])
    : t("common.na");
  const categoryBars = categoryEntries.map(([category, count]) =>
    createBar(
      formatPlantCategoryLabel(category),
      count,
      maxCategory,
      "var(--brand)",
      t("stats.percent_of_species", { percent: percentLabel(count, uniqueSpecies) }),
    ),
  );

  const bloomCounts = computeBloomMonths(plants);
  const peakBloomCount = Math.max(...bloomCounts, 0);
  const peakBloomIndex = peakBloomCount > 0 ? bloomCounts.indexOf(peakBloomCount) : -1;
  const peakBloomLabel = peakBloomIndex >= 0 ? monthLabel(peakBloomIndex) : t("common.na");
  const currentMonth = new Date().getMonth();
  const bloomCalendar = createElement("div", "bloom-calendar");
  bloomCounts.forEach((count, index) => {
    const height = peakBloomCount > 0 ? (count / peakBloomCount) * 100 : 0;
    const isCurrent = index === currentMonth;
    const isPeak = count === peakBloomCount && peakBloomCount > 0;
    const cell = createElement(
      "div",
      `bloom-cell${isCurrent ? " bloom-current" : ""}${isPeak ? " bloom-peak" : ""}`,
    );
    const label = createElement("span", "bloom-label", monthLabel(index));
    const wrap = createElement("div", "bloom-bar-wrap");
    const bar = createElement("div", "bloom-bar");
    bar.style.height = `${height.toFixed(1)}%`;
    wrap.appendChild(bar);
    const value = createElement("span", "bloom-count", fmtNumber(count));
    cell.append(label, wrap, value);
    bloomCalendar.appendChild(cell);
  });

  const lightCounts = new Map<string, number>();
  for (const plant of plants) {
    if (!plant.light) continue;
    lightCounts.set(plant.light, (lightCounts.get(plant.light) ?? 0) + 1);
  }
  const lightEntries = [...lightCounts.entries()].sort((a, b) => b[1] - a[1]);
  const maxLight = Math.max(...lightEntries.map(([, count]) => count), 1);
  const dominantLight = lightEntries[0] ?? null;
  const lightBars = lightEntries.map(([light, count]) =>
    createBar(
      light,
      count,
      maxLight,
      "var(--accent)",
      t("stats.percent_of_species", { percent: percentLabel(count, uniqueSpecies) }),
    ),
  );

  const hardinessCounts = new Map<string, number>();
  for (const plant of plants) {
    if (!plant.hardiness) continue;
    hardinessCounts.set(plant.hardiness, (hardinessCounts.get(plant.hardiness) ?? 0) + 1);
  }
  const hardinessEntries = [...hardinessCounts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  const maxHardiness = Math.max(...hardinessEntries.map(([, count]) => count), 1);
  const busiestHardiness = topEntry(hardinessCounts);
  const hardinessBars = hardinessEntries.map(([hardiness, count]) =>
    createBar(
      hardiness,
      count,
      maxHardiness,
      "var(--brand)",
      t("stats.percent_of_species", { percent: percentLabel(count, uniqueSpecies) }),
    ),
  );

  const mostPlanted = [...plants]
    .filter((plant) => (plant.quantity ?? 0) > 0)
    .sort((a, b) => (b.quantity ?? 0) - (a.quantity ?? 0))
    .slice(0, 10);
  const maxQuantity = mostPlanted.length > 0 ? (mostPlanted[0]!.quantity ?? 0) : 1;
  const topPlant = mostPlanted[0] ?? null;
  const topPlantBars = mostPlanted.map((plant) =>
    createBar(
      plant.name,
      plant.quantity ?? 0,
      maxQuantity,
      "var(--brand)",
      `${fmtNumber(plant.plot_ids?.length ?? 0)} ${t("plants.field_plots").toLowerCase()}`,
    ),
  );

  const deerCount = plants.filter((plant) => plant.deer_resistant).length;
  const deerPct = percent(deerCount, uniqueSpecies);

  const yearCounts = new Map<string, number>();
  for (const plant of plants) {
    if (!plant.year_planted) continue;
    yearCounts.set(plant.year_planted, (yearCounts.get(plant.year_planted) ?? 0) + 1);
  }
  const yearEntries = [...yearCounts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  const maxYear = Math.max(...yearEntries.map(([, count]) => count), 1);
  const busiestYear = topEntry(yearCounts);
  const yearBars = yearEntries.map(([year, count]) =>
    createBar(
      year,
      count,
      maxYear,
      "var(--accent)",
      t("stats.percent_of_species", { percent: percentLabel(count, uniqueSpecies) }),
    ),
  );

  const dashboard = createElement("div", "stats-dashboard");

  const hero = createElement("section", "stats-hero");
  const heroCopy = createElement("div", "stats-hero-copy");
  heroCopy.append(
    createElement("p", "stats-kicker", t("stats.hero_kicker")),
    createElement("h2", undefined, t("stats.hero_title")),
    createElement(
      "p",
      "stats-lede",
      t("stats.hero_lede", { plots: fmtNumber(plots.length), species: fmtNumber(uniqueSpecies) }),
    ),
  );
  const chipRow = createElement("div", "stats-chip-row");
  [
    t("stats.chip_planted_plots", { count: fmtNumber(plantedPlots.length) }),
    t("stats.chip_dominant_zone", { zone: dominantZoneName }),
    t("stats.chip_peak_bloom", { label: peakBloomLabel }),
    t("stats.chip_top_category", { category: dominantCategoryShort }),
    t("stats.chip_main_light", { light: dominantLight?.[0] ?? t("common.na") }),
  ].forEach((text) => chipRow.appendChild(createChip(text)));
  heroCopy.appendChild(chipRow);

  const heroRings = createElement("div", "stats-hero-rings");
  heroRings.append(
    createRingCard(
      t("stats.ring_plots_planted"),
      plantedPct,
      t("stats.ring_plots_planted_note", {
        planted: fmtNumber(plantedPlots.length),
        total: fmtNumber(plots.length),
      }),
      "var(--brand)",
    ),
    createRingCard(
      t("stats.ring_deer_resistant"),
      deerPct,
      t("stats.ring_deer_resistant_note", {
        deer: fmtNumber(deerCount),
        species: fmtNumber(uniqueSpecies),
      }),
      "var(--accent)",
    ),
  );
  hero.append(heroCopy, heroRings);

  const overviewGrid = createElement("div", "stat-overview-grid");
  [
    createCard(t("stats.card_total_plots"), fmtNumber(plots.length), t("stats.card_total_plots_note", { count: fmtNumber(emptyPlots) }), "ink"),
    createCard(t("stats.card_plantings"), fmtNumber(totalQuantity), t("stats.card_plantings_note", { avg: fmtCompact(avgPerPlantedPlot) }), "brand"),
    createCard(t("stats.card_species"), fmtNumber(uniqueSpecies), t("stats.card_species_note", { category: dominantCategoryShort }), "sky"),
    createCard(t("stats.card_woody_plots"), fmtNumber(woodyPlots), t("stats.card_woody_plots_note", { trees: fmtNumber(treePlots), bushes: fmtNumber(bushPlots) }), "sage"),
    createCard(t("stats.card_peak_bloom"), peakBloomLabel, t("stats.card_peak_bloom_note", { count: fmtNumber(peakBloomCount) }), "accent"),
    createCard(
      t("stats.card_sunlight_lead"),
      dominantLight ? fmtNumber(dominantLight[1]) : "0",
      dominantLight ? t("stats.card_sunlight_lead_note", { light: dominantLight[0] }) : t("stats.card_sunlight_lead_empty"),
      "earth",
    ),
  ].forEach((card) => overviewGrid.appendChild(card));

  const bloomBody = createElement("div", "stat-seasonality");
  bloomBody.appendChild(
    createStoryGrid([
      createStoryCard(t("stats.story_peak_window"), peakBloomLabel, t("stats.story_peak_window_note", { count: fmtNumber(peakBloomCount) })),
      createStoryCard(t("stats.story_current_month"), monthLabel(currentMonth), t("stats.story_current_month_note", { count: fmtNumber(bloomCounts[currentMonth] ?? 0) })),
    ]),
  );
  bloomBody.appendChild(bloomCalendar);

  const growingConditionsBody = createElement("div", "stat-split");
  growingConditionsBody.append(
    createMiniSection(
      t("stats.mini_sunlight"),
      lightBars.length > 0 ? createBarsContainer(lightBars) : createEmptyState(t("stats.empty_light")),
    ),
    createMiniSection(
      t("stats.mini_hardiness"),
      hardinessBars.length > 0 ? createBarsContainer(hardinessBars) : createEmptyState(t("stats.empty_hardiness")),
    ),
  );

  const profileBody = createStoryGrid([
    createStoryCard(t("stats.story_open_plots"), fmtNumber(emptyPlots), t("stats.story_open_plots_note", { percent: percentLabel(emptyPlots, plots.length) })),
    createStoryCard(t("stats.story_tree_presence"), fmtNumber(treePlots), t("stats.story_tree_presence_note", { percent: percentLabel(treePlots, plots.length) })),
    createStoryCard(t("stats.story_bush_presence"), fmtNumber(bushPlots), t("stats.story_bush_presence_note", { percent: percentLabel(bushPlots, plots.length) })),
  ]);

  const statLayout = createElement("div", "stat-layout");
  statLayout.append(
    createSection(
      {
        kicker: t("stats.section_spatial_kicker"),
        title: t("stats.section_spatial_title"),
        note: dominantZone
          ? t("stats.section_spatial_note", {
            zone: dominantZoneName,
            percent: percentLabel(dominantZone[1], plots.length),
          })
          : t("stats.section_spatial_empty"),
        extraClass: "stat-section--wide",
      },
      createBarsContainer(zoneBars),
    ),
    createSection(
      {
        kicker: t("stats.section_palette_kicker"),
        title: t("stats.section_palette_title"),
        note: dominantCategory
          ? t("stats.section_palette_note", {
            category: formatPlantCategoryLabel(dominantCategory[0]),
            count: fmtNumber(dominantCategory[1]),
          })
          : t("stats.section_palette_empty"),
      },
      createBarsContainer(categoryBars),
    ),
    createSection(
      {
        kicker: t("stats.section_seasonality_kicker"),
        title: t("stats.section_seasonality_title"),
        note: peakBloomCount > 0
          ? t("stats.section_seasonality_note", {
            label: peakBloomLabel,
            count: fmtNumber(peakBloomCount),
          })
          : t("stats.section_seasonality_empty"),
        extraClass: "stat-section--wide",
      },
      bloomBody,
    ),
    createSection(
      {
        kicker: t("stats.section_environment_kicker"),
        title: t("stats.section_environment_title"),
        note: dominantLight && busiestHardiness
          ? t("stats.section_environment_note", {
            light: dominantLight[0],
            hardiness: busiestHardiness[0],
          })
          : t("stats.section_environment_empty"),
        extraClass: "stat-section--wide",
      },
      growingConditionsBody,
    ),
    createSection(
      {
        kicker: t("stats.section_concentration_kicker"),
        title: t("stats.section_concentration_title"),
        note: topPlant
          ? t("stats.section_concentration_note", {
            name: topPlant.name,
            count: fmtNumber(topPlant.quantity ?? 0),
          })
          : t("stats.section_concentration_empty"),
        extraClass: "stat-section--wide",
      },
      topPlantBars.length > 0 ? createBarsContainer(topPlantBars) : createEmptyState(t("stats.section_concentration_empty")),
    ),
    createSection(
      {
        kicker: t("stats.section_profile_kicker"),
        title: t("stats.section_profile_title"),
        note: t("stats.section_profile_note", {
          deer: fmtNumber(deerCount),
          species: fmtNumber(uniqueSpecies),
          woody: fmtNumber(woodyPlots),
        }),
      },
      profileBody,
    ),
    createSection(
      {
        kicker: t("stats.section_timeline_kicker"),
        title: t("stats.section_timeline_title"),
        note: busiestYear
          ? t("stats.section_timeline_note", {
            year: busiestYear[0],
            count: fmtNumber(busiestYear[1]),
          })
          : t("stats.section_timeline_empty"),
      },
      yearBars.length > 0 ? createBarsContainer(yearBars) : createEmptyState(t("stats.section_timeline_empty")),
    ),
  );

  dashboard.append(hero, overviewGrid);

  if (actions && cbs) {
    const attentionSection = renderNeedsAttention(actions, cbs);
    if (attentionSection) {
      dashboard.appendChild(attentionSection);
    }
  }

  dashboard.appendChild(statLayout);
  container.replaceChildren(dashboard);
}
