CREATE TABLE IF NOT EXISTS public.app_secrets (
    key text PRIMARY KEY,
    encrypted_value bytea NOT NULL,
    encryption_key_id text NOT NULL DEFAULT 'app',
    value_last4 text,
    created_at_ms bigint NOT NULL DEFAULT ((extract(epoch FROM now()) * 1000)::bigint),
    updated_at_ms bigint NOT NULL DEFAULT ((extract(epoch FROM now()) * 1000)::bigint),
    updated_by_user_id bigint,
    CONSTRAINT app_secrets_updated_by_user_id_fkey
        FOREIGN KEY (updated_by_user_id) REFERENCES public.auth_users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS app_secrets_updated_by_user_id_idx
    ON public.app_secrets(updated_by_user_id);

DELETE FROM public.app_settings
WHERE key = 'shademap_api_key';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'auth_users'
          AND column_name = 'shademap_api_key'
    ) THEN
        UPDATE public.auth_users
        SET shademap_api_key = NULL
        WHERE shademap_api_key IS NOT NULL;
    END IF;
END $$;

ALTER TABLE public.auth_users
DROP COLUMN IF EXISTS shademap_api_key;
