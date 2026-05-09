"""Mock data generator for flash sale business.

Generates realistic data with configurable noise and anomalies.
Output: SQLite populated with districts, products, users, orders, competitors.

Key design: DataService abstract class wraps all access so real data sources
can replace this module without changing Agent logic (ADR-002).
"""

import random
import json
import os
from datetime import datetime, timedelta
from typing import Optional
import numpy as np
import pandas as pd

from app.core.config import settings
from app.db.models import (
    init_db, SessionLocal,
    BusinessDistrict, Product, Order, CompetitorSnapshot,
    Campaign, CampaignTask
)


# ── Seed for reproducibility ──
random.seed(42)
np.random.seed(42)

# ── Constants ──
CATEGORIES = ["水果", "饮料", "零食", "乳制品", "速食", "烘焙", "日用品", "酒类"]
DISTRICT_NAMES = ["望京", "三里屯", "国贸", "中关村", "五道口"]
PEAK_HOURS_POOL = [[11, 12, 17, 18], [12, 13, 18, 19], [10, 11, 16, 17], [11, 13, 17, 19], [12, 14, 18, 20]]
CHANNELS = ["search", "recommend", "social", "push"]
FUNNEL_STAGES = ["exposure", "click", "cart", "payment", "repurchase"]


# ═══════════════════════════════════════════════
# District Generation
# ═══════════════════════════════════════════════

def generate_districts(db) -> list:
    """Generate 5 business districts with distinct profiles."""
    districts = []
    profiles = [
        # name, city, lbs_heat, office/res, avg_consumption, competitor_density
        ("望京", "北京", 0.85, 0.70, 45.0, 0.60),
        ("三里屯", "北京", 0.92, 0.30, 65.0, 0.75),
        ("国贸", "北京", 0.88, 0.90, 55.0, 0.80),
        ("中关村", "北京", 0.78, 0.85, 40.0, 0.50),
        ("五道口", "北京", 0.80, 0.50, 35.0, 0.55),
    ]
    for i, (name, city, lbs, ratio, consumption, comp_density) in enumerate(profiles):
        d = BusinessDistrict(
            id=i + 1,
            name=name,
            city=city,
            lbs_heat_index=lbs,
            office_residential_ratio=ratio,
            avg_consumption=consumption,
            competitor_density=comp_density,
            peak_hours=PEAK_HOURS_POOL[i],
        )
        db.add(d)
        districts.append(d)
    return districts


# ═══════════════════════════════════════════════
# Product Generation
# ═══════════════════════════════════════════════

def generate_products(db, num: int = 200) -> list:
    """Generate products across 8 categories with realistic pricing."""
    products = []
    category_params = {
        "水果":   (5, 15, 8, 25, 3, 7),
        "饮料":   (3, 8,  5, 15, 30, 180),
        "零食":   (4, 12, 6, 20, 60, 365),
        "乳制品": (6, 18, 10, 30, 7, 21),
        "速食":   (8, 25, 15, 40, 30, 180),
        "烘焙":   (5, 20, 10, 35, 1, 3),
        "日用品": (10, 30, 20, 60, 180, 730),
        "酒类":   (20, 80, 40, 150, 180, 1095),
    }
    for i in range(num):
        cat = random.choice(CATEGORIES)
        cost_low, cost_high, price_low, price_high, shelf_min, shelf_max = category_params[cat]
        cost = round(random.uniform(cost_low, cost_high), 2)
        price = round(random.uniform(max(cost, price_low), price_high), 2)
        gm = round((price - cost) / price, 4) if price > 0 else 0
        p = Product(
            sku_id=f"SKU_{i:04d}",
            name=f"{cat}_商品_{i:04d}",
            category=cat,
            cost_price=cost,
            original_price=price,
            gross_margin=gm,
            inventory=random.randint(0, 500),
            shelf_life_days=random.randint(shelf_min, shelf_max),
            repurchase_rate=round(random.uniform(0.05, 0.40), 4),
        )
        db.add(p)
        products.append(p)
    return products


# ═══════════════════════════════════════════════
# Order / Funnel Generation (with noise)
# ═══════════════════════════════════════════════

def _inject_noise(val: float, rate: float = None) -> Optional[float]:
    """Randomly return NaN (missing) or extreme value (anomaly)."""
    if rate is None:
        rate = settings.NOISE_RATE
    r = random.random()
    if r < rate:
        return None  # missing
    if r < rate + settings.ANOMALY_RATE:
        return val * random.uniform(5, 20)  # anomaly spike
    return val


