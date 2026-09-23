from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
import psycopg
from dotenv import load_dotenv


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set in the .env file")


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="PharmaStock API",
    description="Pharmacy inventory management and stock intelligence system",
    version="1.1.0"
)


# ============================================================
# MODELS
# ============================================================

class Medicine(BaseModel):
    id: int
    name: str
    strength: str
    dosage_form: str
    reorder_level: int


class MedicineCreate(BaseModel):
    name: str
    strength: str
    dosage_form: str
    reorder_level: int = 20


class InventoryItem(BaseModel):
    medicine: str
    strength: str
    dosage_form: str
    batch_number: str
    quantity: int
    expiry_date: str
    days_until_expiry: int
    status: str


class BatchCreate(BaseModel):
    medicine_id: int
    batch_number: str
    quantity: int
    expiry_date: str


class StockMovementCreate(BaseModel):
    batch_id: int
    movement_type: str
    quantity: int
    reason: str | None = None


# ============================================================
# PURCHASING MODELS
# ============================================================

class SupplierCreate(BaseModel):
    name: str
    contact_person: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None


class Supplier(BaseModel):
    id: int
    name: str
    contact_person: str | None
    phone: str | None
    email: str | None
    address: str | None
    is_active: bool


class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    order_number: str
    notes: str | None = None


class PurchaseOrderItemCreate(BaseModel):
    medicine_id: int
    quantity_ordered: int
    unit_cost: float


class PurchaseReceiptCreate(BaseModel):
    purchase_order_item_id: int
    batch_number: str
    quantity_received: int
    expiry_date: str
    received_by: str | None = None
    notes: str | None = None


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():
    return {
        "message": "PharmaStock API is running",
        "version": "1.1.0"
    }


# ============================================================
# MEDICINES
# ============================================================

@app.get("/medicines", response_model=list[Medicine])
def get_medicines():

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                id,
                name,
                strength,
                dosage_form,
                reorder_level
            FROM medicines
            ORDER BY id;
        """)

        rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "name": row[1],
                "strength": row[2],
                "dosage_form": row[3],
                "reorder_level": row[4]
            }
            for row in rows
        ]

    finally:
        connection.close()


# ============================================================
# CREATE MEDICINE
# ============================================================

@app.post("/medicines", response_model=Medicine)
def create_medicine(medicine: MedicineCreate):

    if medicine.reorder_level < 0:
        raise HTTPException(
            status_code=400,
            detail="Reorder level cannot be negative"
        )

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            INSERT INTO medicines
                (
                    name,
                    strength,
                    dosage_form,
                    reorder_level
                )
            VALUES
                (%s, %s, %s, %s)

            RETURNING
                id,
                name,
                strength,
                dosage_form,
                reorder_level;
        """, (
            medicine.name,
            medicine.strength,
            medicine.dosage_form,
            medicine.reorder_level
        ))

        row = cursor.fetchone()

        connection.commit()

        return {
            "id": row[0],
            "name": row[1],
            "strength": row[2],
            "dosage_form": row[3],
            "reorder_level": row[4]
        }

    finally:
        connection.close()

# ============================================================
# UPDATE MEDICINE
# ============================================================

