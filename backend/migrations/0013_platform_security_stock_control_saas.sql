-- 0013 PLATFORM SECURITY, STOCK CONTROL, RECEIPTS, MESSAGING AND SAAS
--
-- Additive only: no table is dropped, no existing row is changed except where
-- a new column needs a value (documented per statement).
--
--  * roles: Organization Owner, Inventory Officer, Purchasing Officer,
--    Cashier, Auditor (existing roles unchanged)
--  * MFA (TOTP) for users, MFA challenges, step-up time on sessions
--  * platform_audit (MedCart Tech operations, separate from company audit)
--  * security_events (failed sign-ins, authorization failures, probes)
--  * idempotency_keys for critical operations
--  * stock adjustments with reason codes and approval; stock counts
--  * purchase order submit / approve / expected delivery
--  * receipts: discount, tax, subtotal, digital receipt token, consent
--  * organization files (logo), onboarding, platform settings
--  * plans catalogue in the database, subscriptions, payments, messaging
--    packages and credit ledger, per-organization messaging providers,
--    WhatsApp channel, delivery status, consent, provider routing

-- ---------------------------------------------------------------- roles
ALTER TABLE users DROP CONSTRAINT users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN (
    'OWNER', 'ADMINISTRATOR', 'MANAGER', 'PHARMACIST', 'PHARMACY_TECHNICIAN', 'INVENTORY_OFFICER',
    'STOREKEEPER', 'PURCHASING_OFFICER', 'CASHIER', 'AUDITOR', 'VIEWER'));

-- ---------------------------------------------------------------- MFA
ALTER TABLE users
    ADD COLUMN mfa_secret_encrypted text,
    ADD COLUMN mfa_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN mfa_enabled_at timestamp without time zone,
    ADD COLUMN mfa_recovery_codes text[],          -- SHA-256 hashes, removed when used
    ADD COLUMN mfa_last_counter bigint;            -- last accepted TOTP step (replay protection)

ALTER TABLE sessions
    ADD COLUMN mfa_verified_at timestamp without time zone,
    ADD COLUMN privileged boolean NOT NULL DEFAULT false;

-- Pending second factor after a correct password (no session yet).
CREATE TABLE mfa_challenges (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id),
    token_hash char(64) NOT NULL UNIQUE,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp without time zone NOT NULL,
    attempts smallint NOT NULL DEFAULT 0,
    used_at timestamp without time zone,
    client_name varchar(100),
    ip_address varchar(64)
);

ALTER TABLE password_reset_tokens DROP CONSTRAINT password_reset_tokens_purpose_check;
ALTER TABLE password_reset_tokens ADD CONSTRAINT password_reset_tokens_purpose_check
    CHECK (purpose IN ('FORGOT', 'ADMIN_RESET', 'PLATFORM_RECOVERY'));
ALTER TABLE password_reset_tokens ADD COLUMN revoked_at timestamp without time zone;

-- ---------------------------------------------------------------- platform audit
-- MedCart Tech platform operations. Not under row level security: only
-- platform endpoints (MFA-protected platform administrators) read it.
CREATE TABLE platform_audit (
    id bigserial PRIMARY KEY,
    occurred_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor_user_id integer REFERENCES users(id),
    actor_username varchar(50),
    action varchar(60) NOT NULL,
    target_organization_id integer REFERENCES organizations(id),
    entity_type varchar(50),
    entity_id varchar(64),
    details jsonb,
    reason text,
    result varchar(20) NOT NULL DEFAULT 'SUCCESS',
    ip_address varchar(64),
    request_id varchar(32),
    session_id bigint
);
CREATE INDEX platform_audit_time_idx ON platform_audit (occurred_at DESC);
CREATE INDEX platform_audit_org_idx ON platform_audit (target_organization_id, occurred_at DESC);

CREATE FUNCTION platform_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'platform_audit is append-only';
END $$;
CREATE TRIGGER platform_audit_no_update BEFORE UPDATE OR DELETE ON platform_audit
    FOR EACH ROW EXECUTE FUNCTION platform_audit_append_only();

-- ---------------------------------------------------------------- security events
-- Written by the request middleware and sign-in code. Organization ids are
-- recorded when known; queries always filter explicitly (no RLS: the platform
-- security view aggregates across organizations without reading their data).
CREATE TABLE security_events (
    id bigserial PRIMARY KEY,
    occurred_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    organization_id integer REFERENCES organizations(id),
    user_id integer REFERENCES users(id),
    username varchar(50),
    event_type varchar(30) NOT NULL,
    method varchar(10),
    path varchar(200),
    status_code smallint,
    ip_address varchar(64),
    request_id varchar(32),
    detail text
);
CREATE INDEX security_events_time_idx ON security_events (occurred_at DESC);
CREATE INDEX security_events_type_idx ON security_events (event_type, occurred_at DESC);
CREATE INDEX security_events_org_idx ON security_events (organization_id, occurred_at DESC);

