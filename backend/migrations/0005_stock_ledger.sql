-- 0005 STOCK LEDGER
--
-- Makes stock_movements a reconcilable ledger:
--
-- * user_id records who made each movement.
--
-- * ADJUSTMENT rows now store the signed CHANGE (e.g. -5), not the counted
--   quantity. Previously the API stored the counted quantity, so the ledger
--   could not be summed. The API contract is unchanged: POST /stock-movements
--   with ADJUSTMENT still takes the physically counted quantity; the API now
--   records the difference.
--
-- * Opening balances: batches created directly (POST /batches) and legacy
--   test data had quantities with no matching movement. For each batch whose
--   quantity differs from the sum of its movements, ONE clearly-labelled
--   ADJUSTMENT row is added, dated before that batch's first movement. No
--   batch quantity is changed.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM stock_movements WHERE movement_type = 'ADJUSTMENT') THEN
        RAISE EXCEPTION 'Migration 0005: existing ADJUSTMENT movements store counted quantities, which cannot be converted automatically. Review them with the maintainer before migrating.';
    END IF;
END $$;


ALTER TABLE stock_movements
    ADD COLUMN user_id integer REFERENCES users (id);

CREATE INDEX idx_stock_movements_user ON stock_movements (user_id);


WITH ledger AS (
    SELECT
        batches.id AS batch_id,
        batches.quantity - COALESCE(SUM(
            CASE
                WHEN stock_movements.movement_type IN ('RECEIVED', 'RETURNED')
                    THEN stock_movements.quantity
                WHEN stock_movements.movement_type IN ('DISPENSED', 'DAMAGED', 'EXPIRED')
                    THEN -stock_movements.quantity
                ELSE 0
            END
        ), 0) AS gap,
        MIN(stock_movements.movement_date) AS first_movement
    FROM batches
    LEFT JOIN stock_movements ON stock_movements.batch_id = batches.id
    GROUP BY batches.id, batches.quantity
)
INSERT INTO stock_movements (batch_id, movement_type, quantity, movement_date, reason)
SELECT
    batch_id,
    'ADJUSTMENT',
    gap,
    COALESCE(first_movement - INTERVAL '1 second', CURRENT_TIMESTAMP),
    'Opening balance (recorded by migration 0005 to reconcile the stock ledger)'
FROM ledger
WHERE gap <> 0;


ALTER TABLE stock_movements
    ADD CONSTRAINT stock_movements_quantity_sign CHECK (
        (movement_type = 'ADJUSTMENT' AND quantity <> 0)
        OR (movement_type <> 'ADJUSTMENT' AND quantity > 0)
    );