@app.put("/medicines/{medicine_id}", response_model=Medicine)
def update_medicine(medicine_id: int, medicine: MedicineCreate):

    connection = psycopg.connect(DATABASE_URL)

    try:

        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE medicines
            SET
                name = %s,
                strength = %s,
                dosage_form = %s,
                reorder_level = %s
            WHERE id = %s
            RETURNING
                id,
                name,
                strength,
                dosage_form,
                reorder_level;
            """,
            (
                medicine.name,
                medicine.strength,
                medicine.dosage_form,
                medicine.reorder_level,
                medicine_id
            )
        )

        row = cursor.fetchone()

        if row is None:

            raise HTTPException(
                status_code=404,
                detail="Medicine not found"
            )

        connection.commit()

        return {
            "id": row[0],
            "name": row[1],
            "strength": row[2],
            "dosage_form": row[3],
            "reorder_level": row[4]
        }

    except HTTPException:
        raise

    except Exception as e:

        connection.rollback()

        print(
            "UPDATE MEDICINE ERROR:",
            repr(e)
        )

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    finally:

        connection.close()


# ============================================================
# INVENTORY
# ============================================================
# ============================================================
# INVENTORY
# ============================================================

@app.get("/inventory", response_model=list[InventoryItem])
def get_inventory():

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                medicines.name,
                medicines.strength,
                medicines.dosage_form,
                batches.batch_number,
                batches.quantity,
                batches.expiry_date,

                batches.expiry_date - CURRENT_DATE
                    AS days_until_expiry,

                CASE
                    WHEN batches.expiry_date < CURRENT_DATE
                        THEN 'EXPIRED'

                    WHEN batches.expiry_date <= CURRENT_DATE + 30
                        THEN 'URGENT'

                    WHEN batches.expiry_date <= CURRENT_DATE + 90
                        THEN 'APPROACHING EXPIRY'

                    ELSE 'NORMAL'
                END AS status

            FROM medicines

            JOIN batches
                ON medicines.id = batches.medicine_id

            ORDER BY batches.expiry_date;
        """)

        rows = cursor.fetchall()

        return [
            {
                "medicine": row[0],
                "strength": row[1],
                "dosage_form": row[2],
                "batch_number": row[3],
                "quantity": row[4],
                "expiry_date": str(row[5]),
                "days_until_expiry": row[6],
                "status": row[7]
            }
            for row in rows
        ]

    finally:
        connection.close()


# ============================================================
# CREATE BATCH
# ============================================================

@app.post("/batches")
def create_batch(batch: BatchCreate):

    if batch.quantity < 0:
        raise HTTPException(
            status_code=400,
            detail="Quantity cannot be negative"
        )

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT id
            FROM medicines
            WHERE id = %s;
        """, (batch.medicine_id,))

        medicine = cursor.fetchone()

        if medicine is None:
            raise HTTPException(
                status_code=404,
                detail="Medicine not found"
            )

        cursor.execute("""
            INSERT INTO batches
                (
                    medicine_id,
                    batch_number,
                    quantity,
                    expiry_date
                )
            VALUES
                (%s, %s, %s, %s)

            RETURNING
                id,
                medicine_id,
                batch_number,
                quantity,
                expiry_date;
        """, (
            batch.medicine_id,
            batch.batch_number,
            batch.quantity,
            batch.expiry_date
        ))

        row = cursor.fetchone()

        connection.commit()

        return {
            "id": row[0],
            "medicine_id": row[1],
            "batch_number": row[2],
            "quantity": row[3],
            "expiry_date": str(row[4])
        }

    finally:
        connection.close()


# ============================================================
# LOW STOCK ALERTS
# ============================================================

@app.get("/stock-alerts")
def get_stock_alerts():

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                medicines.id,
                medicines.name,
                medicines.strength,
                medicines.dosage_form,

                COALESCE(
                    SUM(batches.quantity),
                    0
                ) AS current_stock,

                medicines.reorder_level

            FROM medicines

            LEFT JOIN batches
                ON medicines.id = batches.medicine_id

            GROUP BY
                medicines.id,
                medicines.name,
                medicines.strength,
                medicines.dosage_form,
                medicines.reorder_level

            ORDER BY medicines.name;
        """)

        rows = cursor.fetchall()

        alerts = []

        for row in rows:

            current_stock = row[4]
            reorder_level = row[5]

            if current_stock <= reorder_level:
                status = "LOW STOCK"
            else:
                status = "NORMAL"

            alerts.append({
                "medicine": row[1],
                "strength": row[2],
                "dosage_form": row[3],
                "current_stock": current_stock,
                "reorder_level": reorder_level,
                "status": status
            })

        return alerts

    finally:
        connection.close()


# ============================================================
# CREATE STOCK MOVEMENT
# ============================================================

