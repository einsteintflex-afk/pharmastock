# ============================================================
# DASHBOARD AND ANALYTICS
# ============================================================
# GET /slow-moving-products and GET /consumption keep their original fields.

import psycopg
from fastapi import APIRouter, Depends, Query

from ..database import get_db
from ..security import CurrentUser, require, require_feature
from ..services import analytics, intelligence, trends

router = APIRouter(tags=["Analytics"])


@router.get("/dashboard")
def dashboard(user: CurrentUser = Depends(require("analytics.read")),
              conn: psycopg.Connection = Depends(get_db)):
    return analytics.dashboard(conn)


@router.get("/slow-moving-products")
def slow_moving_products(user: CurrentUser = Depends(require("analytics.read")),
                         conn: psycopg.Connection = Depends(get_db)):
    rows = analytics.consumption_summary(conn)
    rows.sort(key=lambda r: (r["units_dispensed_last_90_days"], r["medicine"]))
    return [
        {
            "medicine_id": r["medicine_id"],
            "medicine": r["medicine"],
            "strength": r["strength"],
            "dosage_form": r["dosage_form"],
            "units_dispensed_last_90_days": r["units_dispensed_last_90_days"],
            "dispensing_transactions_last_90_days": r["dispensing_transactions_last_90_days"],
            "status": r["movement_class"],
            "usable_stock": r["usable_stock"],
            "days_of_stock": r["days_of_stock"],
            "last_dispensed": r["last_dispensed"],
        }
        for r in rows
    ]


@router.get("/consumption")
def consumption(user: CurrentUser = Depends(require("analytics.read")),
                conn: psycopg.Connection = Depends(get_db)):
    rows = analytics.consumption_summary(conn)
    rows.sort(key=lambda r: (-r["units_dispensed_last_30_days"], r["medicine"]))
    return [
        {
            "medicine_id": r["medicine_id"],
            "medicine": r["medicine"],
            "strength": r["strength"],
            "dosage_form": r["dosage_form"],
            "units_dispensed_last_30_days": r["units_dispensed_last_30_days"],
            # Original fields: the 30-day averages.
            "average_daily_consumption": round(r["units_dispensed_last_30_days"] / 30, 2),
            "average_weekly_consumption": round(r["units_dispensed_last_30_days"] / (30 / 7), 2),
            "units_dispensed_last_90_days": r["units_dispensed_last_90_days"],
            "units_dispensed_previous_30_days": r["units_dispensed_previous_30_days"],
            "trend": r["trend"],
            "trend_change_percent": r["trend_change_percent"],
            "movement_class": r["movement_class"],
        }
        for r in rows
    ]


@router.get("/analytics/consumption")
def consumption_analysis(user: CurrentUser = Depends(require("analytics.read")),
                         conn: psycopg.Connection = Depends(get_db)):
    rows = analytics.consumption_summary(conn)
    return {
        "medicines": rows,
        "top_moving": sorted([r for r in rows if r["units_dispensed_last_90_days"] > 0],
                             key=lambda r: -r["units_dispensed_last_90_days"])[:10],
        "slow_moving": [r for r in rows if r["movement_class"] != "ACTIVE"],
    }


@router.get("/analytics/reorder")
def reorder(only_needed: bool = False, user: CurrentUser = Depends(require("analytics.read")),
            conn: psycopg.Connection = Depends(get_db)):
    return analytics.reorder_recommendations(conn, only_needed=only_needed)


@router.get("/analytics/expiry-risk")
def expiry_risk(include_low: bool = True, user: CurrentUser = Depends(require("analytics.read")),
                conn: psycopg.Connection = Depends(get_db)):
    rows = analytics.expiry_risk(conn, include_normal=include_low)
    at_risk = [r for r in rows if r["risk_level"] != "LOW"]
    return {
        "summary": {
            "batches_at_risk": len(at_risk),
            "units_at_risk": sum(r["projected_units_at_risk"] for r in at_risk),
            "value_at_risk": round(sum(r["value_at_risk"] or 0 for r in at_risk), 2),
            "batches_without_cost": sum(1 for r in at_risk if r["unit_cost"] is None),
        },
        "batches": rows,
    }


