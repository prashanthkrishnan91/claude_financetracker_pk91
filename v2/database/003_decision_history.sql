-- Decision history table: records generated plans and optional user overrides.

create table if not exists decision_history (
    id             uuid primary key default gen_random_uuid(),
    user_id        uuid not null,
    decision_type  text not null,
    input_snapshot jsonb,
    input_params   jsonb,
    generated_actions jsonb,
    final_actions  jsonb,
    status         text not null default 'generated',
    created_at     timestamptz not null default now()
);

create index if not exists idx_decision_history_user_created
    on decision_history (user_id, created_at desc);
