-- DERVA audit log schema for Postgres.
-- Applied by the deploy as the schema owner. The gateway connects as a separate
-- login role that is a member of derva_audit_writer, so it can insert and read
-- events but never change or remove them.

CREATE TABLE IF NOT EXISTS audit_events (
    seq        INTEGER PRIMARY KEY,
    event_id   VARCHAR(32)  NOT NULL UNIQUE,
    request_id VARCHAR(64)  NOT NULL,
    ts         VARCHAR(40)  NOT NULL,
    type       VARCHAR(32)  NOT NULL,
    actor      VARCHAR(128) NOT NULL,
    payload    TEXT         NOT NULL,
    prev_hash  VARCHAR(64)  NOT NULL,
    hash       VARCHAR(64)  NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_request_id ON audit_events (request_id);

-- Reject changes even from the table owner. Only dropping the triggers (a
-- schema change, visible in its own right) would allow edits, and the hash
-- chain still exposes any edit made that way.
CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events;
CREATE TRIGGER audit_events_no_update
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();

DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events;
CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only();

-- Writer role: insert and read only.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'derva_audit_writer') THEN
        CREATE ROLE derva_audit_writer NOLOGIN;
    END IF;
END
$$;
REVOKE ALL ON audit_events FROM PUBLIC;
GRANT SELECT, INSERT ON audit_events TO derva_audit_writer;

-- Current state of each request, for queries and the review page.
CREATE OR REPLACE VIEW request_state AS
SELECT DISTINCT ON (request_id)
       request_id, type AS state, ts AS updated_at, seq
FROM audit_events
ORDER BY request_id, seq DESC;
GRANT SELECT ON request_state TO derva_audit_writer;

-- Retention is not implemented in v0.1. The planned approach: partition the
-- table by month, export each expired partition together with the anchor that
-- covers it, then detach and drop the partition. Row triggers do not fire on
-- partition drops, so no trigger has to be disabled, and the export plus anchor
-- keep the archived history verifiable.
