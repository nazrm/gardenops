CREATE TABLE IF NOT EXISTS public.garden_calendar_events (
    id bigint NOT NULL,
    public_id text DEFAULT ''::text NOT NULL,
    garden_id bigint NOT NULL,
    title text DEFAULT ''::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    event_on text NOT NULL,
    created_by_user_id bigint NOT NULL,
    updated_by_user_id bigint NOT NULL,
    created_at_ms bigint NOT NULL,
    updated_at_ms bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS public.garden_calendar_event_plots (
    event_id bigint NOT NULL,
    plot_id text NOT NULL
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n
          ON n.oid = c.relnamespace
        WHERE c.relkind = 'S'
          AND c.relname = 'garden_calendar_events_id_seq'
          AND n.nspname = 'public'
    ) THEN
        CREATE SEQUENCE public.garden_calendar_events_id_seq
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
          AND table_name = 'garden_calendar_events'
          AND column_name = 'id'
          AND is_identity = 'NO'
    ) THEN
        ALTER TABLE public.garden_calendar_events
            ALTER COLUMN id SET DEFAULT nextval('public.garden_calendar_events_id_seq');
        ALTER SEQUENCE public.garden_calendar_events_id_seq
            OWNED BY public.garden_calendar_events.id;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'garden_calendar_events_pkey'
    ) THEN
        ALTER TABLE ONLY public.garden_calendar_events
            ADD CONSTRAINT garden_calendar_events_pkey PRIMARY KEY (id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'garden_calendar_event_plots_pkey'
    ) THEN
        ALTER TABLE ONLY public.garden_calendar_event_plots
            ADD CONSTRAINT garden_calendar_event_plots_pkey PRIMARY KEY (event_id, plot_id);
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_garden_calendar_events_public_id
    ON public.garden_calendar_events USING btree (public_id);

CREATE INDEX IF NOT EXISTS idx_garden_calendar_events_garden_event_on
    ON public.garden_calendar_events USING btree (garden_id, event_on, updated_at_ms DESC);

CREATE INDEX IF NOT EXISTS idx_garden_calendar_event_plots_plot
    ON public.garden_calendar_event_plots USING btree (plot_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_garden_calendar_events_garden_id_gardens'
    ) THEN
        ALTER TABLE ONLY public.garden_calendar_events
            ADD CONSTRAINT fk_garden_calendar_events_garden_id_gardens
            FOREIGN KEY (garden_id) REFERENCES public.gardens(id) ON DELETE CASCADE DEFERRABLE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_garden_calendar_events_created_by_user_id_auth_users'
    ) THEN
        ALTER TABLE ONLY public.garden_calendar_events
            ADD CONSTRAINT fk_garden_calendar_events_created_by_user_id_auth_users
            FOREIGN KEY (created_by_user_id) REFERENCES public.auth_users(id) ON DELETE CASCADE DEFERRABLE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_garden_calendar_events_updated_by_user_id_auth_users'
    ) THEN
        ALTER TABLE ONLY public.garden_calendar_events
            ADD CONSTRAINT fk_garden_calendar_events_updated_by_user_id_auth_users
            FOREIGN KEY (updated_by_user_id) REFERENCES public.auth_users(id) ON DELETE CASCADE DEFERRABLE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_garden_calendar_event_plots_event_id_calendar_events'
    ) THEN
        ALTER TABLE ONLY public.garden_calendar_event_plots
            ADD CONSTRAINT fk_garden_calendar_event_plots_event_id_calendar_events
            FOREIGN KEY (event_id) REFERENCES public.garden_calendar_events(id) ON DELETE CASCADE DEFERRABLE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_garden_calendar_event_plots_plot_id_plots'
    ) THEN
        ALTER TABLE ONLY public.garden_calendar_event_plots
            ADD CONSTRAINT fk_garden_calendar_event_plots_plot_id_plots
            FOREIGN KEY (plot_id) REFERENCES public.plots(plot_id) ON DELETE CASCADE DEFERRABLE;
    END IF;
END
$$;
