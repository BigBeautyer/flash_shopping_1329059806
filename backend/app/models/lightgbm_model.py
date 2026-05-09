"""LightGBM ranking model for product selection (Phase 2).

Replaces the rule-based ProductScoringModel with a trained LightGBM ranker.
Outputs feature importance for explainability (ADR-003).

Training target: composite score based on historical GMV, CVR, and margin.
Features: 5-category features from MockFeatureExtractor (~20 numeric dims).
"""

from __future__ import annotations
import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple
import numpy as np
import pandas as pd
import pickle

from app.models.feature_engine import MockFeatureExtractor
from app.services.data_service import MockDataService
from app.services.metrics_service import MetricsService
from app.core.config import settings

logger = logging.getLogger(__name__)

# Model save path
MODEL_DIR = Path(settings.DATA_DIR) / "models"
MODEL_PATH = MODEL_DIR / "lightgbm_ranker.pkl"
FEATURE_NAMES_PATH = MODEL_DIR / "lightgbm_features.json"


class LightGBMScoringModel:
    """LightGBM-based product scoring for Phase 2.

    Usage:
        model = LightGBMScoringModel()
        if not model.is_trained:
            model.train()
        top_skus = model.get_top_n(district_id, sku_ids, n=20)
    """

    def __init__(
        self,
        feature_extractor: MockFeatureExtractor | None = None,
        model_path: Path | None = None,
    ):
        self.fe = feature_extractor or MockFeatureExtractor()
        self.ds = MockDataService()
        self.metrics = MetricsService(self.ds)
        self.model_path = model_path or MODEL_PATH
        self._model = None
        self._feature_names: List[str] = []
        self._feature_importance: dict = {}

    @property
    def is_trained(self) -> bool:
        return self._model is not None or self.model_path.exists()

    # ═══════════════ Training ═══════════════

    def train(self, force: bool = False) -> dict:
        """Train LightGBM ranker on all districts' historical data.

        Returns training summary: {num_samples, num_features, top_features, ...}
        """
        if self.is_trained and not force:
            self.load()
            return {"status": "already_trained", "num_features": len(self._feature_names)}

        logger.info("[LightGBM] Starting training...")

        # 1. Build training data
        X, y, feature_names = self._build_training_data()
        if len(X) == 0:
            logger.warning("[LightGBM] No training data available")
            return {"status": "no_data"}

        logger.info(f"[LightGBM] Training on {len(X)} samples, {len(feature_names)} features")

        # 2. Train LightGBM
        import lightgbm as lgb

        # Train/val split (80/20)
        split = int(len(X) * 0.8)
        indices = np.random.RandomState(42).permutation(len(X))
        train_idx, val_idx = indices[:split], indices[split:]

        dtrain = lgb.Dataset(X[train_idx], label=y[train_idx])
        dval = lgb.Dataset(X[val_idx], label=y[val_idx], reference=dtrain)

        params = {
            "objective": "regression",
            "metric": "rmse",
            "boosting_type": "gbdt",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "verbose": -1,
            "seed": 42,
            "n_jobs": -1,
        }

        self._model = lgb.train(
            params,
            dtrain,
            valid_sets=[dval],
            num_boost_round=200,
            callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)],
        )

        self._feature_names = feature_names

        # 3. Extract feature importance
        importance = self._model.feature_importance(importance_type="gain")
        self._feature_importance = {
            name: round(float(imp), 2)
            for name, imp in sorted(
                zip(feature_names, importance), key=lambda x: x[1], reverse=True
            )
        }

        # 4. Save model
        self.save()

        return {
            "status": "trained",
            "num_samples": len(X),
            "num_features": len(feature_names),
            "best_iteration": self._model.best_iteration,
            "top_features": dict(list(self._feature_importance.items())[:5]),
        }

    def _build_training_data(self) -> Tuple[np.ndarray, np.ndarray, List[str]]:
        """Build feature matrix X and target y from historical data.

        Target: normalized composite of GMV contribution + high CVR + high margin.
        """
        all_features = []
        all_targets = []

        for district_id in range(1, settings.NUM_DISTRICTS + 1):
            # Get all products
            products = self.ds.get_all_products()
            if products.empty:
                continue
            sku_ids = products["sku_id"].tolist()

            # Extract features
            features = self.fe.extract(district_id, sku_ids)
            if features.empty:
                continue

            # Compute target per SKU
            targets = self._compute_targets(district_id, sku_ids, products)

            # Merge features with targets
            features["_target"] = features["sku_id"].map(targets)
            features = features.dropna(subset=["_target"])

            if len(features) == 0:
                continue

            # Select numeric feature columns
            feature_cols = [
                c for c in features.columns
                if c not in ("sku_id", "name", "category", "_target")
                and pd.api.types.is_numeric_dtype(features[c])
            ]

            all_features.append(features[feature_cols].values)
            all_targets.append(features["_target"].values)

        if not all_features:
            return np.array([]), np.array([]), []

        X = np.vstack(all_features)
        y = np.hstack(all_targets)
        feature_names = feature_cols  # from last iteration (same for all districts)

        return X, y, feature_names

    def _compute_targets(
        self, district_id: int, sku_ids: List[str], products: pd.DataFrame
    ) -> dict:
        """Compute a composite target score per SKU based on historical performance.

        Target = 0.4 * gmv_contribution_norm + 0.3 * cvr_norm + 0.3 * margin_norm
        """
        end = datetime.utcnow()
        start = end - timedelta(days=30)
        funnel_df = self.ds.get_funnel_data(district_id, start, end)

        targets = {}
        for sku in sku_ids:
            sku_data = funnel_df[funnel_df["sku_id"] == sku] if not funnel_df.empty else pd.DataFrame()
            pay_data = sku_data[sku_data["funnel_stage"] == "payment"] if not sku_data.empty else pd.DataFrame()
            cart_data = sku_data[sku_data["funnel_stage"] == "cart"] if not sku_data.empty else pd.DataFrame()

            # GMV contribution
            gmv = (pay_data["price"] * pay_data["quantity"]).sum() if not pay_data.empty else 0

            # CVR
            carts = len(cart_data)
            pays = len(pay_data)
            cvr = pays / max(carts, 1)

            # Margin (from product)
            prod = products[products["sku_id"] == sku]
            margin = float(prod["gross_margin"].iloc[0]) if not prod.empty else 0.2

            targets[sku] = gmv * 0.4 + cvr * 0.3 + margin * 0.3

        # Normalize target to [0, 1]
        if targets:
            max_val = max(targets.values())
            if max_val > 0:
                targets = {k: v / max_val for k, v in targets.items()}

        return targets

    # ═══════════════ Inference ═══════════════

    def score(self, district_id: int, sku_ids: List[str]) -> pd.DataFrame:
        """Score products and return ranked DataFrame."""
        if not self.is_trained:
            raise RuntimeError("Model not trained. Call train() first or use ProductScoringModel as fallback.")

        if self._model is None:
            self.load()

        features = self.fe.extract(district_id, sku_ids)
        if features.empty:
            return pd.DataFrame()

        # Select numeric feature columns (match training)
        feature_cols = [
            c for c in features.columns
            if c not in ("sku_id", "name", "category")
            and pd.api.types.is_numeric_dtype(features[c])
        ]

        # Ensure feature columns match training order
        if self._feature_names:
            available = [c for c in self._feature_names if c in feature_cols]
            X = features[available].fillna(0).values
        else:
            X = features[feature_cols].fillna(0).values

        # Predict
        preds = self._model.predict(X)
        preds = np.clip(preds, 0, 1)

        scores = pd.DataFrame({
            "sku_id": features["sku_id"],
            "total_score": np.round(preds, 4),
        })

        # Risk rating
        scores["risk_level"] = pd.cut(
            scores["total_score"],
            bins=[-0.01, 0.4, 0.7, 1.01],
            labels=["high", "medium", "low"],
        ).astype(str)

        # Merge product info
        products = self.ds.get_product_features(sku_ids)
        if not products.empty:
            scores = scores.merge(
                products[["sku_id", "name", "category", "original_price", "gross_margin", "inventory"]],
                on="sku_id", how="left",
            )

        return scores.sort_values("total_score", ascending=False).reset_index(drop=True)

    def get_top_n(self, district_id: int, sku_ids: List[str], n: int = 20) -> List[dict]:
        """Return top-N products as Agent Output-ready list."""
        try:
            df = self.score(district_id, sku_ids)
        except RuntimeError:
            # Fallback to rule-based model
            from app.models.product_scoring import ProductScoringModel
            return ProductScoringModel().get_top_n(district_id, sku_ids, n)

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
                "expected_gmv": round(float(row.get("total_score", 0)) * float(row.get("original_price", 10)) * 100, 2),
                "risk_level": row.get("risk_level", "medium"),
                "price": row.get("original_price", 0),
                "gross_margin": row.get("gross_margin", 0),
                "inventory": int(row.get("inventory", 0)),
            })
        return results

    def get_feature_importance(self) -> dict:
        """Return feature importance for explainability."""
        if not self._feature_importance:
            if self.model_path.exists():
                self.load()
        return self._feature_importance

    # ═══════════════ Persistence ═══════════════

    def save(self):
        """Save model and metadata to disk."""
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump(self._model, f)
        with open(FEATURE_NAMES_PATH, "w") as f:
            json.dump({
                "feature_names": self._feature_names,
                "feature_importance": self._feature_importance,
                "saved_at": datetime.utcnow().isoformat(),
            }, f)
        logger.info(f"[LightGBM] Model saved to {self.model_path}")

    def load(self):
        """Load model from disk."""
        if not self.model_path.exists():
            raise FileNotFoundError(f"Model not found at {self.model_path}")

        with open(self.model_path, "rb") as f:
            self._model = pickle.load(f)

        if FEATURE_NAMES_PATH.exists():
            with open(FEATURE_NAMES_PATH) as f:
                meta = json.load(f)
                self._feature_names = meta.get("feature_names", [])
                self._feature_importance = meta.get("feature_importance", {})

        logger.info(f"[LightGBM] Model loaded ({len(self._feature_names)} features)")
