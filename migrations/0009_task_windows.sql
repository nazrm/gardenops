ALTER TABLE public.garden_tasks
    ADD COLUMN IF NOT EXISTS window_start_on date;

ALTER TABLE public.garden_tasks
    ADD COLUMN IF NOT EXISTS window_end_on date;

ALTER TABLE public.garden_tasks
    ADD COLUMN IF NOT EXISTS window_kind text;

UPDATE public.garden_tasks
SET window_start_on = COALESCE(
        window_start_on,
        (due_on::date - INTERVAL '21 days')::date
    ),
    window_end_on = COALESCE(
        window_end_on,
        (due_on::date + INTERVAL '14 days')::date
    ),
    window_kind = CASE
        WHEN COALESCE(NULLIF(window_kind, ''), '') = '' THEN 'recommended'
        ELSE window_kind
    END
WHERE task_type = 'prune'
  AND due_on IS NOT NULL;

UPDATE public.garden_tasks
SET window_start_on = COALESCE(
        window_start_on,
        (due_on::date - INTERVAL '10 days')::date
    ),
    window_end_on = COALESCE(
        window_end_on,
        (due_on::date + INTERVAL '5 days')::date
    ),
    window_kind = CASE
        WHEN COALESCE(NULLIF(window_kind, ''), '') = '' THEN 'recommended'
        ELSE window_kind
    END
WHERE task_type = 'sow'
  AND due_on IS NOT NULL;

UPDATE public.garden_tasks
SET window_start_on = COALESCE(
        window_start_on,
        (due_on::date - INTERVAL '5 days')::date
    ),
    window_end_on = COALESCE(
        window_end_on,
        (due_on::date + INTERVAL '7 days')::date
    ),
    window_kind = CASE
        WHEN COALESCE(NULLIF(window_kind, ''), '') = '' THEN 'recommended'
        ELSE window_kind
    END
WHERE task_type = 'plant_out'
  AND due_on IS NOT NULL;

UPDATE public.garden_tasks
SET window_start_on = COALESCE(
        window_start_on,
        (due_on::date - INTERVAL '4 days')::date
    ),
    window_end_on = COALESCE(
        window_end_on,
        (due_on::date + INTERVAL '7 days')::date
    ),
    window_kind = CASE
        WHEN COALESCE(NULLIF(window_kind, ''), '') = '' THEN 'recommended'
        ELSE window_kind
    END
WHERE task_type = 'harvest'
  AND due_on IS NOT NULL;
