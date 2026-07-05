export interface Plot {
  plot_id: string;
  zone_code: string;
  zone_name: string;
  plot_number: number;
  grid_row: number | null;
  grid_col: number | null;
  sub_zone: string;
  notes: string;
  color: string | null;
  plant_count: number;
  has_tree: boolean;
  has_bush: boolean;
  categories: string[];
}

export type PlantPresenceStatus = "present" | "mixed" | "gone";

export interface Plant {
  plt_id: string;
  name: string;
  latin: string;
  category: string;
  bloom_month: string;
  color: string;
  hardiness: string;
  height_cm: number | null;
  light: string;
  link: string;
  year_planted: string | null;
  deer_resistant: boolean;
  care_watering: string;
  care_soil: string;
  care_planting: string;
  care_maintenance: string;
  care_notes: string;
  quantity?: number;
  plot_ids?: string[];
  missing_plot_ids?: string[];
  seen_growing?: boolean | null;
  seen_growing_date?: string | null;
  seen_growing_year?: number | null;
  seen_growing_is_current_year?: boolean;
  observed_this_year?: boolean;
  last_bloomed_on?: string | null;
  last_bloomed_year?: number | null;
  bloomed_this_year?: boolean;
  presence_status?: PlantPresenceStatus;
  last_not_seen_year?: string | null;
}

export interface IndoorPlant extends Plant {
  room_label: string | null;
  quantity: number;
}

/** Format a plot label for display. Indoor plots show zone_name + room. */
export function formatPlotLabel(
  plot_id: string,
  zone_name: string,
  roomLabel?: string | null,
): string {
  if (plot_id.startsWith("INDOOR-")) {
    return roomLabel ? `${zone_name} \u2014 ${roomLabel}` : zone_name;
  }
  return plot_id;
}

export interface MoveAction {
  plots: Array<{ plot_id: string; row: number; col: number }>;
  house?: { row: number; col: number; width: number; height: number };
  mapObject?: { public_id: string; geometry: MapObjectGeometry };
}

export interface CameraState {
  x: number;
  y: number;
  zoom: number;
}

export type JournalEventType =
  | "planted"
  | "moved"
  | "divided"
  | "pruned"
  | "watered"
  | "fertilized"
  | "bloomed"
  | "harvested"
  | "died"
  | "observed";

export interface JournalEntry {
  id: string;
  garden_id: number;
  event_type: JournalEventType;
  occurred_on: string;
  title: string;
  notes: string;
  metadata: Record<string, unknown>;
  actor_user_id: number | null;
  actor_username: string | null;
  created_at_ms: number;
  updated_at_ms: number;
  plant_ids: string[];
  plot_ids: string[];
  plots?: Array<{ plot_id: string; zone_name: string }>;
}

export interface JournalListResponse {
  entries: JournalEntry[];
  total: number;
}

export type TaskType =
  | "water"
  | "protect"
  | "prune"
  | "deadhead"
  | "divide"
  | "fertilize"
  | "sow"
  | "plant_out"
  | "observe_bloom"
  | "harvest"
  | "inspect_issue";

export type TaskStatus = "pending" | "completed" | "skipped" | "snoozed";

export type TaskSeverity = "low" | "normal" | "high";

export interface GardenTask {
  id: string;
  garden_id: number;
  task_type: TaskType;
  title: string;
  description: string;
  status: TaskStatus;
  severity: TaskSeverity;
  due_on: string;
  snoozed_until: string | null;
  window_start_on?: string | null;
  window_end_on?: string | null;
  window_kind?: "recommended" | "manual" | null;
  rule_source: string;
  metadata: Record<string, unknown>;
  created_by_user_id: number | null;
  completed_by_user_id: number | null;
  completed_at_ms: number | null;
  created_at_ms: number;
  updated_at_ms: number;
  plant_ids: string[];
  plot_ids: string[];
}

export interface TaskListResponse {
  tasks: GardenTask[];
  total: number;
}

export type AttentionProviderKey =
  | "task"
  | "weather"
  | "issue"
  | "calendar"
  | "notification_status";

export type AttentionCategory =
  | "needs_action"
  | "warning"
  | "upcoming"
  | "no_action_needed"
  | "system";