-- ---------------------------------------------------------------- idempotency
CREATE TABLE idempotency_keys (
    id bigserial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    user_id integer NOT NULL REFERENCES users(id),
    key varchar(100) NOT NULL,
    endpoint varchar(200) NOT NULL,
    request_hash char(64) NOT NULL,
    response jsonb,
    status_code smallint,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT idempotency_keys_unique UNIQUE (organization_id, user_id, key)
);

-- ---------------------------------------------------------------- stock adjustments
CREATE TABLE stock_adjustments (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    adjustment_number varchar(30) NOT NULL,
    batch_id integer NOT NULL REFERENCES batches(id),
    medicine_id integer NOT NULL REFERENCES medicines(id),
    location_id integer NOT NULL REFERENCES locations(id),
    previous_quantity integer NOT NULL,
    adjustment_quantity integer NOT NULL CHECK (adjustment_quantity <> 0),
    new_quantity integer,
    unit_cost numeric(12, 2),
    reason_code varchar(30) NOT NULL CHECK (reason_code IN (
        'PHYSICAL_COUNT', 'DAMAGE', 'BREAKAGE', 'EXPIRY_WRITE_OFF', 'THEFT_LOSS', 'SUPPLIER_CORRECTION',
        'DATA_ENTRY_CORRECTION', 'CUSTOMER_RETURN', 'TRANSFER_CORRECTION', 'OPENING_BALANCE', 'OTHER')),
    notes text,
    status varchar(20) NOT NULL CHECK (status IN ('PENDING_APPROVAL', 'POSTED', 'REJECTED', 'CANCELLED')),
    requested_by integer NOT NULL REFERENCES users(id),
    requested_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_by integer REFERENCES users(id),
    decided_at timestamp without time zone,
    decision_note text,
    movement_id integer REFERENCES stock_movements(id),
    stock_count_id integer,
    session_id bigint,
    client_name varchar(100),
    request_id varchar(32),
    CONSTRAINT stock_adjustments_number_unique UNIQUE (organization_id, adjustment_number)
);
CREATE INDEX stock_adjustments_status_idx ON stock_adjustments (organization_id, status, requested_at DESC);
CREATE INDEX stock_adjustments_batch_idx ON stock_adjustments (batch_id);

-- ---------------------------------------------------------------- stock counts
CREATE TABLE stock_counts (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    count_number varchar(30) NOT NULL,
    location_id integer NOT NULL REFERENCES locations(id),
    name varchar(150),
    notes text,
    status varchar(20) NOT NULL DEFAULT 'IN_PROGRESS'
        CHECK (status IN ('IN_PROGRESS', 'SUBMITTED', 'POSTED', 'CANCELLED')),
    created_by integer NOT NULL REFERENCES users(id),
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    submitted_by integer REFERENCES users(id),
    submitted_at timestamp without time zone,
    posted_by integer REFERENCES users(id),
    posted_at timestamp without time zone,
    cancelled_reason text,
    CONSTRAINT stock_counts_number_unique UNIQUE (organization_id, count_number)
);
CREATE TABLE stock_count_lines (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    stock_count_id integer NOT NULL REFERENCES stock_counts(id) ON DELETE CASCADE,
    batch_id integer NOT NULL REFERENCES batches(id),
    medicine_id integer NOT NULL REFERENCES medicines(id),
    system_quantity integer NOT NULL,       -- recorded quantity at the moment of counting
    counted_quantity integer NOT NULL CHECK (counted_quantity >= 0),
    reason_code varchar(30),
    notes text,
    counted_by integer NOT NULL REFERENCES users(id),
    counted_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    adjustment_id integer REFERENCES stock_adjustments(id),
    CONSTRAINT stock_count_lines_batch_unique UNIQUE (stock_count_id, batch_id)
);
CREATE INDEX stock_count_lines_count_idx ON stock_count_lines (stock_count_id);
ALTER TABLE stock_adjustments ADD CONSTRAINT stock_adjustments_count_fk
    FOREIGN KEY (stock_count_id) REFERENCES stock_counts(id);

-- ---------------------------------------------------------------- purchasing
ALTER TABLE purchase_orders DROP CONSTRAINT purchase_order_status_check;
ALTER TABLE purchase_orders ADD CONSTRAINT purchase_order_status_check CHECK (status IN (
    'DRAFT', 'SUBMITTED', 'APPROVED', 'ORDERED', 'PARTIALLY_RECEIVED', 'RECEIVED', 'CANCELLED'));
