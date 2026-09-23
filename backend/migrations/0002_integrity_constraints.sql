-- 0002 INTEGRITY CONSTRAINTS
-- Adds validation the original schema relied on the API for (and which the
-- API did not always enforce: e.g. PUT /medicines accepted a negative
-- reorder level). Existing data is checked first; the migration stops with
-- a clear message instead of silently changing any row.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM medicines WHERE reorder_level < 0) THEN
        RAISE EXCEPTION 'Migration 0002: medicines with a negative reorder_level exist. Correct them, then re-run.';
    END IF;

    IF EXISTS (SELECT 1 FROM medicines WHERE btrim(name) = '') THEN
        RAISE EXCEPTION 'Migration 0002: medicines with a blank name exist. Correct them, then re-run.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM medicines
        GROUP BY lower(btrim(name)), lower(coalesce(btrim(strength), '')), lower(coalesce(btrim(dosage_form), ''))
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Migration 0002: duplicate medicines (same name, strength and dosage form) exist. Merge them, then re-run.';
    END IF;

    IF EXISTS (SELECT 1 FROM batches WHERE quantity < 0) THEN
        RAISE EXCEPTION 'Migration 0002: batches with negative quantity exist. Correct them, then re-run.';
    END IF;

    IF EXISTS (SELECT 1 FROM batches WHERE btrim(batch_number) = '') THEN
        RAISE EXCEPTION 'Migration 0002: batches with a blank batch number exist. Correct them, then re-run.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM stock_movements
        WHERE movement_type NOT IN ('RECEIVED', 'DISPENSED', 'RETURNED', 'DAMAGED', 'EXPIRED', 'ADJUSTMENT')
    ) THEN
        RAISE EXCEPTION 'Migration 0002: stock movements with an unknown movement_type exist.';
    END IF;

    IF EXISTS (SELECT 1 FROM suppliers WHERE btrim(name) = '') THEN
        RAISE EXCEPTION 'Migration 0002: suppliers with a blank name exist. Correct them, then re-run.';
    END IF;

    IF EXISTS (SELECT 1 FROM suppliers GROUP BY lower(btrim(name)) HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'Migration 0002: duplicate supplier names exist. Merge them, then re-run.';
    END IF;
END $$;


-- Medicines
ALTER TABLE medicines
    ADD CONSTRAINT medicines_reorder_level_non_negative CHECK (reorder_level >= 0),
    ADD CONSTRAINT medicines_name_not_blank CHECK (btrim(name) <> '');

CREATE UNIQUE INDEX medicines_identity_unique
    ON medicines (lower(btrim(name)), lower(coalesce(btrim(strength), '')), lower(coalesce(btrim(dosage_form), '')));


-- Batches
ALTER TABLE batches
    ADD CONSTRAINT batches_quantity_non_negative CHECK (quantity >= 0),
    ADD CONSTRAINT batches_batch_number_not_blank CHECK (btrim(batch_number) <> '');

CREATE INDEX idx_batches_medicine ON batches (medicine_id);
CREATE INDEX idx_batches_expiry ON batches (expiry_date);


-- Stock movements
ALTER TABLE stock_movements
    ADD CONSTRAINT stock_movements_type_check CHECK (
        movement_type IN ('RECEIVED', 'DISPENSED', 'RETURNED', 'DAMAGED', 'EXPIRED', 'ADJUSTMENT')
    );

CREATE INDEX idx_stock_movements_batch ON stock_movements (batch_id);
CREATE INDEX idx_stock_movements_date ON stock_movements (movement_date);
CREATE INDEX idx_stock_movements_type_date ON stock_movements (movement_type, movement_date);


-- Suppliers
ALTER TABLE suppliers
    ADD CONSTRAINT suppliers_name_not_blank CHECK (btrim(name) <> '');

CREATE UNIQUE INDEX suppliers_name_unique ON suppliers (lower(btrim(name)));


-- Purchase receipts (foreign keys had no indexes)
CREATE INDEX idx_purchase_receipts_item ON purchase_receipts (purchase_order_item_id);
CREATE INDEX idx_purchase_receipts_batch ON purchase_receipts (batch_id);