def generate_orders(db, districts: list, products: list, num_users: int = 10000, num_months: int = 3):
    """Generate funnel data: exposure → click → cart → payment → repurchase.

    This is the core data that feeds the dashboard and agents.
    Simulates realistic conversion funnel with noise.
    """
    end_date = datetime(2026, 5, 7)
    start_date = end_date - timedelta(days=30 * num_months)

    # Per-district baseline conversion rates (reflects district characteristics)
    district_cvr = {
        1: (0.12, 0.18),  # 望京: decent conversion
        2: (0.10, 0.15),  # 三里屯: higher traffic, lower cvr
        3: (0.14, 0.20),  # 国贸: high-income, high cvr
        4: (0.11, 0.16),  # 中关村: tech workers
        5: (0.09, 0.14),  # 五道口: students
    }

    user_ids = list(range(1, num_users + 1))
    sku_ids = [p.sku_id for p in products]
    orders = []
    batch_size = 5000
    count = 0

    for day_offset in range((end_date - start_date).days):
        current_date = start_date + timedelta(days=day_offset)
        # Simulate daily active campaigns (随机 1-3 个商圈有活动)
        active_districts = random.sample(districts, k=random.randint(1, 3))
        # Products selected for campaign (模拟选品范围)
        campaign_skus = random.sample(sku_ids, k=random.randint(10, 40))

        for district in active_districts:
            did = district.id
            # Determine daily traffic volume (affected by lbs_heat and day-of-week)
            base_traffic = int(500 * district.lbs_heat_index)
            if current_date.weekday() >= 5:  # weekend boost
                base_traffic = int(base_traffic * 1.3)
            if current_date.hour in [11, 12, 17, 18]:  # peak hour boost
                base_traffic = int(base_traffic * 1.5)

            for sku in campaign_skus:
                exposures = max(1, int(random.gauss(base_traffic, base_traffic * 0.3)))
                cvr_low, cvr_high = district_cvr[did]
                funnel_cvr = random.uniform(cvr_low, cvr_high)

                # Funnel stages
                clicks = int(exposures * random.uniform(0.08, 0.15))
                carts = int(clicks * random.uniform(0.15, 0.25))
                payments = int(carts * funnel_cvr)
                repurchases = int(payments * random.uniform(0.10, 0.30))

                price = random.uniform(5, 50)

                # Exposure records
                for _ in range(min(exposures, 200)):  # cap per sku per day
                    uid = random.choice(user_ids)
                    ch = random.choice(CHANNELS)
                    orders.append(Order(
                        user_id=uid, sku_id=sku, campaign_id=f"CAMP_{current_date.strftime('%Y%m%d')}",
                        district_id=did, price=price, quantity=1, channel=ch,
                        funnel_stage="exposure",
                        created_at=current_date + timedelta(hours=random.randint(8, 22)),
                    ))
                    count += 1

                # Click records (subset)
                for _ in range(min(clicks, 60)):
                    uid = random.choice(user_ids)
                    orders.append(Order(
                        user_id=uid, sku_id=sku, campaign_id=f"CAMP_{current_date.strftime('%Y%m%d')}",
                        district_id=did, price=price, quantity=1, channel=random.choice(CHANNELS),
                        funnel_stage="click",
                        created_at=current_date + timedelta(hours=random.randint(8, 22)),
                    ))
                    count += 1

                # Cart records
                for _ in range(min(carts, 30)):
                    uid = random.choice(user_ids)
                    orders.append(Order(
                        user_id=uid, sku_id=sku, campaign_id=f"CAMP_{current_date.strftime('%Y%m%d')}",
                        district_id=did, price=price, quantity=1, channel=random.choice(CHANNELS),
                        funnel_stage="cart",
                        created_at=current_date + timedelta(hours=random.randint(8, 22)),
                    ))
                    count += 1

                # Payment records
                for _ in range(min(payments, 15)):
                    uid = random.choice(user_ids)
                    paid_price = _inject_noise(price) or price
                    orders.append(Order(
                        user_id=uid, sku_id=sku, campaign_id=f"CAMP_{current_date.strftime('%Y%m%d')}",
                        district_id=did, price=paid_price, quantity=random.randint(1, 3),
                        channel=random.choice(CHANNELS),
                        funnel_stage="payment",
                        created_at=current_date + timedelta(hours=random.randint(8, 22)),
                    ))
                    count += 1

                # Repurchase records (small subset)
                for _ in range(min(repurchases, 8)):
                    uid = random.choice(user_ids)
                    orders.append(Order(
                        user_id=uid, sku_id=sku, campaign_id=f"CAMP_{current_date.strftime('%Y%m%d')}",
                        district_id=did, price=price, quantity=1, channel=random.choice(CHANNELS),
                        funnel_stage="repurchase",
                        created_at=current_date + timedelta(hours=random.randint(8, 22)),
                    ))
                    count += 1

                # Bulk insert every batch_size
                if count >= batch_size:
                    db.bulk_save_objects(orders)
                    db.commit()
                    orders.clear()
                    count = 0

    # Final flush
    if orders:
        db.bulk_save_objects(orders)
        db.commit()


