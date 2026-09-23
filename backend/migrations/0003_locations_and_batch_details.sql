-- 0003 LOCATIONS AND BATCH DETAILS
--
-- Why these changes are necessary (not merely "useful"):
--
-- * locations: stock valuation by location and future multi-branch /
--   hospital use need to know WHERE a batch is. No existing table holds this.
--   A self-referencing parent_id lets a hospital model Store -> Pharmacy ->
--   Ward later without another schema change.
--
-- * batches.unit_cost: stock valuation needs a cost per batch. For batches
--   received through a purchase order it can be traced through
--   purchase_receipts -> purchase_order_items.unit_cost, and it is backfilled
--   from there below. Batches entered directly (POST /batches, opening stock)
--   have no purchase order, so the cost must live on the batch. Where a batch
--   was received over several receipts at different prices, the weighted
--   average cost is stored.
--
-- * batches.supplier_id / received_date: the same reasoning. Backfilled from
--   purchase receipts where traceable; left NULL (unknown) otherwise. Nothing
--   is invented.

CREATE TABLE locations (
    id serial PRIMARY KEY,
    name varchar(150) NOT NULL,
    location_type varchar(30) NOT NULL,
    parent_id integer REFERENCES locations (id),
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT locations_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT locations_type_check CHECK (
        location_type IN ('PHARMACY', 'STORE', 'COLD_CHAIN', 'WARD', 'BRANCH', 'DEPARTMENT')
    ),
    CONSTRAINT locations_not_own_parent CHECK (parent_id IS NULL OR parent_id <> id)
);

CREATE UNIQUE INDEX locations_name_unique ON locations (lower(btrim(name)));

INSERT INTO locations (name, location_type) VALUES ('Main Pharmacy', 'PHARMACY');


ALTER TABLE batches
    ADD COLUMN location_id integer REFERENCES locations (id),
    ADD COLUMN unit_cost numeric(12, 2),
    ADD COLUMN supplier_id integer REFERENCES suppliers (id),
    ADD COLUMN received_date date,
    ADD COLUMN created_at timestamp without time zone;

ALTER TABLE batches
    ADD CONSTRAINT batches_unit_cost_non_negative CHECK (unit_cost IS NULL OR unit_cost >= 0);

-- Every existing batch is in the single existing pharmacy.
UPDATE batches
SET location_id = (SELECT id FROM locations WHERE name = 'Main Pharmacy');

ALTER TABLE batches ALTER COLUMN location_id SET NOT NULL;

-- created_at is unknown for existing rows (left NULL); new rows get a timestamp.
ALTER TABLE batches ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP;


-- Backfill cost, supplier and received date from purchase receipts.
WITH receipt_summary AS (
    SELECT
        purchase_receipts.batch_id,
        SUM(purchase_receipts.quantity_received * purchase_order_items.unit_cost)
            / NULLIF(SUM(purchase_receipts.quantity_received), 0) AS weighted_cost,
        MIN(purchase_receipts.received_date)::date AS first_received,
        (ARRAY_AGG(purchase_orders.supplier_id ORDER BY purchase_receipts.received_date))[1] AS supplier_id
    FROM purchase_receipts
    JOIN purchase_order_items ON purchase_receipts.purchase_order_item_id = purchase_order_items.id
    JOIN purchase_orders ON purchase_order_items.purchase_order_id = purchase_orders.id
    GROUP BY purchase_receipts.batch_id
)
UPDATE batches
SET
    unit_cost = ROUND(receipt_summary.weighted_cost, 2),
    received_date = receipt_summary.first_received,
    supplier_id = receipt_summary.supplier_id
FROM receipt_summary
WHERE batches.id = receipt_summary.batch_id;


DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM batches GROUP BY medicine_id, batch_number, location_id HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Migration 0003: the same batch number exists twice for one medicine. Merge the rows, then re-run.';
    END IF;
END $$;

-- The same batch may later exist at several locations (after transfers),
-- but only once per location.
CREATE UNIQUE INDEX batches_medicine_batch_location_unique
    ON batches (medicine_id, batch_number, location_id);

CREATE INDEX idx_batches_location ON batches (location_id);
CREATE INDEX idx_batches_supplier ON batches (supplier_id);
