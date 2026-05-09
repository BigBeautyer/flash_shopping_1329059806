"""Anomaly detection engine — multi-level funnel detection with statistical bands.

Detection methods:
1. Rolling z-score: detect values >2σ from rolling mean
2. Absolute threshold: metric below hard floor
3. Relative change: day-over-day drop > X%

Phase 2: full depth — multi-method, per-metric config, severity grading.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from app.services.metrics_service import MetricsService
from app.services.data_service import MockDataService


@dataclass
class Anomaly:
    """Single detected anomaly."""
    metric_name: str            # e.g. "ctr", "cvr", "gmv"
    funnel_layer: str           # e.g. "exposure", "click", "cart", "payment"
    detected_at: str            # ISO date
    current_value: float
    expected_value: float       # rolling mean or threshold
    deviation: float            # signed deviation from expected
    sigma: float                # standard deviations from mean
    severity: str               # "info" / "warning" / "critical" / "severe"
    direction: str              # "up" / "down"
    detection_method: str       # "zscore" / "threshold" / "relative_change"
    context: dict = field(default_factory=dict)


@dataclass
class DiagnosisReport:
    """Full diagnosis report for a district."""
    report_id: str
    district_id: int
    district_name: str
    generated_at: str
    period_start: str
    period_end: str
    anomalies: List[dict] = field(default_factory=list)
    funnel_attribution: dict = field(default_factory=dict)
    root_causes: List[dict] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    overall_severity: str = "info"  # info / warning / critical
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class AnomalyDetector:
    """Multi-method anomaly detection for flash sale funnel metrics.

    Detection config per metric — tuned for flash sale business characteristics.
    """

    # ── Metric detection config ──
    # zscore_window: rolling window size for baseline
    # zscore_threshold: sigma threshold for anomaly flagging
    # absolute_floor: hard minimum (triggers critical regardless of z-score)
    # dod_drop_pct: day-over-day drop percentage that triggers warning
    METRIC_CONFIG = {
        "exposure_uv":    {"zscore_window": 7, "zscore_threshold": 2.0, "absolute_floor": 100,   "dod_drop_pct": 0.30},
        "click_uv":       {"zscore_window": 7, "zscore_threshold": 2.0, "absolute_floor": 50,    "dod_drop_pct": 0.25},
        "cart_uv":        {"zscore_window": 7, "zscore_threshold": 2.0, "absolute_floor": 20,    "dod_drop_pct": 0.25},
        "payment_uv":     {"zscore_window": 7, "zscore_threshold": 2.0, "absolute_floor": 10,    "dod_drop_pct": 0.30},
        "repurchase_uv":  {"zscore_window": 7, "zscore_threshold": 1.8, "absolute_floor": 2,     "dod_drop_pct": 0.40},
        "ctr":            {"zscore_window": 7, "zscore_threshold": 1.8, "absolute_floor": 0.02,  "dod_drop_pct": 0.20},
        "cart_rate":      {"zscore_window": 7, "zscore_threshold": 1.8, "absolute_floor": 0.05,  "dod_drop_pct": 0.20},
        "cvr":            {"zscore_window": 7, "zscore_threshold": 1.8, "absolute_floor": 0.03,  "dod_drop_pct": 0.25},
        "repurchase_rate": {"zscore_window": 7, "zscore_threshold": 1.5, "absolute_floor": 0.02, "dod_drop_pct": 0.30},
        "gmv":            {"zscore_window": 7, "zscore_threshold": 2.0, "absolute_floor": 50,    "dod_drop_pct": 0.25},
        "aov":            {"zscore_window": 7, "zscore_threshold": 1.8, "absolute_floor": 5,     "dod_drop_pct": 0.15},
        "gross_margin":   {"zscore_window": 7, "zscore_threshold": 1.5, "absolute_floor": 0.05,  "dod_drop_pct": 0.10},
        "sell_through":   {"zscore_window": 7, "zscore_threshold": 1.8, "absolute_floor": 0.10,  "dod_drop_pct": 0.25},
    }

    # Funnel layer mapping for attribution
    METRIC_TO_LAYER = {
        "exposure_uv": "exposure",
        "click_uv": "click",
        "cart_uv": "cart",
        "payment_uv": "payment",
        "repurchase_uv": "repurchase",
        "ctr": "click",
        "cart_rate": "cart",
        "cvr": "payment",
        "repurchase_rate": "repurchase",
        "gmv": "payment",
        "aov": "payment",
        "gross_margin": "payment",
        "sell_through": "payment",
    }

    def __init__(self, data_service: MockDataService | None = None):
        self.ds = data_service or MockDataService()
        self.metrics = MetricsService(self.ds)

    # ── Public API ──

    def detect(
        self,
        district_id: int,
        days: int = 30,
        recent_window: int = 1,
    ) -> Tuple[List[Anomaly], pd.DataFrame]:
        """Run all detection methods on a district's recent data.

        Returns (anomalies, daily_trend_df).
        """
        end = datetime.utcnow()
        start = end - timedelta(days=days)
        trend = self.metrics.get_daily_trend(district_id, days)

        if not trend:
            return [], pd.DataFrame()

        df = pd.DataFrame(trend)
        if "date" not in df.columns or df.empty:
            return [], df

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df = self._compute_derived_metrics(df)

        anomalies: List[Anomaly] = []

        for metric_name, config in self.METRIC_CONFIG.items():
            if metric_name not in df.columns:
                continue
            series = df[metric_name].values.astype(float).copy()

            # Only detect on metrics with enough data points
            if len(series) < max(config["zscore_window"], 3):
                continue

            # Check the most recent `recent_window` data points
            for offset in range(recent_window):
                idx = len(series) - 1 - offset
                if idx < config["zscore_window"]:
                    continue

                date_str = df["date"].iloc[idx].strftime("%Y-%m-%d") if hasattr(df["date"].iloc[idx], 'strftime') else str(df["date"].iloc[idx])
                detected = self._check_point(
                    metric_name, series, idx, date_str, config
                )
                anomalies.extend(detected)

        return anomalies, df

    def get_anomaly_summary(self, district_id: int, days: int = 30) -> dict:
        """Get a summary of current anomalies for UI rendering."""
        anomalies, df = self.detect(district_id, days, recent_window=3)

        by_severity = {"severe": [], "critical": [], "warning": [], "info": []}
        for a in anomalies:
            by_severity[a.severity].append(asdict(a))

        by_layer: Dict[str, List[dict]] = {}
        for a in anomalies:
            layer = a.funnel_layer
            if layer not in by_layer:
                by_layer[layer] = []
            by_layer[layer].append(asdict(a))

        return {
            "district_id": district_id,
            "total_anomalies": len(anomalies),
            "by_severity": {k: len(v) for k, v in by_severity.items()},
            "by_layer": {k: len(v) for k, v in by_layer.items()},
            "anomalies": [asdict(a) for a in anomalies],
            "by_severity_detail": by_severity,
            "by_layer_detail": by_layer,
        }

    # ── Detection Methods ──

    def _check_point(
        self,
        metric_name: str,
        series: np.ndarray,
        idx: int,
        date_str: str,
        config: dict,
    ) -> List[Anomaly]:
        """Run all detection methods on a single data point."""
        current = float(series[idx])
        window = series[max(0, idx - config["zscore_window"]):idx]
        anomalies: List[Anomaly] = []
        layer = self.METRIC_TO_LAYER.get(metric_name, "unknown")

        # Method 1: Z-score detection
        if len(window) >= 3:
            mean = np.mean(window)
            std = np.std(window)
            if std > 0:
                sigma = abs((current - mean) / std)
                if sigma >= config["zscore_threshold"]:
                    direction = "down" if current < mean else "up"
                    severity = self._sigma_to_severity(sigma)
                    anomalies.append(Anomaly(
                        metric_name=metric_name,
                        funnel_layer=layer,
                        detected_at=date_str,
                        current_value=round(current, 4),
                        expected_value=round(float(mean), 4),
                        deviation=round(current - float(mean), 4),
                        sigma=round(float(sigma), 2),
                        severity=severity,
                        direction=direction,
                        detection_method="zscore",
                        context={"window_size": len(window), "window_std": round(float(std), 4)},
                    ))

        # Method 2: Absolute floor check (only for downward dips)
        floor = config.get("absolute_floor")
        if floor is not None and current < floor:
            # Avoid duplicate if z-score already flagged it
            if not any(a.metric_name == metric_name and a.detected_at == date_str for a in anomalies):
                anomalies.append(Anomaly(
                    metric_name=metric_name,
                    funnel_layer=layer,
                    detected_at=date_str,
                    current_value=round(current, 4),
                    expected_value=float(floor),
                    deviation=round(current - float(floor), 4),
                    sigma=0,
                    severity="critical",
                    direction="down",
                    detection_method="threshold",
                    context={"floor": floor},
                ))

        # Method 3: Day-over-day relative change (only if we have a previous day)
        if idx > 0 and series[idx - 1] > 0:
            prev = float(series[idx - 1])
            dod_change = (current - prev) / prev
            dod_threshold = config.get("dod_drop_pct", 0.3)
            if dod_change < -dod_threshold:
                # Only add if not already caught by z-score (more severe)
                if not any(a.metric_name == metric_name and a.detected_at == date_str and a.severity in ("critical", "severe") for a in anomalies):
                    anomalies.append(Anomaly(
                        metric_name=metric_name,
                        funnel_layer=layer,
                        detected_at=date_str,
                        current_value=round(current, 4),
                        expected_value=round(prev, 4),
                        deviation=round(float(dod_change), 4),
                        sigma=0,
                        severity="warning",
                        direction="down",
                        detection_method="relative_change",
                        context={"prev_value": round(prev, 4), "dod_pct": round(float(dod_change), 4)},
                    ))

        return anomalies

    # ── Helpers ──

    @staticmethod
    def _compute_derived_metrics(df: pd.DataFrame) -> pd.DataFrame:
        """Compute derived rate metrics from funnel counts."""
        df = df.copy()
        if "exposure_uv" in df.columns and "click_uv" in df.columns:
            df["ctr"] = np.where(df["exposure_uv"] > 0, df["click_uv"] / df["exposure_uv"], 0.0)
        if "click_uv" in df.columns and "cart_uv" in df.columns:
            df["cart_rate"] = np.where(df["click_uv"] > 0, df["cart_uv"] / df["click_uv"], 0.0)
        if "cart_uv" in df.columns and "payment_uv" in df.columns:
            df["cvr"] = np.where(df["cart_uv"] > 0, df["payment_uv"] / df["cart_uv"], 0.0)
        if "payment_uv" in df.columns and "repurchase_uv" in df.columns:
            df["repurchase_rate"] = np.where(df["payment_uv"] > 0, df["repurchase_uv"] / df["payment_uv"], 0.0)
        # aov, gross_margin, sell_through are computed from dashboard snapshot for now
        return df

    @staticmethod
    def _sigma_to_severity(sigma: float) -> str:
        if sigma >= 3.0:
            return "severe"
        elif sigma >= 2.5:
            return "critical"
        elif sigma >= 2.0:
            return "warning"
        return "info"
