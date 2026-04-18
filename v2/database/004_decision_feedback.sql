-- Additive migration: add user_feedback column to decision_history.
-- user_feedback stores the structured override payload from the user.
-- Schema: { "type": "accept"|"modify"|"reject", "modified_actions": [...], "notes": optional }

alter table decision_history
    add column if not exists user_feedback jsonb;
