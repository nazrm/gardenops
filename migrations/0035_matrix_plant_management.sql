ALTER TABLE public.assistant_requests
    DROP CONSTRAINT IF EXISTS ck_assistant_requests_kind;

ALTER TABLE public.assistant_requests
    ADD CONSTRAINT ck_assistant_requests_kind
    CHECK (request_kind IN (
        'question', 'journal', 'harvest', 'issue', 'task_completion',
        'plant_create', 'plant_assign', 'plant_move', 'plant_delete', 'unknown'
    ));