ALTER TABLE purchase_orders
    ADD COLUMN expected_delivery_date date,
    ADD COLUMN submitted_by integer REFERENCES users(id),
    ADD COLUMN submitted_at timestamp without time zone,
    ADD COLUMN approved_by integer REFERENCES users(id),
    ADD COLUMN approved_at timestamp without time zone,
    ADD COLUMN ordered_at timestamp without time zone;

-- ---------------------------------------------------------------- receipts
ALTER TABLE dispensations
    ADD COLUMN subtotal_amount numeric(12, 2),
    ADD COLUMN discount_amount numeric(12, 2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    ADD COLUMN tax_amount numeric(12, 2) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
    ADD COLUMN tax_label varchar(30),
    ADD COLUMN receipt_token char(32),
    ADD COLUMN customer_email varchar(150),
    ADD COLUMN consent_whatsapp boolean NOT NULL DEFAULT false,
    ADD COLUMN consent_sms boolean NOT NULL DEFAULT false,
    ADD COLUMN consent_email boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX dispensations_receipt_token_idx ON dispensations (receipt_token) WHERE receipt_token IS NOT NULL;

-- Public digital-receipt links resolve here before the organization is known.
CREATE TABLE receipt_links (
    token char(32) PRIMARY KEY,
    organization_id integer NOT NULL REFERENCES organizations(id),
    dispensation_id integer NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp without time zone NOT NULL
);

-- ---------------------------------------------------------------- organization files, onboarding
CREATE TABLE organization_files (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    kind varchar(20) NOT NULL CHECK (kind IN ('LOGO')),
    content bytea NOT NULL,
    content_type varchar(50) NOT NULL,
    size_bytes integer NOT NULL,
    sha256 char(64) NOT NULL,
    width integer,
    height integer,
    uploaded_by integer REFERENCES users(id),
    uploaded_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT organization_files_kind_unique UNIQUE (organization_id, kind)
);

ALTER TABLE organizations
    ADD COLUMN onboarding_completed_at timestamp without time zone,
    ADD COLUMN messaging_mode varchar(20) NOT NULL DEFAULT 'DISABLED'
        CHECK (messaging_mode IN ('DISABLED', 'PLATFORM_CREDITS', 'OWN_PROVIDER')),
    ADD COLUMN subscription_status varchar(20);
-- Existing organizations are already set up.
UPDATE organizations SET onboarding_completed_at = created_at;

CREATE TABLE platform_settings (
    key varchar(100) PRIMARY KEY,
    value jsonb NOT NULL,
    description text,
    updated_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by integer REFERENCES users(id)
);
INSERT INTO platform_settings (key, value, description) VALUES
    ('branding.powered_by', '"Powered by MedCart Tech"', 'Attribution shown on receipts, reports and the application'),
    ('branding.show_powered_by', 'true', 'Show the MedCart Tech attribution on customer-facing documents');

-- ---------------------------------------------------------------- plans, subscriptions, payments
CREATE TABLE plans (
    code varchar(30) PRIMARY KEY,
    label varchar(60) NOT NULL,
    description text,
    limits jsonb NOT NULL DEFAULT '{}'::jsonb,
    features text[] NOT NULL DEFAULT '{}',
    price_monthly numeric(12, 2),        -- set by MedCart Tech; NULL = not sold online
    price_annual numeric(12, 2),
    currency varchar(3),
    is_active boolean NOT NULL DEFAULT true,
    sort_order smallint NOT NULL DEFAULT 0,
    updated_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by integer REFERENCES users(id)
);
INSERT INTO plans (code, label, description, limits, features, sort_order) VALUES
    ('BASIC', 'Basic', 'Single pharmacy: inventory, expiry, FEFO dispensing, purchasing, barcode, stock counts, reports',
     '{"max_users": 5, "max_locations": 1, "max_medicines": 2000, "max_scheduled_reports": 0}',
     ARRAY['core', 'dispensing', 'purchasing', 'reports', 'notifications', 'barcode', 'stock_count', 'email_receipts'], 1),
    ('PROFESSIONAL', 'Professional', 'Multiple locations, transfers, advanced analytics, AI assistant, scheduled reports, purchase approvals, WhatsApp / SMS',
     '{"max_users": 25, "max_locations": 5, "max_medicines": 10000, "max_scheduled_reports": 10}',
     ARRAY['core', 'dispensing', 'purchasing', 'reports', 'notifications', 'barcode', 'stock_count', 'email_receipts',
           'multi_location', 'advanced_analytics', 'ai_assistant', 'scheduled_reports', 'advanced_reports',
           'purchase_approvals', 'whatsapp', 'sms'], 2),
    ('ENTERPRISE', 'Enterprise', 'Everything, plus hospital requisitions, multi-branch scale and integrations',
     '{"max_users": null, "max_locations": null, "max_medicines": null, "max_scheduled_reports": null}',
     ARRAY['core', 'dispensing', 'purchasing', 'reports', 'notifications', 'barcode', 'stock_count', 'email_receipts',
           'multi_location', 'advanced_analytics', 'ai_assistant', 'scheduled_reports', 'advanced_reports',
           'purchase_approvals', 'whatsapp', 'sms', 'hospital', 'api_access'], 3);
-- Plans are now a catalogue managed by MedCart Tech (the fixed list check is replaced by a key).
ALTER TABLE organizations DROP CONSTRAINT organizations_plan_check;
ALTER TABLE organizations ADD CONSTRAINT organizations_plan_fk FOREIGN KEY (plan) REFERENCES plans(code);

CREATE TABLE subscriptions (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL REFERENCES organizations(id),
    plan_code varchar(30) NOT NULL REFERENCES plans(code),
    billing_cycle varchar(10) NOT NULL CHECK (billing_cycle IN ('MONTHLY', 'ANNUAL', 'MANUAL')),
    status varchar(20) NOT NULL CHECK (status IN ('TRIAL', 'ACTIVE', 'PAST_DUE', 'CANCELLED', 'EXPIRED')),
    current_period_start date,
    current_period_end date,
    cancel_at_period_end boolean NOT NULL DEFAULT false,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by integer REFERENCES users(id)
);
CREATE INDEX subscriptions_org_idx ON subscriptions (organization_id, created_at DESC);

CREATE TABLE messaging_packages (
    code varchar(30) PRIMARY KEY,
    label varchar(80) NOT NULL,
    credits integer NOT NULL CHECK (credits > 0),
    price numeric(12, 2),                   -- set by MedCart Tech; NULL = not sold yet
    currency varchar(3),
    is_active boolean NOT NULL DEFAULT false,
    updated_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE payments (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL REFERENCES organizations(id),
    purpose varchar(20) NOT NULL CHECK (purpose IN ('SUBSCRIPTION', 'MESSAGING_CREDITS')),
    plan_code varchar(30) REFERENCES plans(code),
    billing_cycle varchar(10),
    package_code varchar(30) REFERENCES messaging_packages(code),
    amount numeric(12, 2) NOT NULL CHECK (amount >= 0),
    currency varchar(3) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PAID', 'FAILED', 'CANCELLED', 'REFUNDED')),
    provider varchar(30) NOT NULL,
    reference varchar(100) NOT NULL UNIQUE,
    checkout_url text,
    created_by integer REFERENCES users(id),
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at timestamp without time zone,
    applied_at timestamp without time zone,
    failure_reason text
);
CREATE INDEX payments_org_idx ON payments (organization_id, created_at DESC);

CREATE TABLE messaging_credit_ledger (
    id bigserial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    change integer NOT NULL,
    reason varchar(20) NOT NULL CHECK (reason IN ('PURCHASE', 'MESSAGE', 'REFUND', 'GRANT', 'CORRECTION')),
    payment_id integer REFERENCES payments(id),
    delivery_id bigint,
    note text,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by integer REFERENCES users(id)
);
CREATE INDEX messaging_credit_ledger_org_idx ON messaging_credit_ledger (organization_id);

-- ---------------------------------------------------------------- messaging
-- Organization's own provider credentials (model A), encrypted with SECRET_KEY.
CREATE TABLE messaging_providers (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    channel varchar(10) NOT NULL CHECK (channel IN ('WHATSAPP', 'SMS', 'EMAIL')),
    provider varchar(30) NOT NULL,
    config_encrypted text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    updated_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by integer REFERENCES users(id),
    CONSTRAINT messaging_providers_channel_unique UNIQUE (organization_id, channel)
);

ALTER TABLE notification_deliveries DROP CONSTRAINT notification_deliveries_channel_check;
ALTER TABLE notification_deliveries ADD CONSTRAINT notification_deliveries_channel_check
    CHECK (channel IN ('EMAIL', 'SMS', 'WHATSAPP'));
ALTER TABLE notification_deliveries
    ADD COLUMN message_type varchar(20) NOT NULL DEFAULT 'ALERT'
        CHECK (message_type IN ('ALERT', 'RECEIPT', 'THANK_YOU', 'REPORT', 'SECURITY', 'TEST', 'PASSWORD_RESET')),
    ADD COLUMN provider varchar(30),
    ADD COLUMN provider_message_id varchar(120),
    ADD COLUMN delivery_status varchar(20),     -- as reported by the provider: SENT / DELIVERED / READ / FAILED
    ADD COLUMN delivery_status_at timestamp without time zone,
    ADD COLUMN dispensation_id integer REFERENCES dispensations(id),
    ADD COLUMN consent_status varchar(20),      -- CONSENTED / NOT_REQUIRED for customer messages
    ADD COLUMN template_name varchar(100),
    ADD COLUMN template_params jsonb,
    ADD COLUMN credits_charged integer NOT NULL DEFAULT 0;

-- Provider callbacks carry only the provider's message id.
CREATE TABLE provider_message_routes (
    provider varchar(30) NOT NULL,
    provider_message_id varchar(120) NOT NULL,
    organization_id integer NOT NULL REFERENCES organizations(id),
    delivery_id bigint NOT NULL,
    PRIMARY KEY (provider, provider_message_id)
);

-- Customer contact preferences per organization (opt-in / opt-out).
CREATE TABLE message_consents (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    phone varchar(50) NOT NULL,
    channel varchar(10) NOT NULL CHECK (channel IN ('WHATSAPP', 'SMS')),
    status varchar(12) NOT NULL CHECK (status IN ('OPTED_IN', 'OPTED_OUT')),
    source varchar(30) NOT NULL,
    recorded_by integer REFERENCES users(id),
    recorded_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT message_consents_unique UNIQUE (organization_id, phone, channel)
);
-- Opt-outs received on the shared platform WhatsApp number apply to every organization.
CREATE TABLE platform_opt_outs (
    phone varchar(50) NOT NULL,
    channel varchar(10) NOT NULL,
    recorded_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (phone, channel)
);

-- ---------------------------------------------------------------- row level security for new tenant tables
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['idempotency_keys', 'stock_adjustments', 'stock_counts', 'stock_count_lines',
                             'organization_files', 'messaging_credit_ledger', 'messaging_providers',
                             'message_consents'] LOOP
        EXECUTE format('CREATE INDEX %I ON %I (organization_id)', t || '_org_id_idx', t);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) '
                       'WITH CHECK (organization_id = current_org())', t);
    END LOOP;