# ═══════════════════════════════════════════════
# Competitor Snapshot Generation
# ═══════════════════════════════════════════════

def generate_competitor_snapshots(db, districts: list):
    """Generate competitor pricing data per district per category."""
    snapshots = []
    competitor_names = ["竞品A", "竞品B", "竞品C"]
    for district in districts:
        for cat in CATEGORIES:
            for comp in competitor_names:
                p_low = round(random.uniform(3, 20), 2)
                p_high = round(p_low + random.uniform(5, 30), 2)
                s = CompetitorSnapshot(
                    district_id=district.id,
                    category=cat,
                    competitor_name=comp,
                    price_band_low=p_low,
                    price_band_high=p_high,
                    discount_depth=round(random.uniform(0.05, 0.35), 2),
                    campaign_frequency=random.randint(1, 10),
                    captured_at=datetime(2026, 5, 7),
                )
                snapshots.append(s)
    db.bulk_save_objects(snapshots)
    db.commit()


# ═══════════════════════════════════════════════
# Master Generator
# ═══════════════════════════════════════════════

def generate_all(force: bool = False):
    """Generate all mock data. Skips if data already exists (unless force=True)."""
    init_db()
    db = SessionLocal()

    # Check if data already exists
    existing = db.query(Product).count()
    if existing > 0 and not force:
        print(f"[MockData] Data already exists ({existing} products). Skipping generation. Use force=True to regenerate.")
        db.close()
        return

    print("[MockData] Generating mock data...")
    print(f"  Districts: {settings.NUM_DISTRICTS}")
    print(f"  Products: {settings.NUM_PRODUCTS}")
    print(f"  Users: {settings.NUM_USERS}")
    print(f"  Months: {settings.NUM_MONTHS}")

    # 1. Districts
    districts = generate_districts(db)
    db.commit()
    print(f"  ✓ {len(districts)} districts")

    # 2. Products
    products = generate_products(db, settings.NUM_PRODUCTS)
    db.commit()
    print(f"  ✓ {len(products)} products")

    # 3. Orders (bulk)
    generate_orders(db, districts, products, settings.NUM_USERS, settings.NUM_MONTHS)
    print(f"  ✓ Orders generated (3 months funnel data)")

    # 4. Competitor snapshots
    generate_competitor_snapshots(db, districts)
    print(f"  ✓ Competitor snapshots")

    # 5. Diagnosis test anomalies
    inject_diagnosis_test_anomalies(db)
    print(f"  ✓ Diagnosis test anomalies injected")

    # 6. Phase 3 seed data: AB experiments + Agent run logs
    inject_phase3_seed_data(db)
    print(f"  ✓ Phase 3 seed data injected")

    db.close()
    print("[MockData] Done!")


# ═══════════════════════════════════════════════
# Diagnosis Test Anomaly Injection
# ═══════════════════════════════════════════════