@app.post("/stock-movements")
def create_stock_movement(
    movement: StockMovementCreate
):

    if movement.quantity <= 0:
        raise HTTPException(
            status_code=400,
            detail="Quantity must be greater than zero"
        )

    movement_type = movement.movement_type.upper()

    allowed_types = [
        "RECEIVED",
        "DISPENSED",
        "RETURNED",
        "DAMAGED",
        "EXPIRED",
        "ADJUSTMENT"
    ]

    if movement_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Invalid movement type",
                "allowed_types": allowed_types
            }
        )

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                id,
                quantity
            FROM batches
            WHERE id = %s;
        """, (movement.batch_id,))

        batch = cursor.fetchone()

        if batch is None:
            raise HTTPException(
                status_code=404,
                detail="Batch not found"
            )

        current_quantity = batch[1]

        if movement_type in ["RECEIVED", "RETURNED"]:

            new_quantity = (
                current_quantity +
                movement.quantity
            )

        elif movement_type in [
            "DISPENSED",
            "DAMAGED",
            "EXPIRED"
        ]:

            new_quantity = (
                current_quantity -
                movement.quantity
            )

            if new_quantity < 0:
                raise HTTPException(
                    status_code=400,
                    detail="Insufficient stock"
                )

        elif movement_type == "ADJUSTMENT":

            new_quantity = movement.quantity

        cursor.execute("""
            INSERT INTO stock_movements
                (
                    batch_id,
                    movement_type,
                    quantity,
                    reason
                )
            VALUES
                (%s, %s, %s, %s)

            RETURNING
                id,
                batch_id,
                movement_type,
                quantity,
                movement_date,
                reason;
        """, (
            movement.batch_id,
            movement_type,
            movement.quantity,
            movement.reason
        ))

        movement_row = cursor.fetchone()

        cursor.execute("""
            UPDATE batches
            SET quantity = %s
            WHERE id = %s;
        """, (
            new_quantity,
            movement.batch_id
        ))

        connection.commit()

        return {
            "id": movement_row[0],
            "batch_id": movement_row[1],
            "movement_type": movement_row[2],
            "quantity": movement_row[3],
            "movement_date": str(movement_row[4]),
            "reason": movement_row[5],
            "new_batch_quantity": new_quantity
        }

    finally:
        connection.close()


# ============================================================
# STOCK MOVEMENT HISTORY
# ============================================================

@app.get("/stock-movements")
def get_stock_movements():

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                stock_movements.id,
                stock_movements.batch_id,

                medicines.name,
                medicines.strength,
                medicines.dosage_form,

                batches.batch_number,

                stock_movements.movement_type,
                stock_movements.quantity,
                stock_movements.movement_date,
                stock_movements.reason

            FROM stock_movements

            JOIN batches
                ON stock_movements.batch_id = batches.id

            JOIN medicines
                ON batches.medicine_id = medicines.id

            ORDER BY
                stock_movements.movement_date DESC;
        """)

        rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "batch_id": row[1],
                "medicine": row[2],
                "strength": row[3],
                "dosage_form": row[4],
                "batch_number": row[5],
                "movement_type": row[6],
                "quantity": row[7],
                "movement_date": str(row[8]),
                "reason": row[9]
            }
            for row in rows
        ]

    finally:
        connection.close()


# ============================================================
# SLOW-MOVING PRODUCTS
# ============================================================