END $$;

-- ---------------------------------------------------------------- settings for every existing organization
ALTER TABLE app_settings NO FORCE ROW LEVEL SECURITY;
INSERT INTO app_settings (organization_id, key, value, description)
SELECT o.id, s.key, s.value::jsonb, s.description
FROM organizations o
CROSS JOIN (VALUES
    ('company.email', '""', 'Company e-mail shown on receipts and reports'),
    ('company.website', '""', 'Company website'),
    ('company.registration_number', '""', 'Pharmacy / business registration number'),
    ('company.tax_id', '""', 'Tax identification number (TIN)'),
    ('receipt.footer', '"Thank you for your patronage."', 'Text printed at the bottom of receipts'),
    ('receipt.tax_rate_percent', '0', 'Tax added to sales, percent (0 = no tax line)'),
    ('receipt.tax_label', '"Tax"', 'Name of the tax on receipts (e.g. VAT, NHIL)'),
    ('receipt.show_batches', 'true', 'Print batch numbers and expiry on receipts'),
    ('report.footer', '""', 'Text printed at the bottom of PDF reports'),
    ('adjustments.approval_quantity_threshold', '0', 'Adjustments of more units than this need approval (0 = never)'),
    ('adjustments.approval_value_threshold', '0', 'Adjustments worth more than this need approval (0 = never)'),
    ('purchasing.approval_required', 'false', 'Purchase orders must be submitted and approved before ordering'),
    ('reorder.safety_days', '7', 'Safety stock, in days of average consumption'),
    ('comms.whatsapp_receipts', 'false', 'Send the digital receipt on WhatsApp after a sale (customer consent required)'),
    ('comms.whatsapp_thank_you', 'false', 'Send a thank-you message on WhatsApp after a sale'),
    ('comms.sms_receipts', 'false', 'Send the receipt link by SMS after a sale (customer consent required)'),
    ('comms.email_receipts', 'false', 'E-mail the receipt after a sale (customer consent required)'),
    ('comms.thank_you_text', '"Thank you for shopping with us."', 'Thank-you text used in receipt messages'),
    ('security.require_mfa_for_admins', 'false', 'Owners and administrators must use two-factor sign-in')
) AS s(key, value, description)
ON CONFLICT DO NOTHING;
ALTER TABLE app_settings FORCE ROW LEVEL SECURITY;