def inject_diagnosis_test_anomalies(db):
    """Inject specific anomaly patterns for diagnosis agent testing.

    Creates anomalies in the last 7 days so the anomaly detection engine
    has something to find:

    District 1 (望京): CTR drop on recent 3 days (simulates bad creative)
    District 3 (国贸): CVR drop on recent 2 days (simulates checkout issue)
    District 2 (三里屯): GMV spike on 1 day (positive anomaly — campaign success)
    District 4 (中关村): Exposure drop on recent 3 days (budget exhausted)
    District 5 (五道口): Repurchase rate decline (user retention issue)
    """
    end_date = datetime(2026, 5, 7)
    user_ids = list(range(1, 10001))

    # ── District 1 (望京): CTR drop —正常曝光, 但点击下降 ──
    for day_offset in range(3):
        day = end_date - timedelta(days=day_offset)
        for _ in range(300):
            db.add(Order(
                user_id=random.choice(user_ids), sku_id=f"SKU_{random.randint(0, 199):04d}",
                campaign_id="DIAG_TEST", district_id=1, price=20,
                quantity=1, channel="search", funnel_stage="exposure",
                created_at=day + timedelta(hours=random.randint(10, 20)),
            ))
        # Intentionally low clicks — CTR should be detectably down
        for _ in range(20):
            db.add(Order(
                user_id=random.choice(user_ids), sku_id=f"SKU_{random.randint(0, 199):04d}",
                campaign_id="DIAG_TEST", district_id=1, price=20,
                quantity=1, channel="search", funnel_stage="click",
                created_at=day + timedelta(hours=random.randint(10, 20)),
            ))

    # ── District 3 (国贸): CVR drop — 加购正常, 但支付下降 ──
    for day_offset in range(2):
        day = end_date - timedelta(days=day_offset)
        for _ in range(200):
            db.add(Order(
                user_id=random.choice(user_ids), sku_id=f"SKU_{random.randint(0, 199):04d}",
                campaign_id="DIAG_TEST", district_id=3, price=35,
                quantity=1, channel="recommend", funnel_stage="click",
                created_at=day + timedelta(hours=random.randint(10, 20)),
            ))
        for _ in range(80):
            db.add(Order(
                user_id=random.choice(user_ids), sku_id=f"SKU_{random.randint(0, 199):04d}",
                campaign_id="DIAG_TEST", district_id=3, price=35,
                quantity=1, channel="recommend", funnel_stage="cart",
                created_at=day + timedelta(hours=random.randint(10, 20)),
            ))
        # Only 5 payments — CVR should be detectably down
        for _ in range(5):
            db.add(Order(
                user_id=random.choice(user_ids), sku_id=f"SKU_{random.randint(0, 199):04d}",
                campaign_id="DIAG_TEST", district_id=3, price=35,
                quantity=1, channel="recommend", funnel_stage="payment",
                created_at=day + timedelta(hours=random.randint(10, 20)),
            ))

    # ── District 2 (三里屯): GMV spike — 正常但高单价支付增加 (positive anomaly) ──
    for day_offset in range(1):
        day = end_date - timedelta(days=day_offset)
        for _ in range(100):
            db.add(Order(
                user_id=random.choice(user_ids), sku_id=f"SKU_{random.randint(0, 199):04d}",
                campaign_id="DIAG_TEST", district_id=2, price=random.uniform(80, 150),
                quantity=1, channel="social", funnel_stage="payment",
                created_at=day + timedelta(hours=random.randint(10, 20)),
            ))

    # ── District 4 (中关村): Exposure drop — 曝光量异常下降 ──
    for day_offset in range(3):
        day = end_date - timedelta(days=day_offset)
        # Very low exposure — should trigger absolute floor check
        for _ in range(30):
            db.add(Order(
                user_id=random.choice(user_ids), sku_id=f"SKU_{random.randint(0, 199):04d}",
                campaign_id="DIAG_TEST", district_id=4, price=15,
                quantity=1, channel="push", funnel_stage="exposure",
                created_at=day + timedelta(hours=random.randint(10, 20)),
            ))

    # ── District 5 (五道口): Repurchase drop — 支付正常, 复购下降 ──
    for day_offset in range(5):
        day = end_date - timedelta(days=day_offset)
        for _ in range(80):
            db.add(Order(
                user_id=random.choice(user_ids), sku_id=f"SKU_{random.randint(0, 199):04d}",
                campaign_id="DIAG_TEST", district_id=5, price=12,
                quantity=1, channel="search", funnel_stage="payment",
                created_at=day + timedelta(hours=random.randint(10, 20)),
            ))
        # Very few repurchases
        for _ in range(max(1, 5 - day_offset)):
            db.add(Order(
                user_id=random.choice(user_ids), sku_id=f"SKU_{random.randint(0, 199):04d}",
                campaign_id="DIAG_TEST", district_id=5, price=12,
                quantity=1, channel="search", funnel_stage="repurchase",
                created_at=day + timedelta(hours=random.randint(10, 20)),
            ))

    db.commit()
    print(f"  [AnomalyInjection] Injected test anomalies for all 5 districts")


# ═══════════════════════════════════════════════
# Phase 3 Seed Data: AB Experiments + Agent Run Logs
# ═══════════════════════════════════════════════

