"""Feature engineering for flash sale business.

5-category feature extraction (ADR-005 reference). All feature extractors
implement the FeatureExtractor base class for future data source swap (ADR-002).
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from app.services.data_service import MockDataService


class FeatureExtractor(ABC):
    """Abstract base for feature extraction. Swap implementation for real data."""

    @abstractmethod
    def extract(self, district_id: int, sku_ids: List[str]) -> pd.DataFrame:
        """Return feature matrix: rows=SKUs, columns=features."""
        ...


class MockFeatureExtractor(FeatureExtractor):
    """Extract features from mock data for rule-based scoring."""

    def __init__(self, data_service: MockDataService | None = None):
        self.ds = data_service or MockDataService()

    def extract(self, district_id: int, sku_ids: List[str]) -> pd.DataFrame:
        """Main entry: extract all 5 feature categories and merge into one matrix."""
        district = self._district_features(district_id)
        product = self._product_features(sku_ids)
        demand = self._demand_features(district_id, sku_ids)
        competitor = self._competitor_features(district_id, sku_ids)
        scenario = self._scenario_features()

        # Merge all features on sku_id
        features = product.copy()
        for feat_df in [demand, competitor]:
            if not feat_df.empty:
                features = features.merge(feat_df, on="sku_id", how="left")

        # Add district-level features as constant columns
        for k, v in district.items():
            features[f"district_{k}"] = v

        # Add scenario features
        for k, v in scenario.items():
            features[k] = v

        # Fill missing numeric values
        numeric_cols = features.select_dtypes(include=[np.number]).columns
        features[numeric_cols] = features[numeric_cols].fillna(0)

        return features

    # ── 1. Business District Features ──

    def _district_features(self, district_id: int) -> dict:
        profile = self.ds.get_district_profile(district_id)
        if not profile:
            return {}
        return {
            "lbs_heat": profile.get("lbs_heat_index", 0),
            "office_ratio": profile.get("office_residential_ratio", 0),
            "avg_consumption": profile.get("avg_consumption", 0),
            "competitor_density": profile.get("competitor_density", 0),
        }

    # ── 2. Product Features ──

    def _product_features(self, sku_ids: List[str]) -> pd.DataFrame:
        df = self.ds.get_product_features(sku_ids)
        if df.empty:
            return pd.DataFrame({"sku_id": sku_ids})

        df["margin_score"] = df["gross_margin"].clip(0, 1)
        df["inventory_score"] = 1 - (df["inventory"] / (df["inventory"].max() + 1))
        df["shelf_life_ratio"] = df["shelf_life_days"] / (df["shelf_life_days"].max() + 1)
        return df

    # ── 3. User Demand Features ──

    def _demand_features(self, district_id: int, sku_ids: List[str]) -> pd.DataFrame:
        """Compute per-SKU demand signals from recent order data."""
        end = datetime.utcnow()
        start = end - timedelta(days=30)
        df = self.ds.get_funnel_data(district_id, start, end)
        if df.empty:
            return pd.DataFrame()

        rows = []
        for sku in sku_ids:
            sku_df = df[df["sku_id"] == sku]
            pay_df = sku_df[sku_df["funnel_stage"] == "payment"]
            cart_df = sku_df[sku_df["funnel_stage"] == "cart"]
            click_df = sku_df[sku_df["funnel_stage"] == "click"]
            exp_df = sku_df[sku_df["funnel_stage"] == "exposure"]

            rows.append({
                "sku_id": sku,
                "demand_sales_30d": len(pay_df),
                "demand_cart_30d": len(cart_df),
                "demand_click_30d": len(click_df),
                "demand_exposure_30d": len(exp_df),
                "demand_ctr_30d": len(click_df) / max(len(exp_df), 1),
                "demand_cvr_30d": len(pay_df) / max(len(cart_df), 1),
            })
        return pd.DataFrame(rows)

    # ── 4. Competitor Features ──

    def _competitor_features(self, district_id: int, sku_ids: List[str]) -> pd.DataFrame:
        """Competitor pressure per category."""
        products = self.ds.get_product_features(sku_ids)
        if products.empty:
            return pd.DataFrame()

        rows = []
        # Cache competitor data by category to avoid repeated queries
        cat_cache = {}
        for _, row in products.iterrows():
            cat = row["category"]
            if cat not in cat_cache:
                comp_df = self.ds.get_competitor_data(district_id, cat)
                if comp_df.empty:
                    cat_cache[cat] = {"comp_avg_discount": 0, "comp_count": 0, "comp_price_mid": 0}
                else:
                    cat_cache[cat] = {
                        "comp_avg_discount": comp_df["discount_depth"].mean(),
                        "comp_count": len(comp_df),
                        "comp_price_mid": (comp_df["price_band_low"].mean() + comp_df["price_band_high"].mean()) / 2,
                    }

            rows.append({
                "sku_id": row["sku_id"],
                "comp_avg_discount": cat_cache[cat]["comp_avg_discount"],
                "comp_count": cat_cache[cat]["comp_count"],
                "comp_price_mid": cat_cache[cat]["comp_price_mid"],
                # Price competitiveness: how much lower/higher vs competitor mid
                "comp_price_gap": (row["original_price"] - cat_cache[cat]["comp_price_mid"])
                                  / max(cat_cache[cat]["comp_price_mid"], 1),
            })

        return pd.DataFrame(rows)

    # ── 5. Scenario Features ──

    def _scenario_features(self) -> dict:
        now = datetime.utcnow()
        return {
            "scenario_weekday": 1 if now.weekday() < 5 else 0,
            "scenario_hour": now.hour,
            "scenario_is_peak": 1 if now.hour in [11, 12, 13, 17, 18, 19] else 0,
            "scenario_month": now.month,
        }
