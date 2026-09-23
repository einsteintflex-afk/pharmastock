-- 0004 USERS, SESSIONS AND AUDIT TRAIL
--
-- Roles are a fixed, code-defined set (permissions are mapped to roles in
-- backend/permissions.py), so a CHECK constraint is used instead of separate
-- roles / permissions tables.

CREATE TABLE users (
    id serial PRIMARY KEY,
    username varchar(50) NOT NULL,
    full_name varchar(150) NOT NULL,
    email varchar(150),
    role varchar(30) NOT NULL,
    password_hash text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    must_change_password boolean NOT NULL DEFAULT false,
    failed_login_count integer NOT NULL DEFAULT 0,
    locked_until timestamp without time zone,
    last_login_at timestamp without time zone,
    password_changed_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT users_username_format CHECK (username ~ '^[A-Za-z0-9._-]{3,50}$'),
    CONSTRAINT users_full_name_not_blank CHECK (btrim(full_name) <> ''),
    CONSTRAINT users_role_check CHECK (
        role IN ('ADMINISTRATOR', 'PHARMACIST', 'PHARMACY_TECHNICIAN', 'STOREKEEPER', 'MANAGER', 'VIEWER')
    ),
    CONSTRAINT users_failed_login_non_negative CHECK (failed_login_count >= 0)
);

CREATE UNIQUE INDEX users_username_unique ON users (lower(username));


-- Server-side sessions: only a SHA-256 hash of the token is stored, so a
-- database leak does not expose usable tokens. Logout revokes the row.
CREATE TABLE sessions (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users (id),
    token_hash char(64) NOT NULL UNIQUE,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp without time zone NOT NULL,
    revoked_at timestamp without time zone,
    ip_address varchar(64),
    user_agent varchar(255)
);

CREATE INDEX idx_sessions_user ON sessions (user_id);


CREATE TABLE audit_log (
    id bigserial PRIMARY KEY,
    occurred_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_id integer REFERENCES users (id),
    username varchar(50),
    action varchar(50) NOT NULL,
    entity_type varchar(50) NOT NULL,
    entity_id varchar(64),
    old_value jsonb,
    new_value jsonb,
    ip_address varchar(64)
);

CREATE INDEX idx_audit_log_occurred ON audit_log (occurred_at DESC);
CREATE INDEX idx_audit_log_entity ON audit_log (entity_type, entity_id);
CREATE INDEX idx_audit_log_user ON audit_log (user_id);


-- The audit trail is append-only.
CREATE FUNCTION audit_log_block_changes() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$;

CREATE TRIGGER audit_log_append_only
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_block_changes();
