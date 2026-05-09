"""Data service abstraction layer.

All data access goes through this service. When switching from mock data to real
data sources, only this module's implementation changes — Agent logic stays intact (ADR-002).
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import List, Optional
import pandas as pd

from app.db.models import SessionLocal, Order, Product, BusinessDistrict, CompetitorSnapshot


class DataService(ABC):
    """Abstract base for data access. Implement for mock or real data sources."""

    @abstractmethod
    def get_funnel_data(self, district_id: int, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        ...

    @abstractmethod
    def get_product_features(self, sku_ids: List[str]) -> pd.DataFrame:
        ...

    @abstractmethod
    def get_district_profile(self, district_id: int) -> dict:
        ...

    @abstractmethod
    def get_competitor_data(self, district_id: int, category: str) -> pd.DataFrame:
        ...


class MockDataService(DataService):
    """SQLite-based mock data service."""

    def get_funnel_data(self, district_id: int, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        db = SessionLocal()
        try:
            orders = db.query(Order).filter(
                Order.district_id == district_id,
                Order.created_at >= start_date,
                Order.created_at <= end_date,
            ).all()
            if not orders:
                return pd.DataFrame(columns=["user_id", "sku_id", "funnel_stage", "price", "quantity", "channel", "created_at"])
            data = [{
                "user_id": o.user_id,
                "sku_id": o.sku_id,
                "funnel_stage": o.funnel_stage,
                "price": o.price or 0,
                "quantity": o.quantity or 1,
                "channel": o.channel,
                "created_at": o.created_at,
            } for o in orders]
            return pd.DataFrame(data)
        finally:
            db.close()

    def get_product_features(self, sku_ids: List[str]) -> pd.DataFrame:
        db = SessionLocal()
        try:
            products = db.query(Product).filter(Product.sku_id.in_(sku_ids)).all()
            data = [{
                "sku_id": p.sku_id,
                "name": p.name,
                "category": p.category,
                "cost_price": p.cost_price,
                "original_price": p.original_price,
                "gross_margin": p.gross_margin,
                "inventory": p.inventory,
                "shelf_life_days": p.shelf_life_days,
                "repurchase_rate": p.repurchase_rate,
            } for p in products]
            return pd.DataFrame(data)
        finally:
            db.close()

    def get_district_profile(self, district_id: int) -> dict:
        db = SessionLocal()
        try:
            d = db.query(BusinessDistrict).filter(BusinessDistrict.id == district_id).first()
            if not d:
                return {}
            return {
                "id": d.id,
                "name": d.name,
                "city": d.city,
                "lbs_heat_index": d.lbs_heat_index,
                "office_residential_ratio": d.office_residential_ratio,
                "avg_consumption": d.avg_consumption,
                "competitor_density": d.competitor_density,
                "peak_hours": d.peak_hours,
            }
        finally:
            db.close()

    def get_competitor_data(self, district_id: int, category: str) -> pd.DataFrame:
        db = SessionLocal()
        try:
            comps = db.query(CompetitorSnapshot).filter(
                CompetitorSnapshot.district_id == district_id,
                CompetitorSnapshot.category == category,
            ).all()
            data = [{
                "competitor_name": c.competitor_name,
                "price_band_low": c.price_band_low,
                "price_band_high": c.price_band_high,
                "discount_depth": c.discount_depth,
                "campaign_frequency": c.campaign_frequency,
            } for c in comps]
            return pd.DataFrame(data)
        finally:
            db.close()

    def get_all_products(self) -> pd.DataFrame:
        db = SessionLocal()
        try:
            products = db.query(Product).all()
            return pd.DataFrame([{
                "sku_id": p.sku_id, "name": p.name, "category": p.category,
                "cost_price": p.cost_price, "original_price": p.original_price,
                "gross_margin": p.gross_margin, "inventory": p.inventory,
                "shelf_life_days": p.shelf_life_days, "repurchase_rate": p.repurchase_rate,
            } for p in products])
        finally:
            db.close()

    def get_all_districts(self) -> pd.DataFrame:
        db = SessionLocal()
        try:
            districts = db.query(BusinessDistrict).all()
            return pd.DataFrame([{
                "id": d.id, "name": d.name, "city": d.city,
                "lbs_heat_index": d.lbs_heat_index,
                "office_residential_ratio": d.office_residential_ratio,
                "avg_consumption": d.avg_consumption,
                "competitor_density": d.competitor_density,
                "peak_hours": d.peak_hours,
            } for d in districts])
        finally:
            db.close()