export type AttentionSeverity = "low" | "normal" | "high" | "critical";

export type AttentionDomainState =
  | "active"
  | "completed"
  | "skipped"
  | "dismissed"
  | "expired"
  | "superseded"
  | "no_action_needed";

export type AttentionUserState =
  | "unread"
  | "read"
  | "dismissed"
  | "snoozed"
  | "preference_hidden";

export type AttentionSectionKey =
  | "needs_attention"
  | "warnings"
  | "coming_up"
  | "no_action_needed";

export interface AttentionAction {
  kind:
    | "open_task"
    | "open_issue"
    | "open_weather"
    | "focus_plant"
    | "select_plot"
    | "open_attention_detail"
    | "restore_attention_outcome";
  label: string;
  target_type: string;
  target_id: string;
  metadata: Record<string, unknown>;
}

export interface AttentionItem {
  id: string;
  provider: AttentionProviderKey;
  type: string;
  category: AttentionCategory;
  severity: AttentionSeverity;
  title: string;
  body: string;
  reason: string;
  target_type: string | null;
  target_id: string | null;
  plot_ids: string[];
  plant_ids: string[];
  due_on: string | null;
  domain_state: AttentionDomainState;
  user_state: AttentionUserState;
  primary_action: AttentionAction | null;
  secondary_actions: AttentionAction[];
  metadata: Record<string, unknown>;
  source_label: string;
  updated_at_ms: number;
}

export interface AttentionSection {
  key: AttentionSectionKey;
  count: number;
  items: AttentionItem[];
}

export type AttentionPreferencePreset = "calm" | "balanced" | "detailed" | "custom";

export interface AttentionPreferenceRule {
  enabled?: boolean;
  panel?: boolean;
  inbox?: boolean;
  digest?: boolean;
  interruptive?: boolean;
  min_severity?: AttentionSeverity;
}

export interface AttentionPreferences {
  user_id: number;
  preset: AttentionPreferencePreset;
  rules: Record<string, AttentionPreferenceRule>;
  quiet_hours: Record<string, unknown>;
  show_no_action_history: boolean;
  metadata: Record<string, unknown>;
}

export interface AttentionPreferencesUpdate {
  preset: AttentionPreferencePreset;
  rules: Record<string, AttentionPreferenceRule>;
  quiet_hours: Record<string, unknown>;
  show_no_action_history: boolean;
}

export interface AttentionTodayResponse {
  garden_id: number;
  generated_at_ms: number;
  sections: AttentionSection[];
  counts: Record<AttentionSectionKey | "total", number>;
  preferences: AttentionPreferences;
  degraded_providers: Array<Record<string, string>>;
}

export type NotificationType =
  | "task_due"
  | "task_overdue"
  | "task_upcoming"
  | "task_generated"
  | "issue_created"
  | "weather_alert"
  | "system";

export interface NotificationEvent {
  id: string;
  garden_id: number;
  user_id: number | null;
  notification_type: NotificationType;
  notification_subtype: string | null;
  severity: "low" | "normal" | "high" | "critical";
  title: string;
  body: string;
  target_type: "task" | "plant" | "plot" | "issue" | "weather_alert" | "task_batch" | null;
  target_id: string | null;
  read_at_ms: number | null;
  dismissed: boolean;
  expires_at_ms: number | null;
  cleared_at_ms: number | null;
  clear_reason: string | null;
  created_at_ms: number;
  metadata: Record<string, string | string[] | number | null> | null;
}

export interface NotificationListResponse {
  notifications: NotificationEvent[];
  total: number;
}

export interface NotificationPreferences {
  in_app_enabled: boolean;
  email_enabled: boolean;
  email_address: string;
  digest_frequency: "none" | "daily" | "weekly";
  quiet_hours_json: Record<string, unknown>;
  task_due_enabled: boolean;
  task_overdue_enabled: boolean;
  notification_rules: Record<string, NotificationRulePreference>;
  policy: NotificationPolicyRule[];
}

export interface NotificationRulePreference {
  in_app_enabled: boolean;
  email_enabled: boolean;
  min_severity: "low" | "normal" | "high" | "critical";
}

