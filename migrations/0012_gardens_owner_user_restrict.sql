DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_gardens_owner_user_id_auth_users'
    ) THEN
        ALTER TABLE ONLY public.gardens
            ADD CONSTRAINT fk_gardens_owner_user_id_auth_users
            FOREIGN KEY (owner_user_id) REFERENCES public.auth_users(id)
            ON DELETE RESTRICT
            NOT VALID;
    END IF;
END
$$;

INSERT INTO schema_migrations (version) VALUES (12) ON CONFLICT DO NOTHING;
