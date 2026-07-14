CREATE OR REPLACE FUNCTION public.enforce_garden_task_updated_at_ms()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at_ms := GREATEST(
        COALESCE(NEW.updated_at_ms, 0),
        COALESCE(OLD.updated_at_ms, 0) + 1
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_garden_tasks_monotonic_updated_at_ms ON public.garden_tasks;

CREATE TRIGGER trg_garden_tasks_monotonic_updated_at_ms
BEFORE UPDATE ON public.garden_tasks
FOR EACH ROW
EXECUTE FUNCTION public.enforce_garden_task_updated_at_ms();

UPDATE public.garden_tasks
SET window_start_on = COALESCE(
        window_start_on,
        (due_on::date - INTERVAL '7 days')::date
    ),
    window_end_on = COALESCE(
        window_end_on,
        (due_on::date + INTERVAL '7 days')::date
    ),
    window_kind = CASE
        WHEN COALESCE(NULLIF(window_kind, ''), '') = '' THEN 'recommended'
        ELSE window_kind
    END
WHERE task_type = 'fertilize'
  AND due_on IS NOT NULL
  AND (
      window_start_on IS NULL
      OR window_end_on IS NULL
      OR COALESCE(NULLIF(window_kind, ''), '') = ''
  );
