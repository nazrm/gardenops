ALTER TABLE public.layout_snapshots
    ADD COLUMN IF NOT EXISTS public_id text;

UPDATE public.layout_snapshots
SET public_id = 'snap_' || substr(
    md5(id::text || ':' || COALESCE(garden_id, 0)::text || ':' || created_at || ':' || data),
    1,
    20
)
WHERE public_id IS NULL OR public_id = '';

ALTER TABLE public.layout_snapshots
    ALTER COLUMN public_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_layout_snapshots_public_id
    ON public.layout_snapshots USING btree (public_id);

ALTER TABLE public.notification_events
    ADD COLUMN IF NOT EXISTS public_id text;

UPDATE public.notification_events
SET public_id = 'note_' || substr(
    md5(id::text || ':' || garden_id::text || ':' || COALESCE(user_id, 0)::text || ':' || created_at_ms::text),
    1,
    20
)
WHERE public_id IS NULL OR public_id = '';

ALTER TABLE public.notification_events
    ALTER COLUMN public_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_events_public_id
    ON public.notification_events USING btree (public_id);
