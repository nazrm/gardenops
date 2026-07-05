ALTER TABLE public.user_attention_preferences
    ADD COLUMN IF NOT EXISTS metadata_json text DEFAULT '{}'::text NOT NULL;
