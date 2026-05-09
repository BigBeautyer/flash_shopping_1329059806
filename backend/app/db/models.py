"""SQLAlchemy ORM models for flash sale business data."""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean,
    ForeignKey, Text, JSON, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from app.core.config import settings

Base = declarative_base()


# ── Campaign ──

class Campaign(Base):
    __tablename__ = "campaign"
    id = Column(String(64), primary_key=True)
    name = Column(String(256), nullable=False)
    goal_type = Column(String(32), nullable=False)  # new_user / clearance / gmv / roi
    city = Column(String(64))
    district = Column(String(128))
    budget = Column(Float)
    category_scope = Column(JSON)  # ["水果", "饮料"]
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    status = Column(String(32), default="draft")  # draft / running / finished / archived
    created_at = Column(DateTime, default=datetime.utcnow)

    tasks = relationship("CampaignTask", back_populates="campaign")
    approvals = relationship("CampaignApproval", back_populates="campaign")


class CampaignTask(Base):
    __tablename__ = "campaign_task"
    id = Column(String(64), primary_key=True)
    campaign_id = Column(String(64), ForeignKey("campaign.id"))
    agent_name = Column(String(64), nullable=False)
    task_protocol = Column(JSON)  # TaskProtocol JSON
    agent_output = Column(JSON)   # AgentOutput JSON
    status = Column(String(32), default="pending")  # pending / running / waiting_review / done / rejected
    created_at = Column(DateTime, default=datetime.utcnow)

    campaign = relationship("Campaign", back_populates="tasks")


class CampaignApproval(Base):
    __tablename__ = "campaign_approval"
    id = Column(String(64), primary_key=True)
    campaign_id = Column(String(64), ForeignKey("campaign.id"))
    task_id = Column(String(64), ForeignKey("campaign_task.id"))
    checkpoint_name = Column(String(64))  # selection_review / final_review
    approved = Column(Boolean, default=False)
    comment = Column(Text)
    approved_by = Column(String(64))
    approved_at = Column(DateTime)

    campaign = relationship("Campaign", back_populates="approvals")


# ── Business Data ──

class BusinessDistrict(Base):
    __tablename__ = "business_district"
    id = Column(Integer, primary_key=True)
    name = Column(String(128))
    city = Column(String(64))
    lbs_heat_index = Column(Float)
    office_residential_ratio = Column(Float)
    avg_consumption = Column(Float)
    competitor_density = Column(Float)
    peak_hours = Column(JSON)  # [11, 12, 17, 18]


class Product(Base):
    __tablename__ = "product"
    id = Column(Integer, primary_key=True)
    sku_id = Column(String(64), unique=True)
    name = Column(String(256))
    category = Column(String(64))
    cost_price = Column(Float)
    original_price = Column(Float)
    gross_margin = Column(Float)
    inventory = Column(Integer)
    shelf_life_days = Column(Integer)
    repurchase_rate = Column(Float)


class Order(Base):
    __tablename__ = "order"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    sku_id = Column(String(64))
    campaign_id = Column(String(64))
    district_id = Column(Integer)
    price = Column(Float)
    quantity = Column(Integer)
    channel = Column(String(32))
    funnel_stage = Column(String(32))  # exposure / click / cart / payment / repurchase
    created_at = Column(DateTime, default=datetime.utcnow)


class CompetitorSnapshot(Base):
    __tablename__ = "competitor_snapshot"
    id = Column(Integer, primary_key=True)
    district_id = Column(Integer)
    category = Column(String(64))
    competitor_name = Column(String(128))
    price_band_low = Column(Float)
    price_band_high = Column(Float)
    discount_depth = Column(Float)
    campaign_frequency = Column(Integer)
    captured_at = Column(DateTime, default=datetime.utcnow)


# ── Agent Governance ──

class AgentRunLog(Base):
    __tablename__ = "agent_run_log"
    id = Column(Integer, primary_key=True)
    run_id = Column(String(64))
    agent_name = Column(String(64))
    task_id = Column(String(64))
    input_protocol = Column(JSON)
    output = Column(JSON)
    tokens_used = Column(Integer)
    latency_ms = Column(Integer)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AgentFeedback(Base):
    __tablename__ = "agent_feedback"
    id = Column(Integer, primary_key=True)
    run_id = Column(String(64))
    agent_name = Column(String(64))
    task_id = Column(String(64))
    accepted = Column(Boolean)
    reject_reason = Column(Text, nullable=True)
    actual_gmv = Column(Float, nullable=True)
    actual_roi = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ABExperiment(Base):
    __tablename__ = "ab_experiment"
    id = Column(Integer, primary_key=True)
    experiment_name = Column(String(256))
    hypothesis = Column(Text)
    control_group = Column(String(64))
    treatment_group = Column(String(64))
    metric_name = Column(String(64))
    control_value = Column(Float)
    treatment_value = Column(Float)
    p_value = Column(Float)
    significant = Column(Boolean)
    created_at = Column(DateTime, default=datetime.utcnow)


class PlaybookCase(Base):
    __tablename__ = "playbook_case"
    id = Column(Integer, primary_key=True)
    title = Column(String(256))
    campaign_type = Column(String(64))
    district_type = Column(String(64))
    strategy_summary = Column(Text)
    key_metrics = Column(JSON)
    tags = Column(JSON)
    embedding_id = Column(String(64), nullable=True)  # ChromaDB doc id
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Engine ──

engine = create_engine(f"sqlite:///{settings.SQLITE_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """Create all tables."""
    import os
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    Base.metadata.create_all(engine)
