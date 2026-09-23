# ============================================================
# BARCODE SCANNING
# ============================================================
# POST /barcode/lookup resolves a scanned code (plain GTIN or GS1
# DataMatrix / GS1-128) to the medicine, the matching batch if the code
# carries a lot number, open purchase-order lines to receive against, and
# warnings (expired, on hold, expiry on the pack differs from the record).
# Fields the code does not carry are simply absent.

from datetime import date

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..database import get_db
from ..security import CurrentUser, require
from ..services import barcode, inventory, stock

router = APIRouter(tags=["Barcode"])


class Scan(BaseModel):
    code: str = Field(min_length=1, max_length=300)
    location_id: int | None = None


@router.post("/barcode/parse")
def parse_code(body: Scan, user: CurrentUser = Depends(require("inventory.read"))):
    try:
        return barcode.parse(body.code)
    except barcode.BarcodeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/barcode/lookup")
def lookup(body: Scan, user: CurrentUser = Depends(require("inventory.read")),
           conn: psycopg.Connection = Depends(get_db)):
    try:
        parsed = barcode.parse(body.code)
    except barcode.BarcodeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    gtin = parsed.get("gtin") or parsed.get("content_gtin")
    medicine = conn.execute(
        """
        SELECT id, name, strength, dosage_form, generic_name, brand_name, gtin, is_active, selling_price
        FROM medicines WHERE gtin = %s
        """,
        (gtin,),
    ).fetchone()
    result = {"parsed": parsed, "medicine": medicine, "batches": [], "matched_batch": None,
              "fefo_batch": None, "open_order_items": [], "warnings": []}
    if medicine is None:
        result["warnings"].append("No medicine is registered with this GTIN. Add the GTIN to the medicine record.")
        return result
    if not medicine["is_active"]:
        result["warnings"].append("This medicine is marked inactive (discontinued).")

    batches = inventory.batches(conn, medicine_id=medicine["id"], location_id=body.location_id,
                                include_empty=False)
    result["batches"] = batches
    fefo = stock.fefo_batches(conn, medicine["id"], body.location_id)
    result["fefo_batch"] = fefo[0] if fefo else None

    lot = parsed.get("batch_number")
    if lot:
        matches = [b for b in inventory.batches(conn, medicine_id=medicine["id"], location_id=body.location_id)
                   if b["batch_number"].lower() == lot.lower()]
        if matches:
            match = matches[0]
            result["matched_batch"] = match
            if parsed.get("expiry_date") and parsed["expiry_date"] != match["expiry_date"]:
                result["warnings"].append(
                    f"Expiry on the pack ({parsed['expiry_date']}) differs from the recorded expiry "
                    f"({match['expiry_date']}). Check the pack and correct the batch record.")
            if match["batch_status"] != "ACTIVE":
                result["warnings"].append(f"Batch {match['batch_number']} is {match['batch_status'].lower()}: "
                                          "do not dispense.")
            if match["quantity"] == 0:
                result["warnings"].append(f"Batch {match['batch_number']} has no recorded stock here.")
            fefo_batch = result["fefo_batch"]
            if fefo_batch and fefo_batch["batch_id"] != match["batch_id"] \
                    and fefo_batch["expiry_date"] < match["expiry_date"]:
                result["warnings"].append(
                    f"FEFO: batch {fefo_batch['batch_number']} expires earlier ({fefo_batch['expiry_date']}) "
                    "and should be used first.")
        else:
            result["warnings"].append(f"Batch {lot} is not on record yet (receive it to create it).")

    if parsed.get("expiry_date") and parsed["expiry_date"] < date.today():
        result["warnings"].append(f"The pack expired on {parsed['expiry_date']}: do not dispense or receive.")

    result["open_order_items"] = conn.execute(
        """
        SELECT poi.id AS purchase_order_item_id, po.id AS purchase_order_id, po.order_number,
               suppliers.name AS supplier, poi.quantity_ordered, poi.quantity_received,
               poi.quantity_ordered - poi.quantity_received AS outstanding
        FROM purchase_order_items poi
        JOIN purchase_orders po ON po.id = poi.purchase_order_id
        JOIN suppliers ON suppliers.id = po.supplier_id
        WHERE poi.medicine_id = %s AND po.status IN ('ORDERED', 'PARTIALLY_RECEIVED')
          AND poi.quantity_received < poi.quantity_ordered
        ORDER BY po.order_date, po.id
        """,
        (medicine["id"],),
    ).fetchall()
    return result
