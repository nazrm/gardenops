CREATE TABLE IF NOT EXISTS public.assistant_requests (
    public_id text PRIMARY KEY,
    garden_id bigint NOT NULL,
    actor_user_id bigint NOT NULL,
    source_channel text NOT NULL DEFAULT 'matrix',
    source_room_id text NOT NULL,
    source_event_id text NOT NULL,
    source_sender_id text NOT NULL,
    request_kind text NOT NULL,
    state text NOT NULL,
    input_text text NOT NULL DEFAULT '',
    capture_asset_id text,
    payload_json text NOT NULL DEFAULT '{}',
    result_json text NOT NULL DEFAULT '{}',
    error_detail text NOT NULL DEFAULT '',
    created_at_ms bigint NOT NULL,
    updated_at_ms bigint NOT NULL,
    expires_at_ms bigint NOT NULL,
    applied_at_ms bigint,
    last_source_event_id text NOT NULL DEFAULT '',
    CONSTRAINT ux_assistant_requests_source_event
        UNIQUE (source_channel, source_room_id, source_event_id),
    CONSTRAINT fk_assistant_requests_garden
        FOREIGN KEY (garden_id) REFERENCES public.gardens(id) ON DELETE CASCADE DEFERRABLE,
    CONSTRAINT fk_assistant_requests_actor
        FOREIGN KEY (actor_user_id) REFERENCES public.auth_users(id) ON DELETE CASCADE DEFERRABLE,
    CONSTRAINT fk_assistant_requests_capture
        FOREIGN KEY (capture_asset_id) REFERENCES public.media_assets(asset_id)
        ON DELETE SET NULL DEFERRABLE,
    CONSTRAINT ck_assistant_requests_source_channel
        CHECK (source_channel = 'matrix'),
    CONSTRAINT ck_assistant_requests_kind
        CHECK (request_kind IN (
            'question', 'journal', 'harvest', 'issue', 'task_completion', 'unknown'
        )),
    CONSTRAINT ck_assistant_requests_state
        CHECK (state IN (
            'processing', 'needs_input', 'proposal', 'answered', 'applied',
            'cancelled', 'expired', 'failed'
        ))
);

CREATE INDEX IF NOT EXISTS idx_assistant_requests_state_expiry
    ON public.assistant_requests (state, expires_at_ms);

CREATE INDEX IF NOT EXISTS idx_assistant_requests_garden_created
    ON public.assistant_requests (garden_id, created_at_ms);
