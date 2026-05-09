"""Product scoring model — rule-based scoring card for Phase 1.

Phase 2 upgrade: replace with LightGBM ranking model (ADR-003).

Scoring dimensions:
1. Sales potential (weight: 0.30)
2. Margin space (weight: 0.25)
3. Competition scarcity (weight: 0.20)
4. Season match (weight: 0.15)
5. Inventory health (weight: 0.10)
"""

from typing import Dict, List
from datetime import datetime
import pandas as pd
import numpy as np

from app.models.feature_engine import MockFeatureExtractor
from app.services.data_service import MockDataService


class ProductScoringModel:
    """Rule-based scoring for Phase 1 MVP. Phase 2: XGBoost."""

    # Weights tuned per business intuition (Phase 2: trained via XGBoost)
    WEIGHTS = {
        "sales_potential": 0.30,
        "margin_space": 0.25,
        "competition_scarcity": 0.20,
        "season_match": 0.15,
        "inventory_health": 0.10,
    }

    def __init__(self, feature_extractor: MockFeatureExtractor | None = None):
        self.fe = feature_extractor or MockFeatureExtractor()
        self.ds = MockDataService()

    def score(self, district_id: int, sku_ids: List[str]) -> pd.DataFrame:
        """Score products and return ranked list with explanations."""
        features = self.fe.extract(district_id, sku_ids)
        if features.empty:
            return pd.DataFrame()

        scores = pd.DataFrame({"sku_id": features["sku_id"]})

        # 1. Sales potential: historical sales + CTR + CVR in this district
        scores["sales_potential"] = self._normalize(
            features.get("demand_sales_30d", 0) * 0.5
            + features.get("demand_ctr_30d", 0) * 0.3
            + features.get("demand_cvr_30d", 0) * 0.2
        )

        # 2. Margin space: gross margin adjusted by competitor price pressure
        price_gap = features.get("comp_price_gap", 0).clip(-0.5, 0.5)
        scores["margin_space"] = self._normalize(
            features.get("margin_score", 0) * 0.7
            + (0.5 + price_gap) * 0.3  # higher if priced below competitor mid
        )

        # 3. Competition scarcity: fewer competitors = higher score
        comp_count = features.get("comp_count", 0).replace(0, 1)
        scores["competition_scarcity"] = self._normalize(1 / comp_count)

        # 4. Season match: simple heuristic based on month
        scores["season_match"] = self._season_heuristic(features)

        # 5. Inventory health: enough stock but not overstocked
        scores["inventory_health"] = self._normalize(
            features.get("inventory_score", 0)
        )

        # Weighted total
        scores["total_score"] = sum(
            scores[dim] * wt for dim, wt in self.WEIGHTS.items()
        )
        scores["total_score"] = scores["total_score"].clip(0, 1).round(4)

        # Risk rating
        scores["risk_level"] = scores["total_score"].apply(self._risk_rating)

        # Merge product info
        products = self.ds.get_product_features(sku_ids)
        if not products.empty:
            scores = scores.merge(
                products[["sku_id", "name", "category", "original_price", "gross_margin", "inventory"]],
                on="sku_id", how="left"
            )

        return scores.sort_values("total_score", ascending=False).reset_index(drop=True)

    def get_top_n(
        self, district_id: int, sku_ids: List[str], n: int = 20
    ) -> List[dict]:
        """Return top-N products as Agent Output-ready list."""
        df = self.score(district_id, sku_ids)
        if df.empty:
            return []

        top = df.head(n)
        results = []
        for _, row in top.iterrows():
            results.append({
                "sku_id": row["sku_id"],
                "name": row.get("name", row["sku_id"]),
                "category": row.get("category", ""),
                "recommend_score": round(row["total_score"], 4),
                "expected_gmv": round(row.get("demand_sales_30d", 0) * row.get("original_price", 0), 2),
                "risk_level": row["risk_level"],
                "price": row.get("original_price", 0),
                "gross_margin": row.get("gross_margin", 0),
                "inventory": int(row.get("inventory", 0)),
            })
        return results

    # ── Helpers ──

    @staticmethod
    def _normalize(series):
        """Min-max normalize to [0, 1]. Handles both Series and scalar."""
        if isinstance(series, (int, float, np.integer, np.floating)):
            return float(series)
        mn, mx = series.min(), series.max()
        if mx == mn:
            return pd.Series(0.5, index=series.index)
        return (series - mn) / (mx - mn)

    @staticmethod
    def _risk_rating(score: float) -> str:
        if score >= 0.7:
            return "low"
        elif score >= 0.4:
            return "medium"
        return "high"

    @staticmethod
    def _season_heuristic(features: pd.DataFrame) -> pd.Series:
        """Simple season matching heuristic. Phase 2: use actual seasonality model."""
        season_score = pd.Series(0.5, index=features.index)

        # Extract scalar month value from first row (all rows have same scenario_month)
        month_val = features["scenario_month"].iloc[0] if "scenario_month" in features.columns else datetime.utcnow().month
        if isinstance(month_val, (pd.Series, np.ndarray)):
            month_val = month_val.item() if hasattr(month_val, 'item') else int(month_val)

        categories = features.get("category", pd.Series([""] * len(features)))

        # Summer: beverages, fruit, alcohol
        if month_val in [6, 7, 8]:
            summer_mask = categories.isin(["饮料", "水果", "酒类"])
            season_score[summer_mask] = 0.8
        # Winter: fast food, bakery, dairy
        elif month_val in [12, 1, 2]:
            winter_mask = categories.isin(["速食", "烘焙", "乳制品"])
            season_score[winter_mask] = 0.8
        # Spring: snacks, fruit
        elif month_val in [3, 4, 5]:
            spring_mask = categories.isin(["零食", "水果"])
            season_score[spring_mask] = 0.7

        return season_score
