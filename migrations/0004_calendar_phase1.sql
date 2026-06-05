CREATE TABLE IF NOT EXISTS public.user_calendar_preferences (
    user_id bigint NOT NULL,
    garden_id bigint NOT NULL,
    default_view text DEFAULT 'month'::text NOT NULL,
    selected_preset text DEFAULT 'essential'::text NOT NULL,
    visible_sources_json text DEFAULT '[]'::text NOT NULL,
    include_recent_history bigint DEFAULT 1 NOT NULL,
    created_at_ms bigint NOT NULL,
    updated_at_ms bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS public.calendar_subscriptions (
    id bigint NOT NULL,
    public_id text DEFAULT ''::text NOT NULL,
    garden_id bigint NOT NULL,
    owner_user_id bigint NOT NULL,
    created_by_user_id bigint NOT NULL,
    label text DEFAULT ''::text NOT NULL,
    preset_key text DEFAULT 'essential'::text NOT NULL,
    token_hash text DEFAULT ''::text NOT NULL,
    token_hint text DEFAULT ''::text NOT NULL,
    scope_json text DEFAULT '{}'::text NOT NULL,
    created_at_ms bigint NOT NULL,
    updated_at_ms bigint NOT NULL,
    revoked_at_ms bigint
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n
          ON n.oid = c.relnamespace
        WHERE c.relkind = 'S'
          AND c.relname = 'calendar_subscriptions_id_seq'
          AND n.nspname = 'public'
    ) THEN
        CREATE SEQUENCE public.calendar_subscriptions_id_seq
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'calendar_subscriptions'
          AND column_name = 'id'
          AND is_identity = 'NO'
    ) THEN
        ALTER TABLE public.calendar_subscriptions
            ALTER COLUMN id SET DEFAULT nextval('public.calendar_subscriptions_id_seq');
        ALTER SEQUENCE public.calendar_subscriptions_id_seq
            OWNED BY public.calendar_subscriptions.id;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'user_calendar_preferences_pkey'
    ) THEN
        ALTER TABLE ONLY public.user_calendar_preferences
            ADD CONSTRAINT user_calendar_preferences_pkey PRIMARY KEY (user_id, garden_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'calendar_subscriptions_pkey'
    ) THEN
        ALTER TABLE ONLY public.calendar_subscriptions
            ADD CONSTRAINT calendar_subscriptions_pkey PRIMARY KEY (id);
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_subscriptions_public_id
    ON public.calendar_subscriptions USING btree (public_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_subscriptions_token_hash
    ON public.calendar_subscriptions USING btree (token_hash);

CREATE INDEX IF NOT EXISTS idx_calendar_subscriptions_garden
    ON public.calendar_subscriptions USING btree (garden_id, owner_user_id, revoked_at_ms);

CREATE INDEX IF NOT EXISTS idx_calendar_subscriptions_owner
    ON public.calendar_subscriptions USING btree (owner_user_id, revoked_at_ms);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_user_calendar_preferences_user_id_auth_users'
    ) THEN
        ALTER TABLE ONLY public.user_calendar_preferences
            ADD CONSTRAINT fk_user_calendar_preferences_user_id_auth_users
            FOREIGN KEY (user_id) REFERENCES public.auth_users(id) ON DELETE CASCADE DEFERRABLE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_user_calendar_preferences_garden_id_gardens'
    ) THEN
        ALTER TABLE ONLY public.user_calendar_preferences
            ADD CONSTRAINT fk_user_calendar_preferences_garden_id_gardens
            FOREIGN KEY (garden_id) REFERENCES public.gardens(id) ON DELETE CASCADE DEFERRABLE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_calendar_subscriptions_garden_id_gardens'
    ) THEN
        ALTER TABLE ONLY public.calendar_subscriptions
            ADD CONSTRAINT fk_calendar_subscriptions_garden_id_gardens
            FOREIGN KEY (garden_id) REFERENCES public.gardens(id) ON DELETE CASCADE DEFERRABLE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_calendar_subscriptions_owner_user_id_auth_users'
    ) THEN
        ALTER TABLE ONLY public.calendar_subscriptions
            ADD CONSTRAINT fk_calendar_subscriptions_owner_user_id_auth_users
            FOREIGN KEY (owner_user_id) REFERENCES public.auth_users(id) ON DELETE CASCADE DEFERRABLE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_calendar_subscriptions_created_by_user_id_auth_users'
    ) THEN
        ALTER TABLE ONLY public.calendar_subscriptions
            ADD CONSTRAINT fk_calendar_subscriptions_created_by_user_id_auth_users
            FOREIGN KEY (created_by_user_id) REFERENCES public.auth_users(id) ON DELETE CASCADE DEFERRABLE;
    END IF;
END
$$;
