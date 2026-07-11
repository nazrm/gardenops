-- A logical weather alert is identified by its garden, type, and start date.
-- Consolidate every dependent reference before enforcing that identity.
LOCK TABLE public.weather_alerts,
    public.weather_alert_plants,
    public.garden_tasks,
    public.user_attention_item_state,
    public.attention_outcomes
    IN SHARE ROW EXCLUSIVE MODE;

DROP TABLE IF EXISTS pg_temp.weather_alert_identity_map;
CREATE TEMPORARY TABLE weather_alert_identity_map ON COMMIT DROP AS
SELECT
    id AS alert_id,
    first_value(id) OVER (
        PARTITION BY garden_id, alert_type, valid_from
        ORDER BY
            dismissed ASC,
            CASE severity
                WHEN 'high' THEN 3
                WHEN 'normal' THEN 2
                WHEN 'low' THEN 1
                ELSE 0
            END DESC,
            valid_until DESC,
            created_at_ms DESC,
            id ASC
    ) AS canonical_id
FROM public.weather_alerts;

CREATE UNIQUE INDEX ON weather_alert_identity_map (alert_id);
CREATE INDEX ON weather_alert_identity_map (canonical_id);

-- Preserve the strongest active alert state, the longest validity window, and
-- all top-level metadata. The selected canonical row wins key conflicts, while
-- plant advice arrays are unioned so no plant-specific guidance is lost.
WITH parsed_alerts AS (
    SELECT
        mapping.canonical_id,
        alert.id,
        alert.created_at_ms,
        alert.dismissed,
        alert.severity,
        alert.valid_until,
        CASE
            WHEN pg_input_is_valid(COALESCE(NULLIF(alert.metadata_json, ''), '{}'), 'jsonb')
            THEN CASE
                WHEN jsonb_typeof(COALESCE(NULLIF(alert.metadata_json, ''), '{}')::jsonb) = 'object'
                THEN COALESCE(NULLIF(alert.metadata_json, ''), '{}')::jsonb
                ELSE '{}'::jsonb
            END
            ELSE '{}'::jsonb
        END AS metadata
    FROM weather_alert_identity_map AS mapping
    JOIN public.weather_alerts AS alert ON alert.id = mapping.alert_id
), metadata_pairs AS (
    SELECT
        parsed.canonical_id,
        entry.key,
        entry.value,
        (parsed.id = parsed.canonical_id)::integer AS is_canonical,
        parsed.created_at_ms,
        parsed.id
    FROM parsed_alerts AS parsed
    CROSS JOIN LATERAL jsonb_each(parsed.metadata) AS entry
    WHERE entry.key <> 'plant_advice'
), merged_metadata AS (
    SELECT
        canonical_id,
        jsonb_object_agg(
            key,
            value
            ORDER BY is_canonical, created_at_ms, id
        ) AS metadata
    FROM metadata_pairs
    GROUP BY canonical_id
), merged_plant_advice AS (
    SELECT
        parsed.canonical_id,
        jsonb_agg(advice.value ORDER BY advice.value::text) AS plant_advice
    FROM parsed_alerts AS parsed
    CROSS JOIN LATERAL (
        SELECT DISTINCT value
        FROM jsonb_array_elements(
            CASE
                WHEN jsonb_typeof(parsed.metadata -> 'plant_advice') = 'array'
                THEN parsed.metadata -> 'plant_advice'
                ELSE '[]'::jsonb
            END
        )
    ) AS advice
    GROUP BY parsed.canonical_id
), merged_state AS (
    SELECT
        parsed.canonical_id,
        max(parsed.valid_until) AS valid_until,
        min(parsed.dismissed) AS dismissed,
        (array_agg(
            parsed.severity
            ORDER BY
                parsed.dismissed ASC,
                CASE parsed.severity
                    WHEN 'high' THEN 3
                    WHEN 'normal' THEN 2
                    WHEN 'low' THEN 1
                    ELSE 0
                END DESC,
                parsed.created_at_ms DESC,
                parsed.id ASC
        ))[1] AS severity
    FROM parsed_alerts AS parsed
    GROUP BY parsed.canonical_id
)
UPDATE public.weather_alerts AS canonical
SET
    valid_until = state.valid_until,
    dismissed = state.dismissed,
    severity = state.severity,
    metadata_json = (
        COALESCE(metadata.metadata, '{}'::jsonb)
        || CASE
            WHEN advice.plant_advice IS NULL THEN '{}'::jsonb
            ELSE jsonb_build_object('plant_advice', advice.plant_advice)
        END
    )::text
FROM merged_state AS state
LEFT JOIN merged_metadata AS metadata
    ON metadata.canonical_id = state.canonical_id
LEFT JOIN merged_plant_advice AS advice
    ON advice.canonical_id = state.canonical_id
WHERE canonical.id = state.canonical_id;

INSERT INTO public.weather_alert_plants (alert_id, plt_id)
SELECT DISTINCT mapping.canonical_id, linked.plt_id
FROM weather_alert_identity_map AS mapping
JOIN public.weather_alert_plants AS linked ON linked.alert_id = mapping.alert_id
WHERE mapping.alert_id <> mapping.canonical_id
ON CONFLICT (alert_id, plt_id) DO NOTHING;

