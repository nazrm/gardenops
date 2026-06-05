ALTER TABLE public.user_calendar_preferences
    ADD COLUMN IF NOT EXISTS selected_plot_ids_json text DEFAULT '[]'::text NOT NULL;

ALTER TABLE public.user_calendar_preferences
    ADD COLUMN IF NOT EXISTS selected_zone_codes_json text DEFAULT '[]'::text NOT NULL;

UPDATE public.user_calendar_preferences
SET selected_plot_ids_json = '[]'
WHERE selected_plot_ids_json IS NULL;

UPDATE public.user_calendar_preferences
SET selected_zone_codes_json = '[]'
WHERE selected_zone_codes_json IS NULL;
