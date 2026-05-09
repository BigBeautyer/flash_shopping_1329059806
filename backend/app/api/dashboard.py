"""Dashboard API endpoints."""

from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from app.services.data_service import MockDataService
from app.services.metrics_service import MetricsService

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

ds = MockDataService()
metrics = MetricsService(ds)


@router.get("/funnel")
def get_funnel(
    district_id: int = Query(1, description="Business district ID"),
    days: int = Query(7, description="Lookback window in days"),
):
    """Get funnel metrics for a district."""
    return metrics.compute_funnel(
        district_id,
        datetime.utcnow() - timedelta(days=days),
        datetime.utcnow(),
    )


@router.get("/snapshot")
def get_snapshot(
    district_id: int = Query(1),
    days: int = Query(7),
):
    """Get dashboard snapshot: all key metrics at once."""
    return metrics.get_dashboard_snapshot(district_id, days)


@router.get("/trend")
def get_trend(
    district_id: int = Query(1),
    days: int = Query(30),
):
    """Get daily trend data for charts."""
    return metrics.get_daily_trend(district_id, days)


@router.get("/districts")
def get_districts():
    """Get all business districts."""
    df = ds.get_all_districts()
    return df.to_dict(orient="records")


@router.get("/products")
def get_products(category: str = Query(None)):
    """Get all products, optionally filtered by category."""
    df = ds.get_all_products()
    if category:
        df = df[df["category"] == category]
    return df.to_dict(orient="records")
