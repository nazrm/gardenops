DROP INDEX IF EXISTS public.ux_audit_events_request_id;

-- Keep the schema-signature index name while allowing a client correlation ID
-- to appear on more than one server-identified audit row.
CREATE UNIQUE INDEX ux_audit_events_request_id
    ON public.audit_events USING btree (request_id, id)
    WHERE request_id != '';