export interface NotificationPolicyRule {
  key: string;
  group: "tasks" | "weather" | "issues" | "system";
  notification_type: NotificationType;
  notification_subtype: string | null;
  default_in_app_enabled: boolean;
  default_email_enabled: boolean;
  supports_severity: boolean;
  default_min_severity: "low" | "normal" | "high" | "critical";
  user_configurable: boolean;
}

export type WeatherAlertType = "frost_warning" | "heat_wave" | "dry_spell" | "rain_surplus";

export interface WeatherAlert {
  id: number;
  garden_id: number;
  alert_type: WeatherAlertType;
  severity: string;
  title: string;
  description: string;
  valid_from: string;
  valid_until: string;
  metadata: Record<string, unknown>;
  dismissed: boolean;
  created_at_ms: number;
  plant_ids: string[];
}

export interface WeatherForecastDay {
  date: string;
  temp_min: number | null;
  temp_max: number | null;
  precipitation: number | null;
  precipitation_probability: number | null;
  wind_speed: number | null;
}

export interface WeatherSummary {
  forecast_available: boolean;
  forecast_days: WeatherForecastDay[];
  alerts: WeatherAlert[];
  frost_vulnerable_plants: Array<{ plt_id: string; name: string; hardiness: string }>;
  watering_sensitive_plants: Array<{ plt_id: string; name: string }>;
}

export type CalendarSourceKey = TaskType | "weather_alert" | "garden_event";

export type CalendarPresetKey =
  | "essential"
  | "all_care"
  | "watering"
  | "harvest_season"
  | "high_value";

export type CalendarViewMode = "month" | "week" | "agenda";

export interface CalendarSourceDefinition {
  key: CalendarSourceKey;
  kind: "task" | "weather" | "manual";
}

export interface CalendarPresetDefinition {
  key: CalendarPresetKey;
  source_keys: CalendarSourceKey[];
}

export interface CalendarPreferences {
  default_view: CalendarViewMode;
  selected_preset: CalendarPresetKey;
  visible_sources: CalendarSourceKey[];
  include_recent_history: boolean;
  selected_plant_ids: string[];
  selected_plot_ids: string[];
  selected_zone_codes: string[];
}

export interface CalendarCapabilities {
  can_subscribe: boolean;
  can_revoke_all: boolean;
}

export interface CalendarEvent {
  id: string;
  kind: "task" | "weather_alert" | "manual_event";
  source_key: CalendarSourceKey;
  title: string;
  description: string;
  start_on: string;
  end_on: string;
  all_day: boolean;
  status: string;
  severity: string;
  read_only: boolean;
  target_type: "task" | "weather_alert" | "manual_event";
  target_id: string;
  plant_ids: string[];
  plot_ids: string[];
  due_on?: string;
  snoozed_until?: string | null;
  updated_at_ms: number;
  created_at_ms: number;
  completed_at_ms?: number | null;
  window_start_on?: string;
  window_end_on?: string;
  window_kind?: string;
  window_state?: "upcoming" | "active" | "elapsed";
  valid_from?: string;
  valid_until?: string;
}

export interface CalendarManualEventInput {
  title: string;
  event_on: string;
  description?: string;
  plant_ids?: string[];
  plot_ids?: string[];
}

export interface CalendarManualEventDraft {
  title?: string;
  event_on?: string;
  description?: string;
  plant_ids?: string[];
  plot_ids?: string[];
}

export interface CalendarEventsResponse {
  events: CalendarEvent[];
  range: {
    start_on: string;
    end_on: string;
  };
  latest_updated_at_ms: number;
  selected_preset: CalendarPresetKey;
  visible_sources: CalendarSourceKey[];
  include_recent_history: boolean;
  selected_plant_ids: string[];
  selected_plot_ids: string[];
  selected_zone_codes: string[];
}

export interface CalendarPreferencesResponse {
  preferences: CalendarPreferences;
  persisted: boolean;
  available_views: CalendarViewMode[];
  available_sources: CalendarSourceDefinition[];
  presets: CalendarPresetDefinition[];
  capabilities: CalendarCapabilities;
}

export interface CalendarSubscription {
  id: string;
  label: string;
  preset_key: CalendarPresetKey;
  visible_sources: CalendarSourceKey[];
  token_hint: string;
  created_at_ms: number;
  updated_at_ms: number;
  owner_user_id: number;
  owned_by_me: boolean;
  can_revoke: boolean;
}

