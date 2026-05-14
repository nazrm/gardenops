ALTER TABLE public.garden_tasks
    ADD COLUMN IF NOT EXISTS public_id text;

ALTER TABLE public.inventory_items
    ADD COLUMN IF NOT EXISTS public_id text;

ALTER TABLE public.procurement_items
    ADD COLUMN IF NOT EXISTS public_id text;

ALTER TABLE public.garden_tasks
    ALTER COLUMN public_id SET DEFAULT ('tsk_' || substr(md5(random()::text || clock_timestamp()::text), 1, 20));

ALTER TABLE public.inventory_items
    ALTER COLUMN public_id SET DEFAULT ('inv_' || substr(md5(random()::text || clock_timestamp()::text), 1, 20));

ALTER TABLE public.procurement_items
    ALTER COLUMN public_id SET DEFAULT ('prc_' || substr(md5(random()::text || clock_timestamp()::text), 1, 20));

UPDATE public.garden_tasks
SET public_id = 'tsk_' || substr(md5(random()::text || clock_timestamp()::text || id::text), 1, 20)
WHERE public_id IS NULL;

UPDATE public.inventory_items
SET public_id = 'inv_' || substr(md5(random()::text || clock_timestamp()::text || id::text), 1, 20)
WHERE public_id IS NULL;

UPDATE public.procurement_items
SET public_id = 'prc_' || substr(md5(random()::text || clock_timestamp()::text || id::text), 1, 20)
WHERE public_id IS NULL;

ALTER TABLE public.garden_tasks
    ALTER COLUMN public_id SET NOT NULL;

ALTER TABLE public.inventory_items
    ALTER COLUMN public_id SET NOT NULL;

ALTER TABLE public.procurement_items
    ALTER COLUMN public_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_garden_tasks_public_id
    ON public.garden_tasks USING btree (public_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_items_public_id
    ON public.inventory_items USING btree (public_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_procurement_items_public_id
    ON public.procurement_items USING btree (public_id);

UPDATE public.notification_events AS n
SET target_id = t.public_id
FROM public.garden_tasks AS t
WHERE n.target_type = 'task'
  AND n.target_id ~ '^[0-9]+$'
  AND t.id = n.target_id::bigint;

UPDATE public.procurement_items AS p
SET metadata_json = jsonb_set(
    COALESCE(NULLIF(p.metadata_json, '')::jsonb, '{}'::jsonb),
    '{inventory_item_id}',
    to_jsonb(i.public_id),
    true
)::text
FROM public.inventory_items AS i
WHERE COALESCE(NULLIF(p.metadata_json, ''), '') != ''
  AND (p.metadata_json::jsonb ->> 'inventory_item_id') = i.id::text;
