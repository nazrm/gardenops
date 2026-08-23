-- Give each contained plot a stable one-cell position inside its parent area.

ALTER TABLE public.plots
    ADD COLUMN IF NOT EXISTS container_position_x integer,
    ADD COLUMN IF NOT EXISTS container_position_y integer;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.garden_map_objects o
        JOIN LATERAL (
            SELECT COUNT(*) AS container_count
            FROM public.plots p
            WHERE p.parent_map_object_id = o.id
              AND p.garden_id = o.garden_id
              AND p.plot_kind = 'container'
              AND p.archived_at_ms IS NULL
        ) counts ON TRUE
        WHERE counts.container_count >
              ((o.geometry_json::jsonb ->> 'width')::integer
               * (o.geometry_json::jsonb ->> 'height')::integer)
    ) THEN
        RAISE EXCEPTION
            'Cannot position contained plots: an area has more containers than cells';
    END IF;
END
$$;

WITH ranked AS (
    SELECT
        p.plot_id,
        (o.geometry_json::jsonb ->> 'width')::integer AS area_width,
        ROW_NUMBER() OVER (
            PARTITION BY p.parent_map_object_id
            ORDER BY p.display_name, p.plot_id
        ) - 1 AS position_index
    FROM public.plots p
    JOIN public.garden_map_objects o
      ON o.id = p.parent_map_object_id
     AND o.garden_id = p.garden_id
    WHERE p.plot_kind = 'container'
      AND p.parent_map_object_id IS NOT NULL
      AND p.archived_at_ms IS NULL
)
UPDATE public.plots p
SET container_position_x = MOD(ranked.position_index, ranked.area_width),
    container_position_y = ranked.position_index / ranked.area_width
FROM ranked
WHERE p.plot_id = ranked.plot_id
  AND (
      p.container_position_x IS NULL
      OR p.container_position_y IS NULL
  );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_plots_container_position_pair'
          AND conrelid = 'public.plots'::regclass
    ) THEN
        ALTER TABLE public.plots
            ADD CONSTRAINT ck_plots_container_position_pair
            CHECK (
                (
                    plot_kind = 'container'
                    AND (container_position_x IS NULL) = (container_position_y IS NULL)
                    AND (
                        container_position_x IS NULL
                        OR (container_position_x >= 0 AND container_position_y >= 0)
                    )
                )
                OR (
                    plot_kind IN ('ground', 'indoor')
                    AND container_position_x IS NULL
                    AND container_position_y IS NULL
                )
            );
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_plots_active_container_position
    ON public.plots USING btree (
        garden_id,
        parent_map_object_id,
        container_position_x,
        container_position_y
    )
    WHERE plot_kind = 'container'
      AND parent_map_object_id IS NOT NULL
      AND container_position_x IS NOT NULL
      AND container_position_y IS NOT NULL
      AND archived_at_ms IS NULL;