export type IssueType =
  | "pest"
  | "disease"
  | "fungal"
  | "nutrient"
  | "environmental"
  | "damage"
  | "other";

export type IssueStatus = "open" | "monitoring" | "treating" | "resolved" | "dismissed";

export type IssueSeverity = "low" | "normal" | "high" | "critical";

export interface GardenIssue {
  id: string;
  garden_id: number;
  issue_type: IssueType;
  title: string;
  description: string;
  severity: IssueSeverity;
  status: IssueStatus;
  suspected_cause: string;
  treatment_plan: string;
  follow_up_on: string | null;
  metadata: Record<string, unknown>;
  created_by_user_id: number | null;
  resolved_by_user_id: number | null;
  resolved_at_ms: number | null;
  created_at_ms: number;
  updated_at_ms: number;
  plant_ids: string[];
  plot_ids: string[];
}

export interface IssueListResponse {
  issues: GardenIssue[];
  total: number;
}

export interface IssueHistoryEvent {
  kind: "created" | "updated" | "resolved";
  at_ms: number;
  actor_user_id: number | null;
  actor_username: string | null;
  title: string;
  status: IssueStatus;
  severity: IssueSeverity;
  summary: string;
}

export interface IssueHistoryResponse {
  issue_events: IssueHistoryEvent[];
  journal_entries: JournalEntry[];
}

export interface IssueSummary {
  open: number;
  monitoring: number;
  treating: number;
  resolved: number;
  total: number;
}

export type HarvestUnit = "kg" | "g" | "lbs" | "oz" | "pieces" | "bunches" | "liters" | "heads" | "other";
export type HarvestQuality = "excellent" | "good" | "fair" | "poor";

export interface HarvestEntry {
  id: string;
  garden_id: number;
  occurred_on: string;
  quantity: number;
  unit: HarvestUnit;
  quality: HarvestQuality;
  notes: string;
  metadata: Record<string, unknown>;
  actor_user_id: number | null;
  created_at_ms: number;
  updated_at_ms: number;
  plant_ids: string[];
  plot_ids: string[];
  plots?: Array<{ plot_id: string; zone_name: string }>;
}

export interface HarvestListResponse {
  entries: HarvestEntry[];
  total: number;
}

export interface HarvestSummary {
  year: number;
  total_entries: number;
  by_plant: Array<{ plt_id: string; name: string; total_qty: number; unit: string; entries: number }>;
  by_month: Array<{ month: number; total_qty: number; entries: number }>;
  by_quality: { excellent: number; good: number; fair: number; poor: number };
}

export type SavedViewType =
  | "plants"
  | "tasks"
  | "calendar"
  | "journal"
  | "issues"
  | "inventory"
  | "harvest"
  | "procurement";

export interface SavedView {
  id: number;
  user_id: number | null;
  garden_id: number;
  view_type: SavedViewType;
  label: string;
  filter_json: Record<string, unknown>;
  is_preset: boolean;
  sort_order: number;
  created_at_ms: number;
  updated_at_ms: number;
}

export interface SavedViewPreset {
  view_type: SavedViewType;
  label: string;
  filter_json: Record<string, unknown>;
  preset_key: string;
}

export type ProcurementStatus = "wanted" | "ordered" | "shipped" | "received" | "cancelled";

export interface ProcurementItem {
  id: string;
  garden_id: number;
  label: string;
  inventory_type: string;
  linked_plt_id: string | null;
  linked_plot_id: string | null;
  vendor_name: string;
  vendor_url: string;
  status: ProcurementStatus;
  cost_minor: number;
  currency: string;
  quantity: number;
  unit: string;
  ordered_on: string | null;
  expected_on: string | null;
  received_on: string | null;
  notes: string;
  metadata: Record<string, unknown>;
  created_by_user_id: number | null;
  created_at_ms: number;
  updated_at_ms: number;
}

export interface ProcurementListResponse {
  items: ProcurementItem[];
  total: number;
}

export interface ProcurementSummary {
  wanted: number;
  ordered: number;
  shipped: number;
  received: number;
  cancelled: number;
  total: number;
  total_cost_minor: number;
  currency: string;
}

