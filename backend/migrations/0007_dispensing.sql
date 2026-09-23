-- 0007 DISPENSING COUNTER
--
-- Community-pharmacy dispensing: one DISPENSATION per customer / prescription,
-- with one line per medicine. Each line is filled FEFO from one or more
-- batches; the resulting DISPENSED stock movements point back to the line
-- (stock_movements.dispensation_item_id), so every unit can be traced from
-- batch to dispensation (needed for recalls) and a void can return stock to
-- the exact batches it came from.
--
-- Why new tables: no existing table records a customer transaction, its
-- prescription details, prices or payment. stock_movements alone cannot
-- group several medicines into one sale.
--
-- medicines.selling_price: the default price per unit at the counter
-- (optional; a line can override it). No existing column holds a price.
--
-- Patient details are optional and minimal (name, phone) - collect only
-- what the pharmacy's policy requires.

ALTER TABLE medicines
    ADD COLUMN selling_price numeric(12, 2),
    ADD CONSTRAINT medicines_selling_price_non_negative CHECK (selling_price IS NULL OR selling_price >= 0);


CREATE TABLE dispensations (
    id serial PRIMARY KEY,
    dispensation_number varchar(30) NOT NULL UNIQUE,
    dispensed_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dispense_type varchar(20) NOT NULL,
    patient_name varchar(150),
    patient_phone varchar(50),
    prescriber varchar(150),
    prescription_number varchar(100),
    location_id integer REFERENCES locations (id),
    payment_method varchar(20) NOT NULL,
    total_amount numeric(12, 2) NOT NULL DEFAULT 0,
    notes text,
    status varchar(20) NOT NULL DEFAULT 'COMPLETED',
    user_id integer NOT NULL REFERENCES users (id),
    voided_at timestamp without time zone,
    voided_by integer REFERENCES users (id),
    void_reason text,
    CONSTRAINT dispensations_type_check CHECK (dispense_type IN ('PRESCRIPTION', 'OTC')),
    CONSTRAINT dispensations_payment_check CHECK (
        payment_method IN ('CASH', 'MOBILE_MONEY', 'CARD', 'NHIS', 'INSURANCE', 'CREDIT', 'NO_CHARGE')
    ),
    CONSTRAINT dispensations_status_check CHECK (status IN ('COMPLETED', 'VOIDED')),
    CONSTRAINT dispensations_total_non_negative CHECK (total_amount >= 0),
    CONSTRAINT dispensations_void_consistent CHECK (
        (status = 'VOIDED') = (voided_at IS NOT NULL)
    )
);

CREATE INDEX idx_dispensations_date ON dispensations (dispensed_at DESC);
CREATE INDEX idx_dispensations_status ON dispensations (status);


CREATE TABLE dispensation_items (
    id serial PRIMARY KEY,
    dispensation_id integer NOT NULL REFERENCES dispensations (id),
    medicine_id integer NOT NULL REFERENCES medicines (id),
    quantity integer NOT NULL,
    unit_price numeric(12, 2),
    line_total numeric(12, 2),
    directions varchar(255),
    CONSTRAINT dispensation_items_quantity_positive CHECK (quantity > 0),
    CONSTRAINT dispensation_items_price_non_negative CHECK (unit_price IS NULL OR unit_price >= 0),
    CONSTRAINT dispensation_items_one_line_per_medicine UNIQUE (dispensation_id, medicine_id)
);

CREATE INDEX idx_dispensation_items_dispensation ON dispensation_items (dispensation_id);
CREATE INDEX idx_dispensation_items_medicine ON dispensation_items (medicine_id);


ALTER TABLE stock_movements
    ADD COLUMN dispensation_item_id integer REFERENCES dispensation_items (id);

CREATE INDEX idx_stock_movements_dispensation_item ON stock_movements (dispensation_item_id);


-- Receipt header (editable under Settings).
INSERT INTO app_settings (key, value, description) VALUES
    ('pharmacy.name', '"PharmaStock Pharmacy"', 'Pharmacy name printed on receipts'),
    ('pharmacy.address', '""', 'Address printed on receipts'),
    ('pharmacy.phone', '""', 'Phone number printed on receipts');
