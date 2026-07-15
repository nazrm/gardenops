import type { GardenProfile, PlannerResult, PlantingSuggestion, PlotSuggestions } from "../core/models";
import type { AvailableWorkflow } from "../services/api";
import { t } from "../core/i18n";
import { formatBloomMonth } from "./dataTables";

export interface WorkflowCallbacks {
  onStart: (workflowId: string, selectedSteps: string[]) => void | Promise<void>;
}

export interface PlannerCallbacks {
  onPlantClick: (pltId: string) => void;
  onPlotClick: (plotId: string) => void;
  onRefresh: (goal: string) => void;
  onPreviewCandidate: (plotId: string, suggestion: PlantingSuggestion) => void;
  onInspectCandidate: (plotId: string, suggestion: PlantingSuggestion) => void;
}

const MONTH_LABELS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

const GOALS: Array<{ key: string; value: string }> = [
  { key: "planner.goal_all", value: "" },
  { key: "planner.goal_shade", value: "shade" },
  { key: "planner.goal_color", value: "color" },
  { key: "planner.goal_edible", value: "edible" },
  { key: "planner.goal_deer", value: "deer" },
  { key: "planner.goal_low_maintenance", value: "low_maintenance" },
];

function createBloomStrip(coverage: number[], gaps: number[]): HTMLElement {
  const strip = document.createElement("div");
  strip.className = "planner-bloom-strip";
  for (let m = 1; m <= 12; m++) {
    const cell = document.createElement("div");
    cell.className = "planner-bloom-cell";
    if (coverage.includes(m)) cell.classList.add("covered");
    if (gaps.includes(m)) cell.classList.add("gap");
    cell.textContent = MONTH_LABELS[m - 1] ?? "";
    cell.title = `${t("planner.bloom_coverage")}: ${m}`;
    strip.appendChild(cell);
  }
  return strip;
}

function createSuggestionCard(
  plotId: string,
  suggestion: PlantingSuggestion,
  cbs: PlannerCallbacks,
): HTMLElement {
  const card = document.createElement("div");
  card.className = "planner-suggestion-card";

  // Header: name + score
  const header = document.createElement("div");
  header.className = "planner-suggestion-header";

  const nameBtn = document.createElement("button");
  nameBtn.type = "button";
  nameBtn.className = "planner-suggestion-name";
  nameBtn.textContent = suggestion.name;
  nameBtn.addEventListener("click", () => cbs.onPlantClick(suggestion.plt_id));

  const score = document.createElement("span");
  score.className = "planner-score-badge";
  score.textContent = `+${suggestion.score}`;

  header.append(nameBtn, score);
  card.appendChild(header);

  // Latin name
  if (suggestion.latin) {
    const latin = document.createElement("div");
    latin.className = "planner-suggestion-latin";
    latin.textContent = suggestion.latin;
    card.appendChild(latin);
  }

  // Reasons
  if (suggestion.reasons.length > 0) {
    const reasons = document.createElement("div");
    reasons.className = "planner-reason-tags";
    for (const reason of suggestion.reasons) {
      const tag = document.createElement("span");
      tag.className = "planner-reason-tag";
      tag.textContent = reason;
      reasons.appendChild(tag);
    }
    card.appendChild(reasons);
  }

  // Meta: category, bloom, hardiness
  const meta = document.createElement("div");
  meta.className = "planner-suggestion-meta";
  if (suggestion.category) {
    const cat = document.createElement("span");
    cat.textContent = suggestion.category;
    meta.appendChild(cat);
  }
  if (suggestion.bloom_month) {
    const bloom = document.createElement("span");
    bloom.textContent = `${t("planner.bloom_coverage")}: ${formatBloomMonth(suggestion.bloom_month)}`;
    meta.appendChild(bloom);
  }
  if (suggestion.hardiness) {
    const h = document.createElement("span");
    h.textContent = suggestion.hardiness;
    meta.appendChild(h);
  }
  if (suggestion.light) {
    const light = document.createElement("span");
    light.textContent = `${t("plants.field_light")}: ${suggestion.light}`;
    meta.appendChild(light);
  }
  card.appendChild(meta);

  const actions = document.createElement("div");
  actions.className = "planner-reason-tags";

  const previewBtn = document.createElement("button");
  previewBtn.type = "button";
  previewBtn.className = "planner-goal-btn";
  previewBtn.textContent = t("planner.preview_on_map");
  previewBtn.addEventListener("click", () => cbs.onPreviewCandidate(plotId, suggestion));

  const inspectBtn = document.createElement("button");
  inspectBtn.type = "button";
  inspectBtn.className = "planner-goal-btn";
  inspectBtn.textContent = t("planner.check_fit");
  inspectBtn.addEventListener("click", () => cbs.onInspectCandidate(plotId, suggestion));

  actions.append(previewBtn, inspectBtn);
  card.appendChild(actions);

  return card;
}

