ALTER TABLE public.garden_journal_entries
    ADD COLUMN IF NOT EXISTS public_id text;

ALTER TABLE public.garden_issues
    ADD COLUMN IF NOT EXISTS public_id text;

ALTER TABLE public.harvest_entries
    ADD COLUMN IF NOT EXISTS public_id text;

UPDATE public.garden_journal_entries
SET public_id = 'jrn_' || substr(md5(random()::text || clock_timestamp()::text || id::text), 1, 20)
WHERE public_id IS NULL;

UPDATE public.garden_issues
SET public_id = 'iss_' || substr(md5(random()::text || clock_timestamp()::text || id::text), 1, 20)
WHERE public_id IS NULL;

UPDATE public.harvest_entries
SET public_id = 'hrv_' || substr(md5(random()::text || clock_timestamp()::text || id::text), 1, 20)
WHERE public_id IS NULL;

ALTER TABLE public.garden_journal_entries
    ALTER COLUMN public_id SET NOT NULL;

ALTER TABLE public.garden_issues
    ALTER COLUMN public_id SET NOT NULL;

ALTER TABLE public.harvest_entries
    ALTER COLUMN public_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_garden_journal_entries_public_id
    ON public.garden_journal_entries USING btree (public_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_garden_issues_public_id
    ON public.garden_issues USING btree (public_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_harvest_entries_public_id
    ON public.harvest_entries USING btree (public_id);

UPDATE public.media_links AS l
SET target_id = e.public_id
FROM public.garden_journal_entries AS e
WHERE l.target_type = 'journal_entry'
  AND l.target_id ~ '^[0-9]+$'
  AND e.id = l.target_id::bigint;

UPDATE public.media_links AS l
SET target_id = i.public_id
FROM public.garden_issues AS i
WHERE l.target_type = 'issue'
  AND l.target_id ~ '^[0-9]+$'
  AND i.id = l.target_id::bigint;

UPDATE public.media_links AS l
SET target_id = h.public_id
FROM public.harvest_entries AS h
WHERE l.target_type = 'harvest_entry'
  AND l.target_id ~ '^[0-9]+$'
  AND h.id = l.target_id::bigint;

UPDATE public.garden_journal_entries AS e
SET metadata_json = jsonb_set(
    COALESCE(NULLIF(e.metadata_json, '')::jsonb, '{}'::jsonb),
    '{issue_id}',
    to_jsonb(i.public_id),
    true
)::text
FROM public.garden_issues AS i
WHERE COALESCE(NULLIF(e.metadata_json, ''), '') != ''
  AND (e.metadata_json::jsonb ->> 'source') = 'auto:issue'
  AND (e.metadata_json::jsonb ->> 'issue_id') = i.id::text;

UPDATE public.garden_journal_entries AS e
SET metadata_json = jsonb_set(
    COALESCE(NULLIF(e.metadata_json, '')::jsonb, '{}'::jsonb),
    '{linked_harvest_entry_id}',
    to_jsonb(h.public_id),
    true
)::text
FROM public.harvest_entries AS h
WHERE COALESCE(NULLIF(e.metadata_json, ''), '') != ''
  AND (e.metadata_json::jsonb ->> 'source') = 'auto:harvest'
  AND (e.metadata_json::jsonb ->> 'linked_harvest_entry_id') = h.id::text;

UPDATE public.harvest_entries AS h
SET metadata_json = jsonb_set(
    COALESCE(NULLIF(h.metadata_json, '')::jsonb, '{}'::jsonb),
    '{journal_entry_id}',
    to_jsonb(e.public_id),
    true
)::text
FROM public.garden_journal_entries AS e
WHERE COALESCE(NULLIF(h.metadata_json, ''), '') != ''
  AND (h.metadata_json::jsonb ->> 'journal_entry_id') = e.id::text;

UPDATE public.garden_tasks AS t
SET rule_source = 'auto:issue_followup:' || i.public_id,
    description = format(
        'Auto-generated from issue %s. Review and update status.',
        i.public_id
    ),
    metadata_json = jsonb_set(
        COALESCE(NULLIF(t.metadata_json, '')::jsonb, '{}'::jsonb),
        '{description_no}',
        to_jsonb(
            format(
                'Automatisk opprettet fra sak %s. Gjennomgå og oppdater status.',
                i.public_id
            )
        ),
        true
    )::text
FROM public.garden_issues AS i
WHERE t.rule_source = 'auto:issue_followup:' || i.id::text;

UPDATE public.garden_tasks AS t
SET rule_source = 'auto:escalation:' || i.public_id || ':' || split_part(t.rule_source, ':', 4),
    description = format(
        'Issue %s passed follow-up date %s. Needs immediate attention.',
        i.public_id,
        split_part(t.rule_source, ':', 4)
    ),
    metadata_json = jsonb_set(
        COALESCE(NULLIF(t.metadata_json, '')::jsonb, '{}'::jsonb),
        '{description_no}',
        to_jsonb(
            format(
                'Sak %s passerte oppfølgingsdato %s. Trenger umiddelbar oppmerksomhet.',
                i.public_id,
                split_part(t.rule_source, ':', 4)
            )
        ),
        true
    )::text
FROM public.garden_issues AS i
WHERE split_part(t.rule_source, ':', 1) = 'auto'
  AND split_part(t.rule_source, ':', 2) = 'escalation'
  AND split_part(t.rule_source, ':', 3) = i.id::text;
