-- 0008 ORGANIZATIONS AND TENANT ISOLATION
--
-- PharmaStock becomes multi-organization (SaaS-ready). Every business row
-- belongs to an organization, and PostgreSQL ROW LEVEL SECURITY enforces
-- isolation in the database itself:
--
--   * The application sets `app.organization_id` on its connection after
--     authenticating the user (see backend/database.py).
--   * Every tenant table has a policy `organization_id = current_org()`,
--     FORCED so it also applies to the table owner. A request can therefore
--     never read or write another organization's rows, even through a bug in
--     application code. If the setting is missing, no rows are visible and
--     inserts fail (the default organization_id is NULL -> NOT NULL error).
--   * Superusers and roles with BYPASSRLS ignore all policies: production
--     must connect as an ordinary role (see deploy/create_app_role.sql). The
--     application refuses to start in production otherwise.
--
-- Existing data: one organization (id 1) is created and every existing row
-- is assigned to it. No row is removed or changed otherwise.
--
-- Later migrations that must change data across organizations temporarily
-- run `ALTER TABLE ... NO FORCE ROW LEVEL SECURITY` inside their transaction.

CREATE TABLE organizations (
    id serial PRIMARY KEY,
    name varchar(150) NOT NULL,
    org_type varchar(30) NOT NULL DEFAULT 'COMMUNITY_PHARMACY',
    status varchar(20) NOT NULL DEFAULT 'ACTIVE',
    plan varchar(20) NOT NULL DEFAULT 'BASIC',
    limits jsonb NOT NULL DEFAULT '{}'::jsonb,
    billing_customer_ref varchar(100),
    trial_ends_at timestamp without time zone,
    current_period_end timestamp without time zone,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT organizations_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT organizations_type_check CHECK (
        org_type IN ('COMMUNITY_PHARMACY', 'PHARMACY_CHAIN', 'HOSPITAL', 'WHOLESALE')
    ),
    CONSTRAINT organizations_status_check CHECK (status IN ('TRIAL', 'ACTIVE', 'SUSPENDED', 'CANCELLED')),
    CONSTRAINT organizations_plan_check CHECK (plan IN ('BASIC', 'PROFESSIONAL', 'ENTERPRISE'))
);

-- The existing installation becomes organization 1 with every capability.
INSERT INTO organizations (id, name, org_type, status, plan)
SELECT 1,
       COALESCE(NULLIF((SELECT value #>> '{}' FROM app_settings WHERE key = 'pharmacy.name'), ''), 'PharmaStock Pharmacy'),
       'COMMUNITY_PHARMACY', 'ACTIVE', 'ENTERPRISE';
SELECT setval('organizations_id_seq', 1, true);


CREATE FUNCTION current_org() RETURNS integer
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.organization_id', true), '')::integer
$$;


-- Users belong to one organization (usernames stay globally unique so that
-- sign-in does not need an organization code).
ALTER TABLE users
    ADD COLUMN organization_id integer REFERENCES organizations (id),
    ADD COLUMN is_platform_admin boolean NOT NULL DEFAULT false,
    -- Optional home location (e.g. a ward): requisitions and dispensing are
    -- then limited to that location.
    ADD COLUMN location_id integer REFERENCES locations (id);
UPDATE users SET organization_id = 1;
ALTER TABLE users ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_users_organization ON users (organization_id);
-- Administrators of the original installation can manage organizations.
UPDATE users SET is_platform_admin = true WHERE role = 'ADMINISTRATOR';


-- Tenant columns on every business table.
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'medicines', 'batches', 'suppliers', 'purchase_orders', 'purchase_order_items',
        'purchase_receipts', 'stock_movements', 'locations', 'notifications', 'notification_reads',
        'app_settings', 'dispensations', 'dispensation_items'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ADD COLUMN organization_id integer REFERENCES organizations (id)', t);
        EXECUTE format('UPDATE %I SET organization_id = 1', t);
        EXECUTE format('ALTER TABLE %I ALTER COLUMN organization_id SET NOT NULL', t);
        EXECUTE format('ALTER TABLE %I ALTER COLUMN organization_id SET DEFAULT current_org()', t);
        EXECUTE format('CREATE INDEX %I ON %I (organization_id)', 'idx_' || t || '_organization', t);
    END LOOP;
END $$;

-- Audit log: organization is NULL for events before an organization is
-- known (e.g. sign-in with an unknown username).
ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only;
ALTER TABLE audit_log ADD COLUMN organization_id integer REFERENCES organizations (id);
UPDATE audit_log SET organization_id = 1;
ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only;
ALTER TABLE audit_log ALTER COLUMN organization_id SET DEFAULT current_org();
CREATE INDEX idx_audit_log_organization ON audit_log (organization_id, occurred_at DESC);


-- Uniqueness becomes per organization.
DROP INDEX medicines_identity_unique;
CREATE UNIQUE INDEX medicines_identity_unique ON medicines (
    organization_id, lower(btrim(name)), lower(coalesce(btrim(strength), '')), lower(coalesce(btrim(dosage_form), ''))
);

DROP INDEX suppliers_name_unique;
CREATE UNIQUE INDEX suppliers_name_unique ON suppliers (organization_id, lower(btrim(name)));

DROP INDEX locations_name_unique;
CREATE UNIQUE INDEX locations_name_unique ON locations (organization_id, lower(btrim(name)));

ALTER TABLE purchase_orders DROP CONSTRAINT purchase_orders_order_number_key;
ALTER TABLE purchase_orders ADD CONSTRAINT purchase_orders_order_number_key UNIQUE (organization_id, order_number);

ALTER TABLE dispensations DROP CONSTRAINT dispensations_dispensation_number_key;
ALTER TABLE dispensations ADD CONSTRAINT dispensations_dispensation_number_key UNIQUE (organization_id, dispensation_number);

ALTER TABLE notifications DROP CONSTRAINT notifications_dedupe_key_key;
ALTER TABLE notifications ADD CONSTRAINT notifications_dedupe_key_key UNIQUE (organization_id, dedupe_key);

ALTER TABLE app_settings DROP CONSTRAINT app_settings_pkey;
ALTER TABLE app_settings ADD PRIMARY KEY (organization_id, key);


-- Hospitals keep a central medical store.
ALTER TABLE locations DROP CONSTRAINT locations_type_check;
ALTER TABLE locations ADD CONSTRAINT locations_type_check CHECK (
    location_type IN ('PHARMACY', 'STORE', 'CENTRAL_STORE', 'COLD_CHAIN', 'WARD', 'BRANCH', 'DEPARTMENT')
);


-- Row level security.
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'medicines', 'batches', 'suppliers', 'purchase_orders', 'purchase_order_items',
        'purchase_receipts', 'stock_movements', 'locations', 'notifications', 'notification_reads',
        'app_settings', 'dispensations', 'dispensation_items'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) '
            'WITH CHECK (organization_id = current_org())', t);
    END LOOP;
END $$;

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_log
    USING (organization_id = current_org())
    WITH CHECK (organization_id IS NULL OR organization_id = current_org());
