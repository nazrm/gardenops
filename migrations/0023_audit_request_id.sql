ALTER TABLE public.audit_events
    ADD COLUMN IF NOT EXISTS request_id text DEFAULT ''::text NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_audit_events_request_id
    ON public.audit_events USING btree (request_id)
    WHERE request_id != '';
