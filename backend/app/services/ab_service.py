"""AB Experiment Service — statistical analysis, experiment CRUD, comparison logic.

Phase 3: runs Welch's t-test (pure numpy), computes effect sizes (Cohen's d),
generates comparison reports, and persists results to the ABExperiment table.

No external stats library required — implements t-distribution CDF via the
regularized incomplete beta function (Abramowitz & Stegun 26.5.8).
"""

from __future__ import annotations
import math
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from app.db.models import SessionLocal, ABExperiment, Order
from app.services.data_service import MockDataService


class ABService:
    """AB experiment design, execution, and statistical analysis."""

    def __init__(self, data_service: MockDataService | None = None):
        self.ds = data_service or MockDataService()

    # ── Statistical Methods (pure Python + numpy, no scipy) ──

    @staticmethod
    def _t_distribution_cdf(t: float, df: float) -> float:
        """Two-tailed t-distribution CDF via regularized incomplete beta.

        Based on Abramowitz & Stegun 26.7.1: P(T <= t) = 1 - I_x(df/2, 0.5) / 2
        where x = df / (df + t^2).
        Implements the regularized incomplete beta function via continued fraction.
        """
        x = df / (df + t * t)
        a, b = df / 2.0, 0.5
        # Regularized incomplete beta via continued fraction (Lentz's method)
        if x == 0 or x == 1:
            return 0.0 if x == 0 else 1.0

        # Log beta function via log gamma
        log_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)

        # Continued fraction for I_x(a,b)
        front = math.exp(a * math.log(x) + b * math.log(1 - x) - log_beta) / a

        # Lentz continued fraction
        f = 1.0
        c = 1.0
        d = 1.0 - (a + b) * x / (a + 1) if (1.0 - (a + b) * x / (a + 1)) != 0 else 1e-30
        if abs(d) < 1e-30:
            d = 1e-30
        d = 1.0 / d
        f *= d

        for m in range(1, 200):
            # Even step
            mm = 2 * m
            numerator = m * (b - m) * x / ((a + mm - 1) * (a + mm))
            d = 1.0 + numerator * d
            if abs(d) < 1e-30:
                d = 1e-30
            c = 1.0 + numerator / c
            if abs(c) < 1e-30:
                c = 1e-30
            d = 1.0 / d
            f *= c * d

            # Odd step
            numerator = -(a + m) * (a + b + m) * x / ((a + mm) * (a + mm + 1))
            d = 1.0 + numerator * d
            if abs(d) < 1e-30:
                d = 1e-30
            c = 1.0 + numerator / c
            if abs(c) < 1e-30:
                c = 1e-30
            d = 1.0 / d
            delta = c * d
            f *= delta

            if abs(delta - 1.0) < 1e-12:
                break

        reg_beta = front * (f - 1.0)
        # Two-tailed p-value
        return reg_beta

    @staticmethod
    def welch_ttest(control_vals: list[float], treatment_vals: list[float]) -> dict:
        """Welch's t-test (unequal variance) — pure Python implementation."""
        if len(control_vals) < 3 or len(treatment_vals) < 3:
            return {"t_stat": 0, "p_value": 1.0, "significant": False, "method": "welch", "note": "insufficient data"}

        c = np.array(control_vals, dtype=float)
        t = np.array(treatment_vals, dtype=float)

        n1, n2 = len(c), len(t)
        m1, m2 = np.mean(c), np.mean(t)
        v1, v2 = np.var(c, ddof=1), np.var(t, ddof=1)

        # Welch's t-statistic
        se = math.sqrt(v1 / n1 + v2 / n2)
        if se < 1e-12:
            return {"t_stat": 0, "p_value": 1.0, "significant": False, "method": "welch", "note": "zero variance"}

        t_stat = (m1 - m2) / se

        # Welch-Satterthwaite degrees of freedom
        num = (v1 / n1 + v2 / n2) ** 2
        denom = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
        df = num / denom if denom > 1e-12 else 1.0

        # Two-tailed p-value
        p_value = ABService._t_distribution_cdf(abs(t_stat), df)

        return {
            "t_stat": round(float(t_stat), 4),
            "p_value": round(float(p_value), 4),
            "significant": bool(p_value < 0.05),
            "method": "welch",
        }

    @staticmethod
    def cohens_d(control_vals: list[float], treatment_vals: list[float]) -> float:
        """Cohen's d effect size: (mean_diff / pooled_std)."""
        c = np.array(control_vals, dtype=float)
        t = np.array(treatment_vals, dtype=float)
        n1, n2 = len(c), len(t)
        if n1 < 2 or n2 < 2:
            return 0.0
        pooled_std = math.sqrt(((n1 - 1) * np.var(c, ddof=1) + (n2 - 1) * np.var(t, ddof=1)) / (n1 + n2 - 2))
        if pooled_std == 0:
            return 0.0
        return round(float((np.mean(t) - np.mean(c)) / pooled_std), 4)

    @staticmethod
    def relative_lift(control_mean: float, treatment_mean: float) -> float:
        """Relative lift = (treatment - control) / |control|."""
        if abs(control_mean) < 1e-8:
            return 0.0
        return round(float((treatment_mean - control_mean) / abs(control_mean)), 4)

    # ── Metric Extraction ──

    def extract_metric(self, group_id: int, metric: str, start: datetime, end: datetime) -> list[float]:
        """Extract metric values for a given district/group over the time window.

        group_id maps to district_id for simplicity.
        """
        df = self.ds.get_funnel_data(group_id, start, end)
        if df.empty:
            return []

        if metric in ("gmv", "gmv_per_user"):
            pay = df[df["funnel_stage"] == "payment"]
            if pay.empty:
                return []
            gmv_series = pay.groupby("user_id").apply(lambda g: (g["price"] * g["quantity"]).sum())
            return [round(float(v), 2) for v in gmv_series.values]

        if metric == "cvr":
            cart_users = set(df[df["funnel_stage"] == "cart"]["user_id"].unique())
            pay_users = set(df[df["funnel_stage"] == "payment"]["user_id"].unique())
            if not cart_users:
                return []
            return [1.0 if u in pay_users else 0.0 for u in cart_users]

        if metric == "ctr":
            exp_users = set(df[df["funnel_stage"] == "exposure"]["user_id"].unique())
            click_users = set(df[df["funnel_stage"] == "click"]["user_id"].unique())
            if not exp_users:
                return []
            return [1.0 if u in click_users else 0.0 for u in exp_users]

        if metric == "aov":
            pay = df[df["funnel_stage"] == "payment"]
            if pay.empty:
                return []
            aov_series = pay.groupby("user_id").apply(lambda g: (g["price"] * g["quantity"]).sum())
            return [round(float(v), 2) for v in aov_series.values]

        # Default: per-user GMV
        pay = df[df["funnel_stage"] == "payment"]
        if pay.empty:
            return []
        gmv_series = pay.groupby("user_id").apply(lambda g: (g["price"] * g["quantity"]).sum())
        return [round(float(v), 2) for v in gmv_series.values]

    # ── Experiment Runner ──

    def run_experiment(
        self,
        experiment_name: str,
        hypothesis: str,
        control_district: int,
        treatment_district: int,
        metric: str = "gmv",
        days: int = 7,
    ) -> dict:
        """Run a full AB experiment: extract, compare, test, save.

        Returns a dict suitable for API response.
        """
        end = datetime.utcnow()
        start = end - pd.Timedelta(days=days)

        control_vals = self.extract_metric(control_district, metric, start, end)
        treatment_vals = self.extract_metric(treatment_district, metric, start, end)

        c_mean = round(np.mean(control_vals), 2) if control_vals else 0
        t_mean = round(np.mean(treatment_vals), 2) if treatment_vals else 0

        ttest_result = self.welch_ttest(control_vals, treatment_vals)
        effect_size = self.cohens_d(control_vals, treatment_vals)
        lift = self.relative_lift(c_mean, t_mean)

        # Determine effect interpretation
        if abs(effect_size) < 0.2:
            effect_label = "negligible"
        elif abs(effect_size) < 0.5:
            effect_label = "small"
        elif abs(effect_size) < 0.8:
            effect_label = "medium"
        else:
            effect_label = "large"

        # Save to DB
        experiment_id = None
        try:
            db = SessionLocal()
            exp = ABExperiment(
                experiment_name=experiment_name,
                hypothesis=hypothesis,
                control_group=str(control_district),
                treatment_group=str(treatment_district),
                metric_name=metric,
                control_value=c_mean,
                treatment_value=t_mean,
                p_value=ttest_result["p_value"],
                significant=ttest_result["significant"],
                created_at=datetime.utcnow(),
            )
            db.add(exp)
            db.commit()
            db.refresh(exp)
            experiment_id = exp.id
        except Exception:
            pass
        finally:
            db.close()

        return {
            "experiment_id": experiment_id,
            "experiment_name": experiment_name,
            "hypothesis": hypothesis,
            "control_group": {"district_id": control_district, "sample_size": len(control_vals)},
            "treatment_group": {"district_id": treatment_district, "sample_size": len(treatment_vals)},
            "metric": metric,
            "results": {
                "control_mean": c_mean,
                "treatment_mean": t_mean,
                "relative_lift": lift,
                "p_value": ttest_result["p_value"],
                "significant": ttest_result["significant"],
                "effect_size_cohens_d": effect_size,
                "effect_interpretation": effect_label,
            },
            "method": ttest_result["method"],
            "period_days": days,
            "generated_at": datetime.utcnow().isoformat(),
        }

    # ── List / Get ──

    def list_experiments(self, limit: int = 20) -> list[dict]:
        db = SessionLocal()
        try:
            exps = db.query(ABExperiment).order_by(ABExperiment.created_at.desc()).limit(limit).all()
            return [_exp_to_dict(e) for e in exps]
        finally:
            db.close()

    def get_experiment(self, experiment_id: int) -> dict | None:
        db = SessionLocal()
        try:
            exp = db.query(ABExperiment).filter(ABExperiment.id == experiment_id).first()
            return _exp_to_dict(exp) if exp else None
        finally:
            db.close()

    # ── Comparison Matrix ──

    def comparison_matrix(self, district_ids: list[int], metric: str = "gmv", days: int = 7) -> list[dict]:
        """Run pairwise comparisons across all provided districts."""
        end = datetime.utcnow()
        start = end - pd.Timedelta(days=days)
        results = []
        for i, d1 in enumerate(district_ids):
            for d2 in district_ids[i + 1:]:
                c_vals = self.extract_metric(d1, metric, start, end)
                t_vals = self.extract_metric(d2, metric, start, end)
                if not c_vals or not t_vals:
                    continue
                c_mean = round(np.mean(c_vals), 2)
                t_mean = round(np.mean(t_vals), 2)
                tt = self.welch_ttest(c_vals, t_vals)
                results.append({
                    "control_district": d1, "treatment_district": d2,
                    "control_mean": c_mean, "treatment_mean": t_mean,
                    "relative_lift": self.relative_lift(c_mean, t_mean),
                    "p_value": tt["p_value"], "significant": tt["significant"],
                    "metric": metric,
                })
        return sorted(results, key=lambda r: r["p_value"])


def _exp_to_dict(exp: ABExperiment) -> dict:
    return {
        "id": exp.id,
        "experiment_name": exp.experiment_name,
        "hypothesis": exp.hypothesis,
        "control_group": exp.control_group,
        "treatment_group": exp.treatment_group,
        "metric_name": exp.metric_name,
        "control_value": exp.control_value,
        "treatment_value": exp.treatment_value,
        "p_value": exp.p_value,
        "significant": exp.significant,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
    }
