-- Preserve request history while retiring pending requests from the old transport.
ALTER TABLE public.assistant_requests DROP CONSTRAINT IF EXISTS ck_assistant_requests_source_channel;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'assistant_requests'
                 AND column_name = 'source_room_id') THEN
        ALTER TABLE public.assistant_requests RENAME COLUMN source_room_id TO source_client_id;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'assistant_requests'
                 AND column_name = 'source_sender_id') THEN
        ALTER TABLE public.assistant_requests RENAME COLUMN source_sender_id TO source_actor_id;
    END IF;
END $$;
UPDATE public.assistant_requests
SET source_channel = 'mcp',
    state = CASE WHEN state IN ('processing', 'needs_input', 'proposal') THEN 'expired' ELSE state END
WHERE source_channel <> 'mcp';
ALTER TABLE public.assistant_requests ALTER COLUMN source_channel SET DEFAULT 'mcp';
ALTER TABLE public.assistant_requests ADD CONSTRAINT ck_assistant_requests_source_channel
    CHECK (source_channel = 'mcp');
UPDATE public.media_links SET target_type = 'mcp_capture' WHERE target_type = 'matrix_capture';
