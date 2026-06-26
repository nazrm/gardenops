ALTER TABLE public.auth_passkey_challenges
    ADD COLUMN IF NOT EXISTS invitation_user_handle text;

INSERT INTO schema_migrations (version) VALUES (17) ON CONFLICT DO NOTHING;
