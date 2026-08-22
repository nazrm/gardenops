-- Make plantable map units canonical plot locations without removing the
-- legacy unit table.  The deterministic plot id keeps this migration safe to
-- rerun while the runtime cutover happens in a later slice.

ALTER TABLE public.plots
    ADD COLUMN IF NOT EXISTS plot_kind text,
    ADD COLUMN IF NOT EXISTS display_name text,
    ADD COLUMN IF NOT EXISTS container_type text,
    ADD COLUMN IF NOT EXISTS parent_map_object_id bigint,
    ADD COLUMN IF NOT EXISTS environment text,
    ADD COLUMN IF NOT EXISTS archived_at_ms bigint;

UPDATE public.plots
SET plot_kind = CASE WHEN zone_code = 'I' THEN 'indoor' ELSE 'ground' END
WHERE plot_kind IS NULL;

UPDATE public.plots
SET environment = CASE WHEN plot_kind = 'indoor' THEN 'indoor' ELSE 'outdoor' END
WHERE environment IS NULL;

ALTER TABLE public.plots
    ALTER COLUMN plot_kind SET DEFAULT 'ground',
    ALTER COLUMN plot_kind SET NOT NULL,
    ALTER COLUMN environment SET DEFAULT 'outdoor',
    ALTER COLUMN environment SET NOT NULL;

-- Keep old indoor rows classified correctly while leaving any canonical
-- container row untouched on a rerun.
UPDATE public.plots
SET plot_kind = CASE WHEN zone_code = 'I' THEN 'indoor' ELSE 'ground' END,
    environment = CASE WHEN zone_code = 'I' THEN 'indoor' ELSE 'outdoor' END
WHERE plot_kind <> 'container'
  AND container_type IS NULL
  AND parent_map_object_id IS NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.garden_map_object_units u
        JOIN public.plots p
          ON p.plot_id = 'CONT-' || md5(u.public_id)
        WHERE p.plot_kind <> 'container'
           OR p.garden_id <> u.garden_id
           OR p.parent_map_object_id IS DISTINCT FROM u.map_object_id
    ) THEN
        RAISE EXCEPTION
            'Cannot migrate legacy map units: deterministic container plot id is already in use';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.garden_map_object_units u
        JOIN public.garden_map_objects o ON o.id = u.map_object_id
        JOIN public.gardens g ON g.id = u.garden_id
        WHERE COALESCE(
            o.created_by_user_id,
            g.owner_user_id,
            (SELECT MIN(gm.user_id)
             FROM public.garden_memberships gm
             WHERE gm.garden_id = u.garden_id),
            (SELECT MIN(au.id) FROM public.auth_users au)
        ) IS NULL
    ) THEN
        RAISE EXCEPTION
            'Cannot migrate legacy map units: no valid ownership user exists';
    END IF;
END
$$;

-- A unit public id is limited to 80 characters by imports, while plot ids are
-- limited to 40 characters by the existing plot API.  A stable hash avoids a
-- second mapping column and remains deterministic across reruns.
INSERT INTO public.plots (
    plot_id,
    garden_id,
    zone_code,
    zone_name,
    plot_number,
    grid_row,
    grid_col,
    plot_kind,
    display_name,
    container_type,
    parent_map_object_id,
    environment
)
SELECT
    'CONT-' || md5(u.public_id),
    u.garden_id,
    'C',
    'Containers',
    0,
    NULL,
    NULL,
    'container',
    COALESCE(NULLIF(btrim(u.name), ''), 'Container ' || u.public_id),
    CASE WHEN u.unit_type = 'shelf' THEN 'other' ELSE u.unit_type END,
    u.map_object_id,
    CASE WHEN o.object_type = 'greenhouse' THEN 'covered' ELSE 'outdoor' END
FROM public.garden_map_object_units u
JOIN public.garden_map_objects o ON o.id = u.map_object_id
ON CONFLICT (plot_id) DO NOTHING;

INSERT INTO public.plot_ownership (plot_id, owner_user_id, garden_id)
SELECT
    p.plot_id,
    COALESCE(
        o.created_by_user_id,
        g.owner_user_id,
        (SELECT MIN(gm.user_id)
         FROM public.garden_memberships gm
         WHERE gm.garden_id = u.garden_id),
        (SELECT MIN(au.id) FROM public.auth_users au)
    ),
    p.garden_id
FROM public.garden_map_object_units u
JOIN public.garden_map_objects o ON o.id = u.map_object_id
JOIN public.gardens g ON g.id = u.garden_id
JOIN public.plots p ON p.plot_id = 'CONT-' || md5(u.public_id)
WHERE p.plot_kind = 'container'
ON CONFLICT (plot_id) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_plots_plot_kind'
          AND conrelid = 'public.plots'::regclass
    ) THEN
        ALTER TABLE public.plots
            ADD CONSTRAINT ck_plots_plot_kind
            CHECK (plot_kind IN ('ground', 'indoor', 'container'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_plots_environment'
          AND conrelid = 'public.plots'::regclass
    ) THEN
        ALTER TABLE public.plots
            ADD CONSTRAINT ck_plots_environment
            CHECK (environment IN ('outdoor', 'covered', 'indoor'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_plots_container_subtype'
          AND conrelid = 'public.plots'::regclass
    ) THEN
        ALTER TABLE public.plots
            ADD CONSTRAINT ck_plots_container_subtype
            CHECK (
                (
                    plot_kind = 'container'
                    AND container_type IN ('pot', 'planter', 'raised_bed', 'other')
                    AND grid_row IS NULL
                    AND grid_col IS NULL
                    AND btrim(COALESCE(display_name, '')) <> ''
                )
                OR (
                    plot_kind IN ('ground', 'indoor')
                    AND container_type IS NULL
                    AND parent_map_object_id IS NULL
                )
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_plots_parent_map_object_garden'
          AND conrelid = 'public.plots'::regclass
    ) THEN
        ALTER TABLE public.plots
            ADD CONSTRAINT fk_plots_parent_map_object_garden
            FOREIGN KEY (parent_map_object_id, garden_id)
            REFERENCES public.garden_map_objects (id, garden_id)
            ON DELETE SET NULL (parent_map_object_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_plots_active_containers
    ON public.plots USING btree (garden_id, parent_map_object_id, plot_id)
    WHERE plot_kind = 'container' AND archived_at_ms IS NULL;
