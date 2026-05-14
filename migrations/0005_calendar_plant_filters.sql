ALTER TABLE public.user_calendar_preferences
    ADD COLUMN IF NOT EXISTS selected_plant_ids_json text DEFAULT '[]'::text NOT NULL;

UPDATE public.user_calendar_preferences
SET selected_plant_ids_json = '[]'
WHERE selected_plant_ids_json IS NULL;
