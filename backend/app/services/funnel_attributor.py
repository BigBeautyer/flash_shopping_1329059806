"""Funnel-layer attribution engine.

Maps detected anomalies to root cause hypotheses using a rule matrix.
Complemented by LLM for natural-language explanation and suggestion generation.

Pattern 5 (agents.md): diagnosis must attribute to specific funnel layers before
suggesting fixes — never let LLM guess the layer.
"""

from typing import Dict, List, Optional
from app.services.anomaly_detector import Anomaly


# ── Attribution Matrix ──
# Each row: (funnel_layer, metric_pattern, root_cause_category, description, fix_direction)

ATTRIBUTION_MATRIX = [
    # ═══ Exposure Layer ═══
    ("exposure", "exposure_uv_down", "预算与投放",
     "曝光量下滑 → 可能是预算不足、渠道分发异常、排位下降或大盘流量波动",
     "检查投放预算消耗率，确认渠道是否正常分发，对比大盘流量趋势"),
    ("exposure", "exposure_uv_up", "流量异常",
     "曝光量异常飙升 → 可能是渠道误投、刷量或竞品停投导致流量涌入",
     "排查渠道投放记录，确认是否为真实流量，防止无效消耗"),

    # ═══ Click / CTR Layer ═══
    ("click", "click_uv_down", "渠道与创意",
     "点击量下滑 → 可能原因：文案吸引力不足、素材点击率下降、位置变差",
     "检查各渠道CTR趋势，对比历史素材效果，排查是否被竞品抢占位置"),
    ("click", "ctr_down", "素材与人群",
     "CTR下滑但曝光正常 → 文案/素材吸引力下降或人群定向错配",
     "检查近期修改的文案和素材，对比不同人群包的CTR差异"),
    ("click", "ctr_up", "正向信号",
     "CTR异常上升 → 可能是素材优化生效或竞品减弱，可考虑追投",
     "确认是否为真实提升，排除刷量；若真实可加大预算"),

    # ═══ Cart Layer ═══
    ("cart", "cart_uv_down", "商品与价格",
     "加购量下滑 → 可能原因：商品吸引力不足、价格不具竞争力、竞品打折",
     "对比竞品价格带，检查商品评价和详情页转化率"),
    ("cart", "cart_rate_down", "选品与定价",
     "加购率下滑但点击正常 → 商品卖点弱或价格高于竞品",
     "检查选品清单的竞品价格差距，评估是否需要调价或换品"),

    # ═══ Payment / CVR Layer ═══
    ("payment", "payment_uv_down", "转化与履约",
     "支付量下滑 → 可能原因：库存不足、优惠券异常、结算链路问题、配送时效差",
     "检查库存预警、优惠券发放和使用率、结算成功率、配送时效"),
    ("payment", "cvr_down", "下单到支付",
     "支付转化率下滑但加购正常 → 结算体验或优惠券门槛问题",
     "检查结算页报错率、优惠券门槛是否过高、支付渠道是否正常"),
    ("payment", "gmv_down", "客单价或转化",
     "GMV下滑 → 可能是客单价下降或高单价商品转化降低",
     "分离检查客单价趋势和支付UV趋势，定位是价格问题还是转化问题"),
    ("payment", "aov_down", "客单价",
     "客单价下滑 → 用户倾向低价商品或凑单行为减少",
     "检查各价格带商品占比变化，评估是否需要设置满减门槛"),

    # ═══ Repurchase Layer ═══
    ("repurchase", "repurchase_uv_down", "用户留存",
     "复购量下滑 → 首单体验差、商品质量下降、竞品拉新活动",
     "检查NPS评分趋势、商品质量退货率、竞品近期拉新活动"),
    ("repurchase", "repurchase_rate_down", "用户粘性",
     "复购率下滑但支付正常 → 用户粘性降低，可能是品类疲劳",
     "检查复购用户画像变化，评估是否需要品类轮换"),

    # ═══ Margin / Health ═══
    ("payment", "gross_margin_down", "毛利风险",
     "毛利率下滑 → 折扣过深或高毛利品销售占比下降",
     "检查折扣力度分布，高毛利品曝光是否充足，是否需要调整选品结构"),
    ("payment", "sell_through_down", "库存周转",
     "动销率下滑 → SKU过多或选品不对路，库存积压",
     "检查库存周转天数，对比品类动销率，评估是否需要收缩选品范围"),
]


class FunnelAttributor:
    """Match detected anomalies to root cause hypotheses."""

    def attribute(self, anomalies: List[Anomaly]) -> dict:
        """Run attribution on a list of anomalies.

        Returns a dict with:
        - attributions: list of {layer, metric, root_cause_category, description, fix_direction, matched_anomalies}
        - layer_summary: per-layer severity summary
        """
        # Group anomalies by layer
        by_layer: Dict[str, List[Anomaly]] = {}
        for a in anomalies:
            layer = a.funnel_layer
            if layer not in by_layer:
                by_layer[layer] = []
            by_layer[layer].append(a)

        # Match each anomaly against attribution matrix
        attributions: List[dict] = []
        matched_ids = set()

        for layer, pattern, category, desc, fix in ATTRIBUTION_MATRIX:
            layer_anomalies = by_layer.get(layer, [])
            matching = [
                a for a in layer_anomalies
                if f"{a.metric_name}_{a.direction}" == pattern
            ]
            if matching:
                for a in matching:
                    matched_ids.add(id(a))
                max_sev = max((a.severity for a in matching), key=lambda s: {"severe": 3, "critical": 2, "warning": 1, "info": 0}.get(s, 0))
                attributions.append({
                    "funnel_layer": layer,
                    "pattern": pattern,
                    "root_cause_category": category,
                    "description": desc,
                    "fix_direction": fix,
                    "severity": max_sev,
                    "matched_anomalies": [
                        {
                            "metric": a.metric_name,
                            "current_value": a.current_value,
                            "expected_value": a.expected_value,
                            "deviation": a.deviation,
                            "severity": a.severity,
                            "detected_at": a.detected_at,
                        }
                        for a in matching
                    ],
                })

        # Add unmatched anomalies as "unknown" attributions
        for a in anomalies:
            if id(a) not in matched_ids:
                attributions.append({
                    "funnel_layer": a.funnel_layer,
                    "pattern": f"{a.metric_name}_{a.direction}",
                    "root_cause_category": "未分类异常",
                    "description": f"{a.metric_name} 异常 {a.direction}，偏离度 {a.sigma}σ",
                    "fix_direction": "需人工分析",
                    "severity": a.severity,
                    "matched_anomalies": [{
                        "metric": a.metric_name,
                        "current_value": a.current_value,
                        "expected_value": a.expected_value,
                        "deviation": a.deviation,
                        "severity": a.severity,
                        "detected_at": a.detected_at,
                    }],
                })

        # Layer summary
        layer_summary = {}
        severity_order = {"severe": 3, "critical": 2, "warning": 1, "info": 0}
        for layer, layer_anomalies in by_layer.items():
            top_sev = max(layer_anomalies, key=lambda a: severity_order.get(a.severity, 0))
            layer_summary[layer] = {
                "anomaly_count": len(layer_anomalies),
                "top_severity": top_sev.severity,
            }

        # Overall severity
        all_anomalies_sorted = sorted(anomalies, key=lambda a: severity_order.get(a.severity, 0), reverse=True)
        overall = all_anomalies_sorted[0].severity if all_anomalies_sorted else "info"

        return {
            "attributions": attributions,
            "layer_summary": layer_summary,
            "overall_severity": overall,
            "total_anomalies": len(anomalies),
        }