def inject_phase3_seed_data(db):
    """Seed AB experiment records and agent run logs for Phase 3 demo."""
    from app.db.models import ABExperiment, AgentRunLog

    # ── AB Experiments ──
    existing_ab = db.query(ABExperiment).count()
    if existing_ab == 0:
        experiments = [
            ABExperiment(
                experiment_name="望京 vs 三里屯 GMV 对比",
                hypothesis="三里屯高消费人群带来更高 GMV",
                control_group="1", treatment_group="2",
                metric_name="gmv",
                control_value=4520.50, treatment_value=5230.80,
                p_value=0.032, significant=True,
            ),
            ABExperiment(
                experiment_name="中关村 vs 五道口 CVR 对比",
                hypothesis="中关村白领转化率高于五道口学生",
                control_group="4", treatment_group="5",
                metric_name="cvr",
                control_value=0.155, treatment_value=0.118,
                p_value=0.008, significant=True,
            ),
            ABExperiment(
                experiment_name="国贸 vs 望京 AOV 对比",
                hypothesis="国贸高消费场景下客单价更高",
                control_group="3", treatment_group="1",
                metric_name="aov",
                control_value=58.30, treatment_value=42.10,
                p_value=0.001, significant=True,
            ),
            ABExperiment(
                experiment_name="三里屯 vs 国贸 CTR 对比",
                hypothesis="商圈流量差异影响点击率",
                control_group="2", treatment_group="3",
                metric_name="ctr",
                control_value=0.112, treatment_value=0.108,
                p_value=0.421, significant=False,
            ),
            ABExperiment(
                experiment_name="望京本地化文案 vs 通用文案 GMV 对比",
                hypothesis="本地化文案提升转化",
                control_group="1", treatment_group="1",
                metric_name="gmv",
                control_value=4100.00, treatment_value=4520.50,
                p_value=0.045, significant=True,
            ),
        ]
        for exp in experiments:
            db.add(exp)
        db.commit()
        print(f"  [Phase3Seed] Injected {len(experiments)} AB experiments")

    # ── Agent Run Logs (simulate a full workflow execution) ──
    existing_logs = db.query(AgentRunLog).count()
    if existing_logs == 0:
        campaign_id = "CAMP_20260507"
        base_time = datetime(2026, 5, 7, 10, 0, 0)

        workflow_runs = [
            ("planner_agent", "wf_plan", 850, 0.92, "low", "活动策划完成，目标：GMV提升20%"),
            ("selection_agent", "wf_select", 3200, 0.85, "medium", "选品完成，推荐50个SKU，建议剔除3个滞销品"),
            ("pricing_agent", "wf_price", 2100, 0.78, "medium", "定价方案生成，加权折扣22%，建议人工审核酒类"),
            ("copywriting_agent", "wf_copy", 1800, 0.81, "low", "3版文案生成，A版（紧迫感）推荐"),
            ("review_agent", "wf_review", 650, 0.90, "low", "审核通过，文案A版+定价方案B"),
            ("diagnosis_agent", "wf_diag", 4500, 0.76, "medium", "发现CTR异常下降，建议优化推送文案"),
        ]

        for i, (agent, t_id, latency, conf, risk, action) in enumerate(workflow_runs):
            log = AgentRunLog(
                run_id=f"{agent}_{campaign_id}_{i}",
                agent_name=agent,
                task_id=f"{t_id}_{campaign_id}",
                input_protocol={"campaign_id": campaign_id, "district_id": 1, "days": 7},
                output={
                    "result": {
                        "summary": f"{agent} output for {campaign_id}",
                        "selections": [] if agent != "selection_agent" else [
                            {"sku_id": f"SKU_{j:04d}", "score": round(random.uniform(0.6, 0.95), 2)}
                            for j in random.sample(range(200), 10)
                        ],
                    },
                    "confidence": conf,
                    "risk_level": risk,
                    "recommended_action": action,
                    "need_human_review": risk != "low",
                    "evidence": [f"Data from district 1, last 7 days", f"Confidence: {conf:.0%}"],
                },
                tokens_used=random.randint(500, 5000),
                latency_ms=latency,
                created_at=base_time + timedelta(minutes=i * 5),
            )
            db.add(log)
        db.commit()
        print(f"  [Phase3Seed] Injected {len(workflow_runs)} agent run logs")


if __name__ == "__main__":
    generate_all(force=True)