@app.get("/slow-moving-products")
def get_slow_moving_products():

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                medicines.id,
                medicines.name,
                medicines.strength,
                medicines.dosage_form,

                COALESCE(
                    SUM(
                        CASE
                            WHEN stock_movements.movement_type
                                = 'DISPENSED'
                            THEN stock_movements.quantity
                            ELSE 0
                        END
                    ),
                    0
                ) AS units_dispensed,

                COUNT(
                    CASE
                        WHEN stock_movements.movement_type
                            = 'DISPENSED'
                        THEN 1
                    END
                ) AS dispensing_transactions

            FROM medicines

            LEFT JOIN batches
                ON medicines.id = batches.medicine_id

            LEFT JOIN stock_movements
                ON batches.id = stock_movements.batch_id
                AND stock_movements.movement_date
                    >= CURRENT_TIMESTAMP
                    - INTERVAL '90 days'

            GROUP BY
                medicines.id,
                medicines.name,
                medicines.strength,
                medicines.dosage_form

            ORDER BY
                units_dispensed ASC;
        """)

        rows = cursor.fetchall()

        products = []

        for row in rows:

            units_dispensed = row[4]
            transactions = row[5]

            if units_dispensed == 0:
                status = "NO MOVEMENT"

            elif units_dispensed <= 10:
                status = "SLOW-MOVING"

            else:
                status = "ACTIVE"

            products.append({
                "medicine_id": row[0],
                "medicine": row[1],
                "strength": row[2],
                "dosage_form": row[3],
                "units_dispensed_last_90_days": units_dispensed,
                "dispensing_transactions_last_90_days": transactions,
                "status": status
            })

        return products

    finally:
        connection.close()


# ============================================================
# CONSUMPTION ANALYTICS
# ============================================================

@app.get("/consumption")
def get_consumption():

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                medicines.id,
                medicines.name,
                medicines.strength,
                medicines.dosage_form,

                COALESCE(
                    SUM(
                        CASE
                            WHEN stock_movements.movement_type
                                = 'DISPENSED'
                            THEN stock_movements.quantity
                            ELSE 0
                        END
                    ),
                    0
                ) AS units_dispensed

            FROM medicines

            LEFT JOIN batches
                ON medicines.id = batches.medicine_id

            LEFT JOIN stock_movements
                ON batches.id = stock_movements.batch_id
                AND stock_movements.movement_date
                    >= CURRENT_TIMESTAMP
                    - INTERVAL '30 days'

            GROUP BY
                medicines.id,
                medicines.name,
                medicines.strength,
                medicines.dosage_form

            ORDER BY
                units_dispensed DESC;
        """)

        rows = cursor.fetchall()

        consumption = []

        for row in rows:

            units = row[4]

            average_daily = round(
                units / 30,
                2
            )

            average_weekly = round(
                units / 4.2857,
                2
            )

            consumption.append({
                "medicine_id": row[0],
                "medicine": row[1],
                "strength": row[2],
                "dosage_form": row[3],
                "units_dispensed_last_30_days": units,
                "average_daily_consumption": average_daily,
                "average_weekly_consumption": average_weekly
            })

        return consumption

    finally:
        connection.close()


# ============================================================
# SUPPLIERS
# ============================================================

@app.post("/suppliers", response_model=Supplier)
def create_supplier(supplier: SupplierCreate):

    if not supplier.name.strip():
        raise HTTPException(
            status_code=400,
            detail="Supplier name is required"
        )

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            INSERT INTO suppliers
                (
                    name,
                    contact_person,
                    phone,
                    email,
                    address
                )
            VALUES
                (%s, %s, %s, %s, %s)

            RETURNING
                id,
                name,
                contact_person,
                phone,
                email,
                address,
                is_active;
        """, (
            supplier.name,
            supplier.contact_person,
            supplier.phone,
            supplier.email,
            supplier.address
        ))

        row = cursor.fetchone()

        connection.commit()

        return {
            "id": row[0],
            "name": row[1],
            "contact_person": row[2],
            "phone": row[3],
            "email": row[4],
            "address": row[5],
            "is_active": row[6]
        }

    finally:
        connection.close()


# ============================================================
# GET SUPPLIERS
# ============================================================

@app.get("/suppliers", response_model=list[Supplier])
def get_suppliers():

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                id,
                name,
                contact_person,
                phone,
                email,
                address,
                is_active
            FROM suppliers
            ORDER BY name;
        """)

        rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "name": row[1],
                "contact_person": row[2],
                "phone": row[3],
                "email": row[4],
                "address": row[5],
                "is_active": row[6]
            }
            for row in rows
        ]

    finally:
        connection.close()


# ============================================================
# CREATE PURCHASE ORDER
# ============================================================

@app.post("/purchase-orders")
def create_purchase_order(order: PurchaseOrderCreate):

    if not order.order_number.strip():
        raise HTTPException(
            status_code=400,
            detail="Order number is required"
        )

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        # Check supplier
        cursor.execute("""
            SELECT
                id,
                name,
                is_active
            FROM suppliers
            WHERE id = %s;
        """, (order.supplier_id,))

        supplier = cursor.fetchone()

        if supplier is None:
            raise HTTPException(
                status_code=404,
                detail="Supplier not found"
            )

        if not supplier[2]:
            raise HTTPException(
                status_code=400,
                detail="Supplier is inactive"
            )

        # Create purchase order
        cursor.execute("""
            INSERT INTO purchase_orders
                (
                    supplier_id,
                    order_number,
                    notes
                )
            VALUES
                (%s, %s, %s)

            RETURNING
                id,
                supplier_id,
                order_number,
                order_date,
                status,
                notes,
                created_at;
        """, (
            order.supplier_id,
            order.order_number,
            order.notes
        ))

        row = cursor.fetchone()

        connection.commit()

        return {
            "id": row[0],
            "supplier_id": row[1],
            "order_number": row[2],
            "order_date": str(row[3]),
            "status": row[4],
            "notes": row[5],
            "created_at": str(row[6])
        }

    except psycopg.errors.UniqueViolation:
        connection.rollback()

        raise HTTPException(
            status_code=400,
            detail="Purchase order number already exists"
        )

    finally:
        connection.close()


