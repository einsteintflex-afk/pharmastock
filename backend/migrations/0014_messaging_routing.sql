-- ============================================================
-- 0014: routing for organizations' own WhatsApp numbers
-- ============================================================
-- Additive. The WhatsApp Business Platform identifies the receiving number
-- (phone_number_id) in its webhooks before the organization is known, so
-- the number -> organization mapping lives outside row level security.

CREATE TABLE whatsapp_numbers (
    phone_number_id varchar(64) PRIMARY KEY,
    organization_id integer NOT NULL REFERENCES organizations(id),
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX whatsapp_numbers_org_idx ON whatsapp_numbers (organization_id);

CREATE INDEX notification_deliveries_dispensation_idx ON notification_deliveries (dispensation_id)
    WHERE dispensation_id IS NOT NULL;
