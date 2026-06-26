ALTER TABLE public.auth_users
    ADD COLUMN IF NOT EXISTS password_auth_disabled bigint DEFAULT 0 NOT NULL,
    ADD COLUMN IF NOT EXISTS passkey_user_handle text,
    ADD COLUMN IF NOT EXISTS passkey_prompt_dismissed_until_ms bigint DEFAULT 0 NOT NULL;

ALTER TABLE public.auth_password_reset_tokens
    ADD COLUMN IF NOT EXISTS purpose text DEFAULT 'password_reset' NOT NULL;

ALTER TABLE public.auth_passkey_challenges
    ADD COLUMN IF NOT EXISTS invitation_token_hash text,
    ADD COLUMN IF NOT EXISTS invitation_scope text,
    ADD COLUMN IF NOT EXISTS invitation_id bigint,
    ADD COLUMN IF NOT EXISTS invitee_username text;

UPDATE public.auth_users
SET passkey_user_handle =
    md5(random()::text || clock_timestamp()::text || id::text)
    || md5(id::text || clock_timestamp()::text || random()::text)
WHERE passkey_user_handle IS NULL
   OR passkey_user_handle = '';

ALTER TABLE public.auth_users
    ALTER COLUMN password_hash DROP NOT NULL;

ALTER TABLE ONLY public.auth_users
    DROP CONSTRAINT IF EXISTS ck_auth_users_password_auth_state;

ALTER TABLE ONLY public.auth_users
    ADD CONSTRAINT ck_auth_users_password_auth_state
    CHECK (
        (
            password_auth_disabled = 0
            AND password_hash IS NOT NULL
            AND length(password_hash) > 0
        )
        OR (
            password_auth_disabled = 1
            AND password_hash IS NULL
        )
    );

CREATE UNIQUE INDEX IF NOT EXISTS ux_auth_users_passkey_user_handle
    ON public.auth_users (passkey_user_handle)
    WHERE passkey_user_handle IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_auth_passkey_challenges_invitation
    ON public.auth_passkey_challenges (invitation_token_hash, expires_at_ms);

INSERT INTO schema_migrations (version) VALUES (16) ON CONFLICT DO NOTHING;