-- Generated weather tasks encode the alert id as the third rule-source segment.
UPDATE public.garden_tasks AS task
SET rule_source =
    split_part(task.rule_source, ':', 1) || ':'
    || split_part(task.rule_source, ':', 2) || ':'
    || mapping.canonical_id::text
    || substring(
        task.rule_source
        FROM length(
            split_part(task.rule_source, ':', 1) || ':'
            || split_part(task.rule_source, ':', 2) || ':'
            || mapping.alert_id::text
        ) + 1
    )
FROM weather_alert_identity_map AS mapping
WHERE mapping.alert_id <> mapping.canonical_id
  AND task.garden_id = (
      SELECT garden_id FROM public.weather_alerts WHERE id = mapping.alert_id
  )
  AND split_part(task.rule_source, ':', 1) = 'auto'
  AND split_part(task.rule_source, ':', 2) IN (
      'frost_protect', 'heat_protect', 'dry_water', 'rain_drainage'
  )
  AND split_part(task.rule_source, ':', 3) = mapping.alert_id::text;

-- User attention state keys use the numeric alert id. Keep the most recently
-- changed state when both the canonical and a redundant alert were acted on.
WITH remapped AS (
    SELECT DISTINCT ON (state.user_id, state.garden_id, mapping.canonical_id)
        state.user_id,
        state.garden_id,
        'attn:weather:alert:' || mapping.canonical_id::text AS item_id,
        state.user_state,
        state.snoozed_until_ms,
        state.reason,
        state.metadata_json,
        state.created_at_ms,
        state.updated_at_ms
    FROM public.user_attention_item_state AS state
    JOIN weather_alert_identity_map AS mapping
      ON state.item_id = 'attn:weather:alert:' || mapping.alert_id::text
    WHERE mapping.alert_id <> mapping.canonical_id
    ORDER BY
        state.user_id,
        state.garden_id,
        mapping.canonical_id,
        state.updated_at_ms DESC,
        state.id DESC
)
INSERT INTO public.user_attention_item_state (
    user_id, garden_id, item_id, user_state, snoozed_until_ms, reason,
    metadata_json, created_at_ms, updated_at_ms
)
SELECT
    user_id, garden_id, item_id, user_state, snoozed_until_ms, reason,
    metadata_json, created_at_ms, updated_at_ms
FROM remapped
ON CONFLICT (user_id, garden_id, item_id) DO UPDATE SET
    user_state = CASE
        WHEN excluded.updated_at_ms >= user_attention_item_state.updated_at_ms
        THEN excluded.user_state ELSE user_attention_item_state.user_state
    END,
    snoozed_until_ms = CASE
        WHEN excluded.updated_at_ms >= user_attention_item_state.updated_at_ms
        THEN excluded.snoozed_until_ms ELSE user_attention_item_state.snoozed_until_ms
    END,
    reason = CASE
        WHEN excluded.updated_at_ms >= user_attention_item_state.updated_at_ms
        THEN excluded.reason ELSE user_attention_item_state.reason
    END,
    metadata_json = CASE
        WHEN excluded.updated_at_ms >= user_attention_item_state.updated_at_ms
        THEN excluded.metadata_json ELSE user_attention_item_state.metadata_json
    END,
    updated_at_ms = greatest(
        excluded.updated_at_ms,
        user_attention_item_state.updated_at_ms
    );

DELETE FROM public.user_attention_item_state AS state
USING weather_alert_identity_map AS mapping
WHERE mapping.alert_id <> mapping.canonical_id
  AND state.item_id = 'attn:weather:alert:' || mapping.alert_id::text;

-- Rain outcomes retain both a direct source id and a metadata copy of the
-- weather id. Rehome both before removing duplicate alert rows.
UPDATE public.attention_outcomes AS outcome
SET
    source_id = CASE
        WHEN outcome.provider = 'weather'
         AND outcome.source_id = mapping.alert_id::text
        THEN mapping.canonical_id::text
        ELSE outcome.source_id
    END,
    metadata_json = CASE
        WHEN pg_input_is_valid(COALESCE(NULLIF(outcome.metadata_json, ''), '{}'), 'jsonb')
         AND COALESCE(NULLIF(outcome.metadata_json, ''), '{}')::jsonb
             ->> 'weather_alert_id' = mapping.alert_id::text
        THEN jsonb_set(
            COALESCE(NULLIF(outcome.metadata_json, ''), '{}')::jsonb,
            '{weather_alert_id}',
            to_jsonb(mapping.canonical_id::text),
            false
        )::text
        ELSE outcome.metadata_json
    END
FROM weather_alert_identity_map AS mapping
WHERE mapping.alert_id <> mapping.canonical_id
  AND outcome.garden_id = (
      SELECT garden_id FROM public.weather_alerts WHERE id = mapping.alert_id
  )
  AND (
      (outcome.provider = 'weather' AND outcome.source_id = mapping.alert_id::text)
      OR (
          pg_input_is_valid(COALESCE(NULLIF(outcome.metadata_json, ''), '{}'), 'jsonb')
          AND COALESCE(NULLIF(outcome.metadata_json, ''), '{}')::jsonb
              ->> 'weather_alert_id' = mapping.alert_id::text
      )
  );

DELETE FROM public.weather_alerts AS redundant
USING weather_alert_identity_map AS mapping
WHERE redundant.id = mapping.alert_id
  AND mapping.alert_id <> mapping.canonical_id;

CREATE UNIQUE INDEX IF NOT EXISTS ux_weather_alerts_identity
    ON public.weather_alerts USING btree (garden_id, alert_type, valid_from);
