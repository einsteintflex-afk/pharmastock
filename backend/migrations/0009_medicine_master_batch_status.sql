-- 0009 MEDICINE MASTER DATA, BATCH STATUS, BARCODES
--
-- medicines: generic and brand names, route of administration, manufacturer,
--   an active flag (discontinued products keep their history but cannot be
--   ordered or registered again) and the GS1 GTIN printed on the pack.
--   Existing rows: generic_name is filled from name; everything else stays
--   empty; all medicines stay active.
-- batches: a hold status. QUARANTINED (under investigation) and RECALLED
--   (manufacturer / regulator recall) stock stays on the books but is
--   excluded from FEFO, dispensing and usable stock. purchase_date is the
--   order date of the purchase order that supplied the batch (backfilled
--   from receipts). barcode_data keeps the raw scanned GS1 string, if any.
--
-- Row level security is forced on these tables, so the backfill
-- temporarily lifts FORCE (the migration runs as the table owner).

ALTER TABLE medicines
    ADD COLUMN generic_name varchar(150),
    ADD COLUMN brand_name varchar(150),
    ADD COLUMN route varchar(50),
    ADD COLUMN manufacturer varchar(150),
    ADD COLUMN is_active boolean NOT NULL DEFAULT true,
    ADD COLUMN gtin varchar(14),
    ADD CONSTRAINT medicines_gtin_format CHECK (gtin IS NULL OR gtin ~ '^[0-9]{8}$|^[0-9]{12,14}$');

CREATE UNIQUE INDEX medicines_gtin_unique ON medicines (organization_id, gtin) WHERE gtin IS NOT NULL;
CREATE INDEX medicines_active_idx ON medicines (organization_id, is_active);

ALTER TABLE batches
    ADD COLUMN batch_status varchar(20) NOT NULL DEFAULT 'ACTIVE',
    ADD COLUMN status_reason text,
    ADD COLUMN status_changed_at timestamp without time zone,
    ADD COLUMN purchase_date date,
    ADD COLUMN barcode_data varchar(200),
    ADD CONSTRAINT batches_status_check CHECK (batch_status IN ('ACTIVE', 'QUARANTINED', 'RECALLED'));

CREATE INDEX batches_fefo_idx ON batches (organization_id, medicine_id, expiry_date) WHERE quantity > 0;
CREATE INDEX stock_movements_batch_date_idx ON stock_movements (batch_id, movement_date);
CREATE INDEX stock_movements_type_date_idx ON stock_movements (organization_id, movement_type, movement_date);

ALTER TABLE medicines NO FORCE ROW LEVEL SECURITY;
ALTER TABLE batches NO FORCE ROW LEVEL SECURITY;
ALTER TABLE purchase_receipts NO FORCE ROW LEVEL SECURITY;
ALTER TABLE purchase_order_items NO FORCE ROW LEVEL SECURITY;
ALTER TABLE purchase_orders NO FORCE ROW LEVEL SECURITY;

UPDATE medicines SET generic_name = name WHERE generic_name IS NULL;

UPDATE batches SET purchase_date = first_order.order_date
FROM (
    SELECT pr.batch_id, MIN(po.order_date)::date AS order_date
    FROM purchase_receipts pr
    JOIN purchase_order_items poi ON poi.id = pr.purchase_order_item_id
    JOIN purchase_orders po ON po.id = poi.purchase_order_id
    GROUP BY pr.batch_id
) AS first_order
WHERE batches.id = first_order.batch_id AND batches.purchase_date IS NULL;

ALTER TABLE medicines FORCE ROW LEVEL SECURITY;
ALTER TABLE batches FORCE ROW LEVEL SECURITY;
ALTER TABLE purchase_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE purchase_order_items FORCE ROW LEVEL SECURITY;
ALTER TABLE purchase_orders FORCE ROW LEVEL SECURITY;
