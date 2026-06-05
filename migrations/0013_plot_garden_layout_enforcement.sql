ALTER TABLE public.plots
    ADD COLUMN IF NOT EXISTS garden_id bigint;

UPDATE public.plots p
SET garden_id = po.garden_id
FROM public.plot_ownership po
WHERE po.plot_id = p.plot_id
  AND p.garden_id IS DISTINCT FROM po.garden_id;

UPDATE public.plots p
SET garden_id = g.id
FROM public.gardens g
WHERE p.garden_id IS NULL
  AND g.slug = 'default';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.plots WHERE garden_id IS NULL) THEN
        RAISE EXCEPTION 'Cannot enforce plot garden layout: plots without garden scope remain';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.set_plots_default_garden_id()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.garden_id IS NULL THEN
        SELECT id
        INTO NEW.garden_id
        FROM public.gardens
        WHERE slug = 'default'
        ORDER BY id
        LIMIT 1;
    END IF;
    IF NEW.garden_id IS NULL THEN
        RAISE EXCEPTION 'plots.garden_id is required';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_plots_default_garden_id ON public.plots;
CREATE TRIGGER trg_plots_default_garden_id
    BEFORE INSERT ON public.plots
    FOR EACH ROW
    EXECUTE FUNCTION public.set_plots_default_garden_id();

CREATE OR REPLACE FUNCTION public.sync_plot_garden_id_from_ownership()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE public.plots
    SET garden_id = NEW.garden_id
    WHERE plot_id = NEW.plot_id
      AND garden_id IS DISTINCT FROM NEW.garden_id;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_plot_ownership_sync_plot_garden_id ON public.plot_ownership;
CREATE TRIGGER trg_plot_ownership_sync_plot_garden_id
    AFTER INSERT OR UPDATE OF garden_id ON public.plot_ownership
    FOR EACH ROW
    EXECUTE FUNCTION public.sync_plot_garden_id_from_ownership();

ALTER TABLE public.plots
    ALTER COLUMN garden_id SET NOT NULL;

ALTER TABLE ONLY public.plots
    DROP CONSTRAINT IF EXISTS fk_plots_garden_id_gardens;

ALTER TABLE ONLY public.plots
    ADD CONSTRAINT fk_plots_garden_id_gardens
    FOREIGN KEY (garden_id)
    REFERENCES public.gardens(id)
    ON DELETE CASCADE
    DEFERRABLE
    NOT VALID;

ALTER TABLE ONLY public.plots
    VALIDATE CONSTRAINT fk_plots_garden_id_gardens;

DROP INDEX IF EXISTS public.idx_plots_row_col_unique;

CREATE INDEX IF NOT EXISTS idx_plots_garden
    ON public.plots USING btree (garden_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_plots_garden_grid_cell
    ON public.plots USING btree (garden_id, grid_row, grid_col)
    WHERE grid_row IS NOT NULL AND grid_col IS NOT NULL;