# ============================================================
# GET PURCHASE ORDERS
# ============================================================

@app.get("/purchase-orders")
def get_purchase_orders():

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                purchase_orders.id,
                purchase_orders.supplier_id,
                suppliers.name,
                purchase_orders.order_number,
                purchase_orders.order_date,
                purchase_orders.status,
                purchase_orders.notes,
                purchase_orders.created_at

            FROM purchase_orders

            JOIN suppliers
                ON purchase_orders.supplier_id = suppliers.id

            ORDER BY
                purchase_orders.id DESC;
        """)

        rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "supplier_id": row[1],
                "supplier": row[2],
                "order_number": row[3],
                "order_date": str(row[4]),
                "status": row[5],
                "notes": row[6],
                "created_at": str(row[7])
            }
            for row in rows
        ]

    finally:
        connection.close()


# ============================================================
# ADD ITEM TO PURCHASE ORDER
# ============================================================

@app.post("/purchase-orders/{purchase_order_id}/items")
def add_purchase_order_item(
    purchase_order_id: int,
    item: PurchaseOrderItemCreate
):

    if item.quantity_ordered <= 0:
        raise HTTPException(
            status_code=400,
            detail="Quantity ordered must be greater than zero"
        )

    if item.unit_cost < 0:
        raise HTTPException(
            status_code=400,
            detail="Unit cost cannot be negative"
        )

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        # Check purchase order
        cursor.execute("""
            SELECT
                id,
                status
            FROM purchase_orders
            WHERE id = %s;
        """, (purchase_order_id,))

        order = cursor.fetchone()

        if order is None:
            raise HTTPException(
                status_code=404,
                detail="Purchase order not found"
            )

        if order[1] in ["RECEIVED", "CANCELLED"]:
            raise HTTPException(
                status_code=400,
                detail="Items cannot be added to a received or cancelled order"
            )

        # Check medicine
        cursor.execute("""
            SELECT
                id,
                name,
                strength,
                dosage_form
            FROM medicines
            WHERE id = %s;
        """, (item.medicine_id,))

        medicine = cursor.fetchone()

        if medicine is None:
            raise HTTPException(
                status_code=404,
                detail="Medicine not found"
            )

        # Add item
        cursor.execute("""
            INSERT INTO purchase_order_items
                (
                    purchase_order_id,
                    medicine_id,
                    quantity_ordered,
                    unit_cost
                )
            VALUES
                (%s, %s, %s, %s)

            RETURNING
                id,
                purchase_order_id,
                medicine_id,
                quantity_ordered,
                unit_cost,
                quantity_received;
        """, (
            purchase_order_id,
            item.medicine_id,
            item.quantity_ordered,
            item.unit_cost
        ))

        row = cursor.fetchone()

        # Change DRAFT to ORDERED when first item is added
        if order[1] == "DRAFT":

            cursor.execute("""
                UPDATE purchase_orders
                SET status = 'ORDERED'
                WHERE id = %s;
            """, (purchase_order_id,))

        connection.commit()

        return {
            "id": row[0],
            "purchase_order_id": row[1],
            "medicine_id": row[2],
            "medicine": medicine[1],
            "strength": medicine[2],
            "dosage_form": medicine[3],
            "quantity_ordered": row[3],
            "unit_cost": float(row[4]),
            "quantity_received": row[5]
        }

    finally:
        connection.close()


# ============================================================
# GET PURCHASE ORDER ITEMS
# ============================================================

@app.get("/purchase-orders/{purchase_order_id}/items")
def get_purchase_order_items(
    purchase_order_id: int
):

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                purchase_order_items.id,
                purchase_order_items.purchase_order_id,
                medicines.id,
                medicines.name,
                medicines.strength,
                medicines.dosage_form,
                purchase_order_items.quantity_ordered,
                purchase_order_items.unit_cost,
                purchase_order_items.quantity_received

            FROM purchase_order_items

            JOIN medicines
                ON purchase_order_items.medicine_id = medicines.id

            WHERE purchase_order_items.purchase_order_id = %s

            ORDER BY purchase_order_items.id;
        """, (purchase_order_id,))

        rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "purchase_order_id": row[1],
                "medicine_id": row[2],
                "medicine": row[3],
                "strength": row[4],
                "dosage_form": row[5],
                "quantity_ordered": row[6],
                "unit_cost": float(row[7]),
                "quantity_received": row[8],
                "quantity_remaining": row[6] - row[8]
            }
            for row in rows
        ]

    finally:
        connection.close()


