ALTER TABLE public.notification_events
    ADD COLUMN IF NOT EXISTS notification_subtype text;

ALTER TABLE public.notification_events
    ADD COLUMN IF NOT EXISTS severity text DEFAULT 'normal'::text;

ALTER TABLE public.notification_events
    ADD COLUMN IF NOT EXISTS expires_at_ms bigint;

ALTER TABLE public.notification_events
    ADD COLUMN IF NOT EXISTS cleared_at_ms bigint;

ALTER TABLE public.notification_events
    ADD COLUMN IF NOT EXISTS clear_reason text;

ALTER TABLE public.notification_events
    ADD COLUMN IF NOT EXISTS superseded_by_id bigint;

ALTER TABLE public.user_notification_preferences
    ADD COLUMN IF NOT EXISTS rules_json text DEFAULT '{}'::text NOT NULL;

INSERT INTO public.notification_events (
    public_id,
    garden_id,
    user_id,
    notification_type,
    notification_subtype,
    severity,
    title,
    body,
    target_type,
    target_id,
    read_at_ms,
    emailed_at_ms,
    metadata_json,
    dismissed,
    created_at_ms,
    expires_at_ms,
    cleared_at_ms,
    clear_reason,
    superseded_by_id
)
SELECT
    'note_' || substr(md5(n.id::text || ':' || gm.user_id::text || ':recipient'), 1, 20),
    n.garden_id,
    gm.user_id,
    n.notification_type,
    n.notification_subtype,
    COALESCE(n.severity, 'normal'),
    n.title,
    n.body,
    n.target_type,
    n.target_id,
    n.read_at_ms,
    n.emailed_at_ms,
    n.metadata_json,
    n.dismissed,
    n.created_at_ms,
    n.expires_at_ms,
    n.cleared_at_ms,
    n.clear_reason,
    n.superseded_by_id
FROM public.notification_events n
JOIN public.garden_memberships gm ON gm.garden_id = n.garden_id
WHERE n.user_id IS NULL
  AND n.notification_type <> 'system'
  AND NOT EXISTS (
      SELECT 1
      FROM public.notification_events existing
      WHERE existing.garden_id = n.garden_id
        AND existing.user_id = gm.user_id
        AND existing.notification_type = n.notification_type
        AND COALESCE(existing.notification_subtype, '') = COALESCE(n.notification_subtype, '')
        AND COALESCE(existing.target_type, '') = COALESCE(n.target_type, '')
        AND COALESCE(existing.target_id, '') = COALESCE(n.target_id, '')
        AND existing.created_at_ms = n.created_at_ms
  )
ON CONFLICT (public_id) DO NOTHING;

UPDATE public.notification_events n
SET cleared_at_ms = COALESCE(cleared_at_ms, (extract(epoch from now()) * 1000)::bigint),
    clear_reason = COALESCE(clear_reason, 'fanout_migrated')
WHERE n.user_id IS NULL
  AND n.notification_type <> 'system'
  AND n.cleared_at_ms IS NULL
  AND EXISTS (
      SELECT 1
      FROM public.garden_memberships gm
      WHERE gm.garden_id = n.garden_id
  );

CREATE INDEX IF NOT EXISTS ix_notification_events_user_active
    ON public.notification_events (garden_id, user_id, created_at_ms DESC)
    WHERE dismissed = 0 AND cleared_at_ms IS NULL;

CREATE INDEX IF NOT EXISTS ix_notification_events_user_log
    ON public.notification_events (garden_id, user_id, created_at_ms DESC);

CREATE INDEX IF NOT EXISTS ix_notification_events_expiry
    ON public.notification_events (expires_at_ms)
    WHERE dismissed = 0 AND cleared_at_ms IS NULL AND expires_at_ms IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_notification_events_target
    ON public.notification_events (garden_id, target_type, target_id, notification_type, user_id);
