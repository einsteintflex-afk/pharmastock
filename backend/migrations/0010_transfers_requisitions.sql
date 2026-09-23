-- 0010 STOCK TRANSFERS AND REQUISITIONS
--
-- Moving stock between locations (central store -> ward, branch -> branch)
-- and requisitions (a ward or department asking the store for stock) share
-- one workflow:
--
--   REQUESTED -> APPROVED -> DISPATCHED (in transit) -> RECEIVED
--        \-> REJECTED         \-> CANCELLED (before dispatch)
--
-- Dispatch takes units from the source location FEFO and writes a
-- TRANSFER_OUT movement per source batch; receipt writes TRANSFER_IN into a
-- batch with the same number and expiry at the destination (created if
-- needed). transfer_allocations links each unit to both movements, so the
-- ledger explains every batch at every location and recalls can be traced.
--
-- Ledger sign: TRANSFER_IN adds, TRANSFER_OUT subtracts.
-- users.location_id (0008) scopes a user to one location for transfers.

ALTER TABLE stock_movements DROP CONSTRAINT stock_movements_type_check;
ALTER TABLE stock_movements ADD CONSTRAINT stock_movements_type_check CHECK (
    movement_type IN ('RECEIVED', 'DISPENSED', 'RETURNED', 'DAMAGED', 'EXPIRED', 'ADJUSTMENT',
                      'TRANSFER_OUT', 'TRANSFER_IN')
);

CREATE TABLE transfers (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    transfer_number varchar(30) NOT NULL,
    request_type varchar(20) NOT NULL DEFAULT 'TRANSFER'
        CHECK (request_type IN ('TRANSFER', 'REQUISITION')),
    priority varchar(10) NOT NULL DEFAULT 'ROUTINE' CHECK (priority IN ('ROUTINE', 'URGENT')),
    status varchar(20) NOT NULL DEFAULT 'REQUESTED'
        CHECK (status IN ('REQUESTED', 'APPROVED', 'DISPATCHED', 'RECEIVED', 'REJECTED', 'CANCELLED')),
    from_location_id integer NOT NULL REFERENCES locations(id),
    to_location_id integer NOT NULL REFERENCES locations(id),
    notes text,
    requested_by integer REFERENCES users(id),
    requested_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_by integer REFERENCES users(id),
    approved_at timestamp without time zone,
    dispatched_by integer REFERENCES users(id),
    dispatched_at timestamp without time zone,
    received_by integer REFERENCES users(id),
    received_at timestamp without time zone,
    approval_note text,
    closed_reason text,
    CONSTRAINT transfers_different_locations CHECK (from_location_id <> to_location_id),
    CONSTRAINT transfers_number_unique UNIQUE (organization_id, transfer_number)
);
CREATE INDEX transfers_status_idx ON transfers (organization_id, status, requested_at DESC);

CREATE TABLE transfer_items (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    transfer_id integer NOT NULL REFERENCES transfers(id) ON DELETE CASCADE,
    medicine_id integer NOT NULL REFERENCES medicines(id),
    quantity_requested integer NOT NULL CHECK (quantity_requested > 0),
    quantity_approved integer CHECK (quantity_approved >= 0),
    quantity_dispatched integer NOT NULL DEFAULT 0 CHECK (quantity_dispatched >= 0),
    quantity_received integer NOT NULL DEFAULT 0 CHECK (quantity_received >= 0),
    CONSTRAINT transfer_items_medicine_unique UNIQUE (transfer_id, medicine_id)
);
CREATE INDEX transfer_items_transfer_idx ON transfer_items (transfer_id);

CREATE TABLE transfer_allocations (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL DEFAULT current_org() REFERENCES organizations(id),
    transfer_item_id integer NOT NULL REFERENCES transfer_items(id) ON DELETE CASCADE,
    source_batch_id integer NOT NULL REFERENCES batches(id),
    destination_batch_id integer REFERENCES batches(id),
    quantity integer NOT NULL CHECK (quantity > 0),
    out_movement_id integer NOT NULL REFERENCES stock_movements(id),
    in_movement_id integer REFERENCES stock_movements(id)
);
CREATE INDEX transfer_allocations_item_idx ON transfer_allocations (transfer_item_id);
CREATE INDEX transfer_allocations_source_idx ON transfer_allocations (source_batch_id);

ALTER TABLE notifications DROP CONSTRAINT notifications_category_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_category_check CHECK (
    category IN ('EXPIRY', 'LOW_STOCK', 'PURCHASING', 'RECEIVING', 'INVENTORY', 'SYSTEM', 'TRANSFERS')
);

ALTER TABLE stock_movements ADD COLUMN transfer_id integer REFERENCES transfers(id);
CREATE INDEX stock_movements_transfer_idx ON stock_movements (transfer_id) WHERE transfer_id IS NOT NULL;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['transfers', 'transfer_items', 'transfer_allocations'] LOOP
        EXECUTE format('CREATE INDEX %I ON %I (organization_id)', t || '_org_idx', t);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) '
                       'WITH CHECK (organization_id = current_org())', t);
    END LOOP;
END $$;

-- Setting for every existing organization (new ones are seeded by the app).
ALTER TABLE app_settings NO FORCE ROW LEVEL SECURITY;
INSERT INTO app_settings (organization_id, key, value, description)
SELECT id, 'transfers.separate_approver', 'false'::jsonb,
       'Transfers and requisitions must be approved by someone other than the requester'
FROM organizations
ON CONFLICT DO NOTHING;
ALTER TABLE app_settings FORCE ROW LEVEL SECURITY;
