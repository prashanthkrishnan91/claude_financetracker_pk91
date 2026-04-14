Purpose: Encodes expert DB management logic.

SKILL: SUPABASE_FINANCE_DB

AUTH: Use app_metadata for investor tiers; NEVER user_metadata.

SCHEMA: Always generate migrations via SQL; never manual dashboard edits.

TYPES: Use the Supabase Connector to generate types after schema changes.

VIEWS: All portfolio aggregation views must use security_invoker = true.