export interface PlantingSuggestion {
  plt_id: string;
  name: string;
  latin: string;
  category: string;
  bloom_month: string;
  color: string;
  hardiness: string;
  height_cm: number | null;
  light: string;
  deer_resistant: boolean;
  score: number;
  reasons: string[];
}

export interface PlotSuggestions {
  plot_id: string;
  zone_code: string;
  zone_name: string;
  suggestions: PlantingSuggestion[];
}

export interface PlannerResult {
  plots: PlotSuggestions[];
  bloom_gaps: number[];
  garden_stats: { total_plots: number; empty_plots: number; planted_plots: number };
}

export interface GardenProfile {
  total_plots: number;
  empty_plots: number;
  planted_plots: number;
  bloom_coverage: number[];
  bloom_gaps: number[];
  categories: Record<string, number>;
  colors: Record<string, number>;
  hardiness_range: { min: string; max: string };
  deer_resistant_count: number;
  deer_vulnerable_count: number;
}

export interface CompanionCheck {
  companions: Array<{ description: string }>;
  conflicts: Array<{ description: string }>;
}

export interface OfflineDraft {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  garden_id?: number | null;
  created_at_ms: number;
  status: "pending" | "syncing" | "failed";
  retry_count: number;
  last_error: string;
}

export type MapObjectType =
  | "patio"
  | "terrace"
  | "greenhouse"
  | "shed"
  | "pond"
  | "path"
  | "bed"
  | "other";

export type MapObjectShape = "rectangle" | "ellipse";
export type MapObjectUnitType = "pot" | "planter" | "raised_bed" | "shelf" | "other";

export interface MapObjectGeometry {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface MapObjectStyle {
  color: string;
}

export interface MapObjectInternalLayout {
  rows: number;
  cols: number;
}

export interface MapObjectUnit {
  public_id: string;
  unit_type: MapObjectUnitType;
  name: string;
  shape_type: MapObjectShape;
  geometry: MapObjectGeometry;
  style: MapObjectStyle;
  sort_order: number;
  created_at_ms?: number;
  updated_at_ms?: number;
}

export interface MapObject {
  public_id: string;
  object_type: MapObjectType;
  name: string;
  shape_type: MapObjectShape;
  geometry: MapObjectGeometry;
  style: MapObjectStyle;
  z_index: number;
  has_internal_layout: boolean;
  internal_layout: MapObjectInternalLayout;
  created_at_ms?: number;
  updated_at_ms?: number;
  units: MapObjectUnit[];
}

export interface MapObjectInput {
  object_type: MapObjectType;
  name: string;
  shape_type: MapObjectShape;
  geometry: MapObjectGeometry;
  style?: MapObjectStyle;
  z_index?: number;
  has_internal_layout?: boolean;
  internal_layout?: MapObjectInternalLayout | null;
}

export interface MapObjectUnitInput {
  unit_type: MapObjectUnitType;
  name: string;
  shape_type: MapObjectShape;
  geometry: MapObjectGeometry;
  style?: MapObjectStyle;
  sort_order?: number;
}

export type AppTab = "map" | "garden" | "activity" | "insights" | "admin";

export interface AppState {
  plots: Plot[];
  mapObjects: MapObject[];
  selectedMapObjectId: string | null;
  showMapObjects: boolean;
  plantsCache: Plant[];
  selectedPlotId: string | null;
  selectedPlotIds: Set<string>;
  sunlitPlotIds: Set<string>;
  editMode: boolean;
  housePosition: { row: number; col: number };
  houseSize: { width: number; height: number };
  northDegrees: number;
  gridRows: number;
  gridCols: number;
  undoStack: MoveAction[];
  highlightedPlotIds: Set<string>;
  highlightedPlantName: string;
  plotAlerts: {
    task_plots: Set<string>;
    issue_plots: Set<string>;
    frost_plots: Set<string>;
  } | null;
}

export interface PasswordPolicy {
  min_length: number;
  require_lower: boolean;
  require_upper: boolean;
  require_digit: boolean;
  require_symbol: boolean;
  reject_common: boolean;
  disallow_username: boolean;
  check_hibp: boolean;
}
