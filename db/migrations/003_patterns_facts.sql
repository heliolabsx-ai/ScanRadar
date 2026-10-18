-- 003_patterns_facts: frozen per-pattern facts (checks are derived from them at any minute). Idempotent.
ALTER TABLE patterns ADD COLUMN IF NOT EXISTS facts JSONB;