# ============================================================
# RECEIVE PURCHASE ORDER ITEM
# ============================================================

@app.post("/purchase-receipts")
def receive_purchase_order_item(
    receipt: PurchaseReceiptCreate
):

    if receipt.quantity_received <= 0:
        raise HTTPException(
            status_code=400,
            detail="Quantity received must be greater than zero"
        )

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        # Lock the purchase order item during receiving
        cursor.execute("""
            SELECT
                purchase_order_items.id,
                purchase_order_items.purchase_order_id,
                purchase_order_items.medicine_id,
                purchase_order_items.quantity_ordered,
                purchase_order_items.quantity_received,
                purchase_orders.status

            FROM purchase_order_items

            JOIN purchase_orders
                ON purchase_order_items.purchase_order_id
                    = purchase_orders.id

            WHERE purchase_order_items.id = %s

            FOR UPDATE;
        """, (receipt.purchase_order_item_id,))

        item = cursor.fetchone()

        if item is None:
            raise HTTPException(
                status_code=404,
                detail="Purchase order item not found"
            )

        item_id = item[0]
        purchase_order_id = item[1]
        medicine_id = item[2]
        quantity_ordered = item[3]
        quantity_already_received = item[4]
        purchase_order_status = item[5]

        if purchase_order_status == "CANCELLED":
            raise HTTPException(
                status_code=400,
                detail="Cannot receive stock for a cancelled purchase order"
            )

        quantity_remaining = (
            quantity_ordered -
            quantity_already_received
        )

        if receipt.quantity_received > quantity_remaining:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Received quantity exceeds outstanding quantity",
                    "quantity_ordered": quantity_ordered,
                    "quantity_already_received": quantity_already_received,
                    "quantity_remaining": quantity_remaining
                }
            )

        # ----------------------------------------------------
        # Find existing batch
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                id,
                quantity,
                expiry_date
            FROM batches
            WHERE medicine_id = %s
            AND batch_number = %s
            FOR UPDATE;
        """, (
            medicine_id,
            receipt.batch_number
        ))

        existing_batch = cursor.fetchone()

        if existing_batch:

            batch_id = existing_batch[0]
            old_quantity = existing_batch[1]

            # Prevent changing an existing batch expiry date
            if str(existing_batch[2]) != receipt.expiry_date:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": "Existing batch has a different expiry date",
                        "existing_expiry_date": str(existing_batch[2]),
                        "provided_expiry_date": receipt.expiry_date
                    }
                )

            new_batch_quantity = (
                old_quantity +
                receipt.quantity_received
            )

            cursor.execute("""
                UPDATE batches
                SET quantity = %s
                WHERE id = %s;
            """, (
                new_batch_quantity,
                batch_id
            ))

        else:

            # Create new batch
            cursor.execute("""
                INSERT INTO batches
                    (
                        medicine_id,
                        batch_number,
                        quantity,
                        expiry_date
                    )
                VALUES
                    (%s, %s, %s, %s)

                RETURNING id;
            """, (
                medicine_id,
                receipt.batch_number,
                receipt.quantity_received,
                receipt.expiry_date
            ))

            batch_id = cursor.fetchone()[0]

            new_batch_quantity = receipt.quantity_received

        # ----------------------------------------------------
        # Record receipt
        # ----------------------------------------------------

        cursor.execute("""
            INSERT INTO purchase_receipts
                (
                    purchase_order_item_id,
                    batch_id,
                    quantity_received,
                    received_by,
                    notes
                )
            VALUES
                (%s, %s, %s, %s, %s)

            RETURNING
                id,
                received_date;
        """, (
            item_id,
            batch_id,
            receipt.quantity_received,
            receipt.received_by,
            receipt.notes
        ))

        receipt_row = cursor.fetchone()

        # ----------------------------------------------------
        # Record stock movement
        # ----------------------------------------------------

        cursor.execute("""
            INSERT INTO stock_movements
                (
                    batch_id,
                    movement_type,
                    quantity,
                    reason
                )
            VALUES
                (%s, 'RECEIVED', %s, %s)

            RETURNING
                id,
                movement_date;
        """, (
            batch_id,
            receipt.quantity_received,
            "Purchase order received"
        ))

        movement_row = cursor.fetchone()

        # ----------------------------------------------------
        # Update purchase order item
        # ----------------------------------------------------

        new_quantity_received = (
            quantity_already_received +
            receipt.quantity_received
        )

        cursor.execute("""
            UPDATE purchase_order_items
            SET quantity_received = %s
            WHERE id = %s;
        """, (
            new_quantity_received,
            item_id
        ))

        # ----------------------------------------------------
        # Determine overall purchase order status
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                COUNT(*) AS total_items,

                COUNT(
                    CASE
                        WHEN quantity_received >= quantity_ordered
                        THEN 1
                    END
                ) AS fully_received_items,

                COALESCE(
                    SUM(quantity_received),
                    0
                ) AS total_received

            FROM purchase_order_items

            WHERE purchase_order_id = %s;
        """, (purchase_order_id,))

        order_summary = cursor.fetchone()

        total_items = order_summary[0]
        fully_received_items = order_summary[1]

        if (
            total_items > 0
            and fully_received_items == total_items
        ):

            new_order_status = "RECEIVED"

        elif (
            order_summary[2] > 0
        ):

            new_order_status = "PARTIALLY_RECEIVED"

        else:

            new_order_status = "ORDERED"

        cursor.execute("""
            UPDATE purchase_orders
            SET status = %s
            WHERE id = %s;
        """, (
            new_order_status,
            purchase_order_id
        ))

        connection.commit()

        return {
            "message": "Stock received successfully",
            "receipt_id": receipt_row[0],
            "purchase_order_item_id": item_id,
            "purchase_order_id": purchase_order_id,
            "batch_id": batch_id,
            "quantity_received": receipt.quantity_received,
            "new_batch_quantity": new_batch_quantity,
            "movement_id": movement_row[0],
            "movement_type": "RECEIVED",
            "purchase_order_status": new_order_status
        }

    except psycopg.errors.UniqueViolation:
        connection.rollback()

        raise HTTPException(
            status_code=400,
            detail="Duplicate record detected"

        )

    finally:
        connection.close()