function createPlotGroup(
  plotSugg: PlotSuggestions,
  cbs: PlannerCallbacks,
): HTMLElement {
  const group = document.createElement("div");
  group.className = "planner-plot-group";

  const header = document.createElement("div");
  header.className = "planner-plot-header";

  const plotBtn = document.createElement("button");
  plotBtn.type = "button";
  plotBtn.className = "planner-suggestion-name";
  plotBtn.textContent = plotSugg.plot_id;
  plotBtn.addEventListener("click", () => cbs.onPlotClick(plotSugg.plot_id));

  const zoneBadge = document.createElement("span");
  zoneBadge.className = "planner-plot-zone";
  zoneBadge.textContent = plotSugg.zone_code;

  header.append(plotBtn, zoneBadge);
  group.appendChild(header);

  if (plotSugg.suggestions.length === 0) {
    const empty = document.createElement("div");
    empty.className = "planner-no-suggestions";
    empty.textContent = t("planner.no_suggestions");
    group.appendChild(empty);
  } else {
    for (const s of plotSugg.suggestions) {
      group.appendChild(createSuggestionCard(plotSugg.plot_id, s, cbs));
    }
  }

  return group;
}

function renderWorkflowSection(
  workflows: AvailableWorkflow[],
  wfCbs: WorkflowCallbacks,
  canStart: boolean,
): HTMLElement {
  const section = document.createElement("section");
  section.className = "planner-section workflow-section";

  const kicker = document.createElement("p");
  kicker.className = "reports-kicker";
  kicker.textContent = t("workflow.kicker");

  const title = document.createElement("h3");
  title.className = "reports-section-title";
  title.textContent = t("workflow.title");

  const sub = document.createElement("p");
  sub.className = "reports-section-subtitle";
  sub.textContent = t("workflow.subtitle");

  section.append(kicker, title, sub);

  for (const wf of workflows) {
    const card = document.createElement("div");
    card.className = "workflow-card";

    const cardTitle = document.createElement("strong");
    cardTitle.className = "workflow-card-title";
    cardTitle.textContent = wf.name;

    const meta = document.createElement("span");
    meta.className = "workflow-card-meta";
    meta.textContent = t("workflow.step_count", {
      count: wf.step_count,
    });

    card.append(cardTitle, meta);

    const checklist = document.createElement("div");
    checklist.className = "workflow-checklist";
    const selected = new Set(wf.steps.map((s) => s.id));

    for (const step of wf.steps) {
      const row = document.createElement("label");
      row.className = "workflow-step";
      const span = document.createElement("span");
      span.textContent = step.title;
      if (canStart) {
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = true;
        cb.addEventListener("change", () => {
          if (cb.checked) selected.add(step.id);
          else selected.delete(step.id);
        });
        row.append(cb, span);
      } else {
        row.appendChild(span);
      }
      checklist.appendChild(row);
    }
    card.appendChild(checklist);

    if (canStart) {
      const startBtn = document.createElement("button");
      startBtn.type = "button";
      startBtn.className = "btn btn-primary";
      startBtn.textContent = t("workflow.start");
      startBtn.addEventListener("click", () => {
        if (startBtn.disabled || selected.size === 0) return;
        startBtn.disabled = true;
        card.setAttribute("aria-busy", "true");
        checklist.querySelectorAll<HTMLInputElement>("input").forEach((input) => {
          input.disabled = true;
        });
        void Promise.resolve(wfCbs.onStart(wf.id, [...selected])).finally(() => {
          if (!card.isConnected) return;
          startBtn.disabled = false;
          card.removeAttribute("aria-busy");
          checklist.querySelectorAll<HTMLInputElement>("input").forEach((input) => {
            input.disabled = false;
          });
        });
      });
      card.appendChild(startBtn);
    }

    section.appendChild(card);
  }

  return section;
}

