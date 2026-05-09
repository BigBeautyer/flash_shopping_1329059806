"""Metrics service — single source of truth for all metric calculations.

Per metrics_definition.md: every metric is computed here. Agents and dashboards
must call this service — never compute metrics inline.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd
import numpy as np

from app.services.data_service import MockDataService


class MetricsService:
    """Compute all 12 north star metrics + 6 guardrail metrics from funnel data."""

    def __init__(self, data_service: MockDataService | None = None):
        self.ds = data_service or MockDataService()

    # ── Funnel Metrics ──

    def compute_funnel(self, district_id: int, start_date: datetime, end_date: datetime) -> dict:
        """Compute full funnel: exposure → click → cart → payment → repurchase."""
        df = self.ds.get_funnel_data(district_id, start_date, end_date)
        if df.empty:
            return self._empty_funnel()

        exposure_uv = df[df["funnel_stage"] == "exposure"]["user_id"].nunique()
        click_uv = df[df["funnel_stage"] == "click"]["user_id"].nunique()
        cart_uv = df[df["funnel_stage"] == "cart"]["user_id"].nunique()
        pay_uv = df[df["funnel_stage"] == "payment"]["user_id"].nunique()
        repurchase_uv = df[df["funnel_stage"] == "repurchase"]["user_id"].nunique()

        return {
            "exposure_uv": int(exposure_uv),
            "click_uv": int(click_uv),
            "cart_uv": int(cart_uv),
            "payment_uv": int(pay_uv),
            "repurchase_uv": int(repurchase_uv),
            "ctr": round(click_uv / exposure_uv, 4) if exposure_uv > 0 else 0,
            "cart_rate": round(cart_uv / click_uv, 4) if click_uv > 0 else 0,
            "cvr": round(pay_uv / cart_uv, 4) if cart_uv > 0 else 0,
            "repurchase_rate": round(repurchase_uv / pay_uv, 4) if pay_uv > 0 else 0,
        }

    def compute_gmv(self, district_id: int, start_date: datetime, end_date: datetime) -> float:
        """GMV = SUM(price × quantity) for payment stage."""
        df = self.ds.get_funnel_data(district_id, start_date, end_date)
        pay_df = df[df["funnel_stage"] == "payment"]
        if pay_df.empty:
            return 0.0
        return round((pay_df["price"] * pay_df["quantity"]).sum(), 2)

    def compute_aov(self, district_id: int, start_date: datetime, end_date: datetime) -> float:
        """AOV = GMV / payment_uv."""
        df = self.ds.get_funnel_data(district_id, start_date, end_date)
        pay_df = df[df["funnel_stage"] == "payment"]
        if pay_df.empty:
            return 0.0
        gmv = (pay_df["price"] * pay_df["quantity"]).sum()
        pay_uv = pay_df["user_id"].nunique()
        return round(gmv / pay_uv, 2) if pay_uv > 0 else 0.0

    def compute_roi(self, district_id: int, start_date: datetime, end_date: datetime, budget: float = 0) -> float:
        """ROI = GMV / cost. Returns 0 if budget is 0."""
        gmv = self.compute_gmv(district_id, start_date, end_date)
        if budget <= 0:
            return 0.0
        return round(gmv / budget, 2)

    def compute_gross_margin(self, district_id: int, start_date: datetime, end_date: datetime) -> float:
        """Gross margin = 1 - (total cost / total revenue)."""
        df = self.ds.get_funnel_data(district_id, start_date, end_date)
        pay_df = df[df["funnel_stage"] == "payment"]
        if pay_df.empty:
            return 0.0
        sku_ids = pay_df["sku_id"].unique().tolist()
        products = self.ds.get_product_features(sku_ids)
        if products.empty:
            return 0.0

        total_revenue = (pay_df["price"] * pay_df["quantity"]).sum()
        # Merge to get cost
        merged = pay_df.merge(products[["sku_id", "cost_price"]], on="sku_id", how="left")
        total_cost = (merged["cost_price"].fillna(0) * merged["quantity"]).sum()
        if total_revenue == 0:
            return 0.0
        return round(1 - total_cost / total_revenue, 4)

    def compute_sell_through_rate(self, district_id: int, start_date: datetime, end_date: datetime) -> float:
        """Sell-through = SKUs with sales / total selected SKUs."""
        df = self.ds.get_funnel_data(district_id, start_date, end_date)
        pay_df = df[df["funnel_stage"] == "payment"]
        all_skus = df["sku_id"].nunique()
        sold_skus = pay_df["sku_id"].nunique()
        return round(sold_skus / all_skus, 4) if all_skus > 0 else 0.0

    # ── Composite Dashboard Data ──

    def get_dashboard_snapshot(self, district_id: int, days: int = 7) -> dict:
        """Get all key metrics for dashboard rendering."""
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        funnel = self.compute_funnel(district_id, start, end)
        gmv = self.compute_gmv(district_id, start, end)
        aov = self.compute_aov(district_id, start, end)
        gm = self.compute_gross_margin(district_id, start, end)
        sell_through = self.compute_sell_through_rate(district_id, start, end)

        return {
            "period": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
            "district_id": district_id,
            "funnel": funnel,
            "gmv": gmv,
            "aov": aov,
            "gross_margin": gm,
            "sell_through_rate": sell_through,
        }

    def get_daily_trend(self, district_id: int, days: int = 30) -> list:
        """Get daily GMV and funnel data for trend chart."""
        end = datetime.utcnow()
        start = end - timedelta(days=days)
        df = self.ds.get_funnel_data(district_id, start, end)
        if df.empty:
            return []

        df["date"] = pd.to_datetime(df["created_at"]).dt.date
        trends = []
        for date, group in df.groupby("date"):
            pay = group[group["funnel_stage"] == "payment"]
            exp = group[group["funnel_stage"] == "exposure"]
            trends.append({
                "date": str(date),
                "gmv": round((pay["price"] * pay["quantity"]).sum(), 2),
                "exposure_uv": int(exp["user_id"].nunique()),
                "click_uv": int(group[group["funnel_stage"] == "click"]["user_id"].nunique()),
                "cart_uv": int(group[group["funnel_stage"] == "cart"]["user_id"].nunique()),
                "payment_uv": int(pay["user_id"].nunique()),
                "repurchase_uv": int(group[group["funnel_stage"] == "repurchase"]["user_id"].nunique()),
            })
        return sorted(trends, key=lambda x: x["date"])

    @staticmethod
    def _empty_funnel() -> dict:
        return {
            "exposure_uv": 0, "click_uv": 0, "cart_uv": 0,
            "payment_uv": 0, "repurchase_uv": 0,
            "ctr": 0, "cart_rate": 0, "cvr": 0, "repurchase_rate": 0,
        }
