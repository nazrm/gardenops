ALTER TABLE public.plant_ownership
    ADD COLUMN IF NOT EXISTS created_at_ms bigint;

UPDATE public.plant_ownership
SET created_at_ms = ((extract(epoch FROM now()) * 1000)::bigint)
WHERE created_at_ms IS NULL;

ALTER TABLE public.plant_ownership
    ALTER COLUMN created_at_ms SET DEFAULT ((extract(epoch FROM now()) * 1000)::bigint),
    ALTER COLUMN created_at_ms SET NOT NULL;