# ============================================================
# PURCHASE RECEIPT HISTORY
# ============================================================

@app.get("/purchase-receipts")
def get_purchase_receipts():

    connection = psycopg.connect(DATABASE_URL)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                purchase_receipts.id,

                purchase_orders.id,
                purchase_orders.order_number,

                suppliers.name,

                medicines.name,
                medicines.strength,
                medicines.dosage_form,

                batches.batch_number,

                purchase_receipts.quantity_received,
                purchase_receipts.received_date,
                purchase_receipts.received_by,
                purchase_receipts.notes

            FROM purchase_receipts

            JOIN purchase_order_items
                ON purchase_receipts.purchase_order_item_id
                    = purchase_order_items.id

            JOIN purchase_orders
                ON purchase_order_items.purchase_order_id
                    = purchase_orders.id

            JOIN suppliers
                ON purchase_orders.supplier_id
                    = suppliers.id

            JOIN medicines
                ON purchase_order_items.medicine_id
                    = medicines.id

            JOIN batches
                ON purchase_receipts.batch_id
                    = batches.id

            ORDER BY
                purchase_receipts.received_date DESC;
        """)

        rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "purchase_order_id": row[1],
                "order_number": row[2],
                "supplier": row[3],
                "medicine": row[4],
                "strength": row[5],
                "dosage_form": row[6],
                "batch_number": row[7],
                "quantity_received": row[8],
                "received_date": str(row[9]),
                "received_by": row[10],
                "notes": row[11]
            }
            for row in rows
        ]

    finally:
        connection.close()

        # ============================================================
# FRONTEND
# ============================================================

app.mount(
    "/app",
    StaticFiles(directory="frontend", html=True),
    name="frontend"
)