export function renderPlannerDashboard(
  container: HTMLElement,
  result: PlannerResult,
  profile: GardenProfile,
  cbs: PlannerCallbacks,
  activeGoal = "",
  workflows?: AvailableWorkflow[],
  workflowCbs?: WorkflowCallbacks,
  canStartWorkflows = false,
): void {
  container.replaceChildren();

  // Section title
  const titleDiv = document.createElement("div");
  titleDiv.className = "planner-section planner-header-section";
  const title = document.createElement("div");
  title.className = "planner-section-title";
  title.textContent = t("planner.title");
  titleDiv.appendChild(title);
  const subtitle = document.createElement("div");
  subtitle.className = "planner-suggestion-latin";
  subtitle.textContent = t("planner.subtitle");
  titleDiv.appendChild(subtitle);
  container.appendChild(titleDiv);

  // Garden profile summary
  const summarySection = document.createElement("div");
  summarySection.className = "planner-section";
  const summaryTitle = document.createElement("div");
  summaryTitle.className = "planner-section-title";
  summaryTitle.textContent = t("planner.garden_summary");
  summarySection.appendChild(summaryTitle);

  // Stats grid
  const statsGrid = document.createElement("div");
  statsGrid.className = "planner-stats-grid";

  const statPairs: Array<[string, number]> = [
    [t("planner.total_plots"), profile.total_plots],
    [t("planner.planted_plots"), profile.planted_plots],
    [t("planner.empty_plots"), profile.empty_plots],
  ];
  for (const [label, value] of statPairs) {
    const card = document.createElement("div");
    card.className = "planner-stat-card";
    const val = document.createElement("div");
    val.className = "planner-stat-value";
    val.textContent = String(value);
    const lbl = document.createElement("div");
    lbl.className = "planner-stat-label";
    lbl.textContent = label;
    card.append(val, lbl);
    statsGrid.appendChild(card);
  }
  summarySection.appendChild(statsGrid);

  // Bloom coverage strip
  const bloomTitle = document.createElement("div");
  bloomTitle.className = "planner-section-title";
  bloomTitle.textContent = t("planner.bloom_coverage");
  summarySection.appendChild(bloomTitle);
  summarySection.appendChild(
    createBloomStrip(profile.bloom_coverage, profile.bloom_gaps),
  );

  // Category distribution
  if (Object.keys(profile.categories).length > 0) {
    const catTitle = document.createElement("div");
    catTitle.className = "planner-section-title";
    catTitle.textContent = t("planner.categories");
    summarySection.appendChild(catTitle);

    const catList = document.createElement("div");
    catList.className = "planner-category-list";
    for (const [cat, count] of Object.entries(profile.categories)) {
      const chip = document.createElement("span");
      chip.className = "planner-category-chip";
      const strong = document.createElement("strong");
      strong.textContent = String(count);
      chip.append(cat, " ", strong);
      catList.appendChild(chip);
    }
    summarySection.appendChild(catList);
  }

  // Deer resistance stats
  if (profile.deer_resistant_count > 0 || profile.deer_vulnerable_count > 0) {
    const deerDiv = document.createElement("div");
    deerDiv.className = "planner-category-list";
    const resChip = document.createElement("span");
    resChip.className = "planner-category-chip";
    resChip.textContent = `${t("planner.deer_resistant")}: ${profile.deer_resistant_count}`;
    const vulnChip = document.createElement("span");
    vulnChip.className = "planner-category-chip";
    vulnChip.textContent = `${t("planner.deer_vulnerable")}: ${profile.deer_vulnerable_count}`;
    deerDiv.append(resChip, vulnChip);
    summarySection.appendChild(deerDiv);
  }

  container.appendChild(summarySection);

  // Goal selector
  const goalSection = document.createElement("div");
  goalSection.className = "planner-section";
  const goalsDiv = document.createElement("div");
  goalsDiv.className = "planner-goals";
  for (const g of GOALS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "planner-goal-btn";
    if (g.value === activeGoal) btn.classList.add("active");
    btn.textContent = t(g.key);
    btn.addEventListener("click", () => cbs.onRefresh(g.value));
    goalsDiv.appendChild(btn);
  }
  goalSection.appendChild(goalsDiv);
  container.appendChild(goalSection);

  // Suggestions by plot
  const suggestionsSection = document.createElement("div");
  suggestionsSection.className = "planner-section";

  if (result.plots.length === 0) {
    const noPlots = document.createElement("div");
    noPlots.className = "planner-no-suggestions";
    noPlots.textContent = t("planner.no_empty_plots");
    suggestionsSection.appendChild(noPlots);
  } else {
    for (const plotSugg of result.plots) {
      suggestionsSection.appendChild(createPlotGroup(plotSugg, cbs));
    }
  }

  container.appendChild(suggestionsSection);

  // Seasonal workflows
  if (workflows && workflows.length > 0 && workflowCbs) {
    container.appendChild(
      renderWorkflowSection(workflows, workflowCbs, canStartWorkflows),
    );
  }
}