@router.get("/analytics/valuation")
def valuation(user: CurrentUser = Depends(require("analytics.read")),
              conn: psycopg.Connection = Depends(get_db)):
    return analytics.valuation(conn)


@router.get("/analytics/forecast")
def forecast(horizon_days: int = Query(default=30, ge=7, le=180),
             user: CurrentUser = Depends(require_feature("advanced_analytics", "analytics.read")),
             conn: psycopg.Connection = Depends(get_db)):
    return analytics.forecast(conn, horizon_days)


@router.get("/analytics/turnover")
def turnover(user: CurrentUser = Depends(require_feature("advanced_analytics", "analytics.read")),
             conn: psycopg.Connection = Depends(get_db)):
    return analytics.turnover(conn)


@router.get("/analytics/purchasing")
def purchasing(user: CurrentUser = Depends(require_feature("advanced_analytics", "analytics.read")),
               conn: psycopg.Connection = Depends(get_db)):
    return analytics.purchasing_patterns(conn)


@router.get("/analytics/stock-trends")
def stock_trends(months: int = Query(default=12, ge=1, le=36),
                 user: CurrentUser = Depends(require("analytics.read")),
                 conn: psycopg.Connection = Depends(get_db)):
    return trends.stock_trends(conn, months)


@router.get("/analytics/expiry-trends")
def expiry_trends(months: int = Query(default=12, ge=1, le=36),
                  user: CurrentUser = Depends(require("analytics.read")),
                  conn: psycopg.Connection = Depends(get_db)):
    return trends.expiry_trends(conn, months)


@router.get("/analytics/locations")
def location_analytics(user: CurrentUser = Depends(require("analytics.read")),
                       conn: psycopg.Connection = Depends(get_db)):
    return trends.location_analytics(conn)


@router.get("/analytics/stockouts")
def stockouts(days: int = Query(default=90, ge=7, le=365),
              user: CurrentUser = Depends(require_feature("advanced_analytics", "analytics.read")),
              conn: psycopg.Connection = Depends(get_db)):
    return trends.stockouts(conn, days)


@router.get("/analytics/suppliers")
def supplier_performance(days: int = Query(default=365, ge=30, le=1825),
                         user: CurrentUser = Depends(require_feature("advanced_analytics", "analytics.read")),
                         conn: psycopg.Connection = Depends(get_db)):
    return trends.supplier_performance(conn, days)


@router.get("/analytics/purchasing-trends")
def purchasing_trends(months: int = Query(default=12, ge=1, le=36),
                      user: CurrentUser = Depends(require_feature("advanced_analytics", "analytics.read")),
                      conn: psycopg.Connection = Depends(get_db)):
    return trends.purchasing_trends(conn, months)


# ------------------------------------------------------------
# Command center: what needs attention, daily brief, movers, search
# ------------------------------------------------------------

@router.get("/attention")
def attention(user: CurrentUser = Depends(require("inventory.read")), conn: psycopg.Connection = Depends(get_db)):
    return intelligence.attention(conn, user)


@router.get("/daily-brief")
def daily_brief(user: CurrentUser = Depends(require("analytics.read")), conn: psycopg.Connection = Depends(get_db)):
    return intelligence.daily_brief(conn, user)


@router.get("/analytics/movers")
def movers(dead_days: int = Query(default=180, ge=30, le=730),
           user: CurrentUser = Depends(require("analytics.read")), conn: psycopg.Connection = Depends(get_db)):
    return intelligence.movers(conn, dead_days)


@router.get("/analytics/stockout-risk")
def stockout_risk(days: int = Query(default=7, ge=1, le=90),
                  user: CurrentUser = Depends(require("analytics.read")), conn: psycopg.Connection = Depends(get_db)):
    return intelligence.stockout_risk(conn, days)


@router.get("/search")
def global_search(q: str = Query(min_length=2, max_length=100), user: CurrentUser = Depends(require("inventory.read")),
                  conn: psycopg.Connection = Depends(get_db)):
    return intelligence.search(conn, user, q)
