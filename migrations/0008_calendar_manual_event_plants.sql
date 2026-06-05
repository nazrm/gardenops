CREATE TABLE IF NOT EXISTS public.garden_calendar_event_plants (
    event_id bigint NOT NULL,
    plt_id text NOT NULL
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'garden_calendar_event_plants_pkey'
    ) THEN
        ALTER TABLE ONLY public.garden_calendar_event_plants
            ADD CONSTRAINT garden_calendar_event_plants_pkey PRIMARY KEY (event_id, plt_id);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_garden_calendar_event_plants_plt_id
    ON public.garden_calendar_event_plants USING btree (plt_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_garden_calendar_event_plants_event_id_calendar_events'
    ) THEN
        ALTER TABLE ONLY public.garden_calendar_event_plants
            ADD CONSTRAINT fk_garden_calendar_event_plants_event_id_calendar_events
            FOREIGN KEY (event_id) REFERENCES public.garden_calendar_events(id) ON DELETE CASCADE DEFERRABLE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_garden_calendar_event_plants_plt_id_plants'
    ) THEN
        ALTER TABLE ONLY public.garden_calendar_event_plants
            ADD CONSTRAINT fk_garden_calendar_event_plants_plt_id_plants
            FOREIGN KEY (plt_id) REFERENCES public.plants(plt_id) ON DELETE CASCADE DEFERRABLE;
    END IF;
END
$$;
