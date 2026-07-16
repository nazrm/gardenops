ALTER TABLE public.auth_sessions
    ADD COLUMN IF NOT EXISTS device_label text DEFAULT ''::text NOT NULL,
    ADD COLUMN IF NOT EXISTS location_hint text DEFAULT ''::text NOT NULL;
