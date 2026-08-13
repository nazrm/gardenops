CREATE TABLE IF NOT EXISTS public.plant_external_references (
    plt_id text NOT NULL,
    source text NOT NULL,
    external_id text NOT NULL DEFAULT '',
    external_entity_id text NOT NULL DEFAULT '',
    canonical_url text NOT NULL DEFAULT '',
    matched_botanical_name text NOT NULL DEFAULT '',
    matched_common_name text NOT NULL DEFAULT '',
    match_type text NOT NULL DEFAULT 'none',
    verification_status text NOT NULL,
    verification_reason text NOT NULL DEFAULT '',
    metadata_json text NOT NULL DEFAULT '{}',
    verified_at_ms bigint NOT NULL,
    CONSTRAINT plant_external_references_pkey PRIMARY KEY (plt_id, source),
    CONSTRAINT fk_plant_external_references_plant
        FOREIGN KEY (plt_id) REFERENCES public.plants(plt_id) ON DELETE CASCADE DEFERRABLE,
    CONSTRAINT ck_plant_external_references_source
        CHECK (source ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
    CONSTRAINT ck_plant_external_references_match_type
        CHECK (match_type IN ('exact', 'synonym', 'manual', 'none')),
    CONSTRAINT ck_plant_external_references_status
        CHECK (verification_status IN ('verified', 'needs_review', 'not_found', 'error'))
);

CREATE INDEX IF NOT EXISTS idx_plant_external_references_status
    ON public.plant_external_references (source, verification_status, verified_at_ms);
