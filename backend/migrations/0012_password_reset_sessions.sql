-- 0012 PASSWORD RESET TOKENS AND NAMED SESSIONS
--
-- password_reset_tokens: one-time, time-limited tokens for "forgot password"
--   (sent by e-mail) and for administrator-issued reset links. Only a
--   SHA-256 hash is stored. Like sessions, the table is read before the
--   user's organization is known, so it is not under row level security;
--   every query filters by the token hash or the user id.
-- sessions.client_name: which app / device created the session (for the
--   "signed-in devices" list), e.g. "Web browser", "Android - Ward A tablet".

CREATE TABLE password_reset_tokens (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id),
    token_hash char(64) NOT NULL UNIQUE,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp without time zone NOT NULL,
    used_at timestamp without time zone,
    created_by integer REFERENCES users(id),
    purpose varchar(20) NOT NULL DEFAULT 'FORGOT' CHECK (purpose IN ('FORGOT', 'ADMIN_RESET'))
);
CREATE INDEX password_reset_tokens_user_idx ON password_reset_tokens (user_id);

ALTER TABLE sessions ADD COLUMN client_name varchar(100);
