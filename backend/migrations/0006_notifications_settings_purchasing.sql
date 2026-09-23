-- 0006 NOTIFICATIONS, SETTINGS AND PURCHASING ACCOUNTABILITY

-- Organisation settings (expiry thresholds etc.) that must be configurable
-- rather than hard-coded.
CREATE TABLE app_settings (
    key varchar(100) PRIMARY KEY,
    value jsonb NOT NULL,
    description text,
    updated_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by integer REFERENCES users (id)
);

INSERT INTO app_settings (key, value, description) VALUES
    ('expiry.critical_days', '30', 'Batches expiring within this many days are CRITICAL'),
    ('expiry.urgent_days', '90', 'Batches expiring within this many days are URGENT'),
    ('expiry.approaching_days', '180', 'Batches expiring within this many days are APPROACHING EXPIRY'),
    ('stock.slow_moving_units_90d', '10', 'Medicines dispensing this many units or fewer in 90 days are SLOW-MOVING'),
    ('reorder.lead_time_days', '14', 'Typical supplier lead time used for reorder recommendations'),
    ('reorder.cover_days', '30', 'Days of stock a reorder should cover after it arrives'),
    ('currency.symbol', '"₵"', 'Currency symbol shown in the interface and reports');


-- In-app notifications. dedupe_key makes generation idempotent (the same
-- expiring batch never produces two open notifications); resolved_at is set
-- automatically when the condition clears.
CREATE TABLE notifications (
    id bigserial PRIMARY KEY,
    category varchar(30) NOT NULL,
    severity varchar(10) NOT NULL,
    title varchar(200) NOT NULL,
    message text NOT NULL,
    entity_type varchar(50),
    entity_id varchar(64),
    dedupe_key varchar(200) NOT NULL UNIQUE,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at timestamp without time zone,
    CONSTRAINT notifications_category_check CHECK (
        category IN ('EXPIRY', 'LOW_STOCK', 'PURCHASING', 'RECEIVING', 'INVENTORY', 'SYSTEM')
    ),
    CONSTRAINT notifications_severity_check CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL'))
);

CREATE INDEX idx_notifications_open ON notifications (resolved_at, created_at DESC);

-- Read state is per user.
CREATE TABLE notification_reads (
    notification_id bigint NOT NULL REFERENCES notifications (id) ON DELETE CASCADE,
    user_id integer NOT NULL REFERENCES users (id),
    read_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (notification_id, user_id)
);


-- Who created an order / received stock. purchase_receipts.received_by
-- (free text) is kept for existing rows and for the name printed on the
-- delivery note.
ALTER TABLE purchase_orders
    ADD COLUMN created_by integer REFERENCES users (id);

ALTER TABLE purchase_receipts
    ADD COLUMN received_by_user_id integer REFERENCES users (id);
