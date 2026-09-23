-- 0011 NOTIFICATION DELIVERY (EMAIL / SMS) AND SCHEDULED REPORTS
--
-- In-app notifications stay the source of truth. Users can opt in to
-- receive notifications at or above a chosen severity by e-mail and/or SMS.
-- Outgoing messages go through an OUTBOX (notification_deliveries): they are
-- written in the same transaction as the event and sent by a background
-- worker with retries, so a mail-server outage never blocks stock work and
-- nothing is lost. Scheduled reports are e-mailed through the same outbox.

ALTER TABLE users
    ADD COLUMN phone varchar(50),
    ADD COLUMN notify_email boolean NOT NULL DEFAULT false,
    ADD COLUMN notify_sms boolean NOT NULL DEFAULT false,
    ADD COLUMN notify_min_severity varchar(10) NOT NULL DEFAULT 'CRITICAL'
        CHECK (notify_min_severity IN ('INFO', 'WARNING', 'CRITICAL'));

CREATE TABLE scheduled_reports (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    name varchar(150) NOT NULL,
    report_key varchar(50) NOT NULL,
    format varchar(10) NOT NULL CHECK (format IN ('csv', 'xlsx', 'pdf')),
    frequency varchar(10) NOT NULL CHECK (frequency IN ('DAILY', 'WEEKLY', 'MONTHLY')),
    day_of_week smallint CHECK (day_of_week BETWEEN 0 AND 6),       -- 0 = Monday
    day_of_month smallint CHECK (day_of_month BETWEEN 1 AND 28),
    hour smallint NOT NULL DEFAULT 7 CHECK (hour BETWEEN 0 AND 23),
    recipients text[] NOT NULL,
    filters jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_active boolean NOT NULL DEFAULT true,
    next_run_at timestamp without time zone NOT NULL,
    last_run_at timestamp without time zone,
    last_status text,
    created_by integer REFERENCES users(id),
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT scheduled_reports_recipients CHECK (cardinality(recipients) BETWEEN 1 AND 20)
);
CREATE INDEX scheduled_reports_due_idx ON scheduled_reports (next_run_at) WHERE is_active;

CREATE TABLE notification_deliveries (
    id bigserial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    notification_id integer REFERENCES notifications(id) ON DELETE SET NULL,
    scheduled_report_id integer REFERENCES scheduled_reports(id) ON DELETE SET NULL,
    user_id integer REFERENCES users(id),
    channel varchar(10) NOT NULL CHECK (channel IN ('EMAIL', 'SMS')),
    recipient varchar(200) NOT NULL,
    subject varchar(300),
    body text NOT NULL,
    attachment_name varchar(200),
    attachment_type varchar(100),
    attachment bytea,
    status varchar(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SENT', 'FAILED', 'SKIPPED')),
    attempts smallint NOT NULL DEFAULT 0,
    last_error text,
    next_attempt_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at timestamp without time zone
);
CREATE INDEX notification_deliveries_pending_idx ON notification_deliveries (next_attempt_at) WHERE status = 'PENDING';
CREATE INDEX notification_deliveries_created_idx ON notification_deliveries (organization_id, created_at DESC);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['scheduled_reports', 'notification_deliveries'] LOOP
        EXECUTE format('CREATE INDEX %I ON %I (organization_id)', t || '_org_idx', t);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) '
                       'WITH CHECK (organization_id = current_org())', t);
    END LOOP;
END $$;
