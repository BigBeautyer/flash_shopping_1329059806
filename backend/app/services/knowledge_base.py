"""RAG Knowledge Base — ChromaDB vector store for operational knowledge retrieval.

Phase 2: stores playbook cases, past campaign strategies, and anomaly resolution patterns.
Retrieved context enriches Agent prompts for better recommendations.

Architecture:
  Documents → Embedding (via DeepSeek API) → ChromaDB → Retrieval → Agent Context

Fallback: in-memory keyword search if ChromaDB or embedding model unavailable.
"""

from __future__ import annotations
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Suppress ChromaDB telemetry noise
os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "none")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

# ChromaDB imports — try, but don't fail if unavailable
HAS_CHROMA = False
try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
    HAS_CHROMA = True
except ImportError:
    logger.warning("[RAG] ChromaDB not installed. RAG features will use in-memory fallback.")


def _create_embedding_fn():
    """Create an embedding function using the configured LLM provider (DeepSeek/OpenAI).

    Uses OpenAI-compatible API so DeepSeek works via base_url override.
    """
    if not settings.LLM_API_KEY:
        return None

    try:
        kwargs = {
            "api_key": settings.LLM_API_KEY,
            "model_name": "text-embedding-3-small",  # OpenAI standard; DeepSeek may differ
        }
        if settings.LLM_PROVIDER != "openai":
            kwargs["api_base"] = settings.LLM_API_BASE
        return OpenAIEmbeddingFunction(**kwargs)
    except Exception as e:
        logger.warning(f"[RAG] Failed to create embedding function: {e}")
        return None


class KnowledgeBase:
    """Vector store for operational knowledge documents.

    Collection: "playbooks" — stores campaign strategies, best practices, anomaly fixes.

    Uses ChromaDB with sentence-transformers or OpenAI embeddings.
    For MVP: simple TF-IDF fallback if ChromaDB not available.
    """

    COLLECTION_NAME = "flash_sale_playbooks"

    def __init__(self, persist_dir: str | None = None, use_chroma: bool = True):
        self.persist_dir = persist_dir or settings.CHROMA_PATH
        self._docs: List[dict] = []
        self._client = None
        self._collection = None
        self.use_chroma = False  # default to in-memory; try chroma below

        if use_chroma and HAS_CHROMA:
            try:
                Path(self.persist_dir).mkdir(parents=True, exist_ok=True)
                # Use API-based embedding to avoid downloading local models
                embedding_fn = _create_embedding_fn()

                self._client = chromadb.PersistentClient(
                    path=self.persist_dir,
                    settings=ChromaSettings(anonymized_telemetry=False),
                )

                if embedding_fn:
                    self._collection = self._client.get_or_create_collection(
                        name=self.COLLECTION_NAME,
                        embedding_function=embedding_fn,
                        metadata={"description": "闪购运营知识库 — Playbook案例、策略、异常修复方案"},
                    )
                else:
                    # No embedding function available → try default (will likely fail without network)
                    logger.warning("[RAG] No API key for embeddings, trying ChromaDB default...")
                    self._collection = self._client.get_or_create_collection(
                        name=self.COLLECTION_NAME,
                        metadata={"description": "闪购运营知识库 — Playbook案例、策略、异常修复方案"},
                    )

                self.use_chroma = True
                logger.info(f"[RAG] ChromaDB initialized at {self.persist_dir}")
            except Exception as e:
                logger.warning(f"[RAG] ChromaDB init failed ({e}), falling back to in-memory mode")
                self.use_chroma = False

        if not self.use_chroma:
            logger.info("[RAG] Using in-memory keyword search fallback")

    # ── Ingestion ──

    def ingest_document(
        self,
        title: str,
        content: str,
        doc_type: str = "playbook",  # playbook / strategy / anomaly_fix / campaign_result
        tags: List[str] | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Ingest a document into the knowledge base.

        Returns doc_id.
        """
        doc_id = f"doc_{uuid.uuid4().hex[:12]}"
        tags = tags or []
        meta = metadata or {}

        if self.use_chroma and self._collection:
            try:
                self._collection.add(
                    ids=[doc_id],
                    documents=[content],
                    metadatas=[{
                        "title": title,
                        "doc_type": doc_type,
                        "tags": json.dumps(tags, ensure_ascii=False),
                        "ingested_at": datetime.utcnow().isoformat(),
                        **meta,
                    }],
                )
            except Exception as e:
                logger.warning(f"[RAG] ChromaDB ingest failed ({e}), storing in memory")
                self._docs.append({
                    "id": doc_id, "title": title, "content": content,
                    "doc_type": doc_type, "tags": tags, "metadata": meta,
                    "ingested_at": datetime.utcnow().isoformat(),
                })
        else:
            self._docs.append({
                "id": doc_id,
                "title": title,
                "content": content,
                "doc_type": doc_type,
                "tags": tags,
                "metadata": meta,
                "ingested_at": datetime.utcnow().isoformat(),
            })

        logger.info(f"[RAG] Ingested: {title} ({doc_type})")
        return doc_id

    def ingest_batch(self, documents: List[dict]) -> List[str]:
        """Ingest multiple documents at once.

        Each doc: {title, content, doc_type, tags?, metadata?}
        """
        doc_ids = []
        for doc in documents:
            doc_id = self.ingest_document(
                title=doc.get("title", "Untitled"),
                content=doc.get("content", ""),
                doc_type=doc.get("doc_type", "playbook"),
                tags=doc.get("tags"),
                metadata=doc.get("metadata"),
            )
            doc_ids.append(doc_id)
        return doc_ids

    # ── Retrieval ──

    def retrieve(
        self,
        query: str,
        doc_type: str | None = None,
        top_k: int = 5,
    ) -> List[dict]:
        """Retrieve most relevant documents for a query.

        Args:
            query: natural language search query
            doc_type: filter by document type (playbook / strategy / anomaly_fix)
            top_k: number of results
        """
        if self.use_chroma and self._collection:
            where_filter = None
            if doc_type:
                where_filter = {"doc_type": doc_type}

            try:
                results = self._collection.query(
                    query_texts=[query],
                    n_results=top_k,
                    where=where_filter,
                )
            except Exception as e:
                logger.warning(f"[RAG] ChromaDB query failed ({e}), using in-memory")
                return self._simple_search(query, doc_type, top_k)

            docs = []
            if results and results.get("ids") and results["ids"][0]:
                for i, doc_id in enumerate(results["ids"][0]):
                    docs.append({
                        "doc_id": doc_id,
                        "title": results["metadatas"][0][i].get("title", ""),
                        "content": results["documents"][0][i],
                        "doc_type": results["metadatas"][0][i].get("doc_type", ""),
                        "tags": json.loads(results["metadatas"][0][i].get("tags", "[]")),
                        "relevance": round(1.0 - i * 0.05, 2),  # approximate
                    })
            return docs
        else:
            # In-memory: simple keyword matching fallback
            return self._simple_search(query, doc_type, top_k)

    def retrieve_for_context(
        self,
        campaign_goal: str = "",
        district_type: str = "",
        category_scope: List[str] | None = None,
        top_k: int = 3,
    ) -> str:
        """Retrieve context documents and format them for Agent prompt injection.

        Returns formatted string ready to append to system prompt.
        """
        query_parts = []
        if campaign_goal:
            goal_labels = {"gmv": "提升GMV", "roi": "提升ROI", "new_user": "拉新获客", "clearance": "清库存"}
            query_parts.append(goal_labels.get(campaign_goal, campaign_goal))
        if district_type:
            query_parts.append(district_type)
        if category_scope:
            query_parts.append(" ".join(category_scope[:3]))

        query = "闪购活动 " + " ".join(query_parts) if query_parts else "闪购运营策略"

        docs = self.retrieve(query, top_k=top_k)
        if not docs:
            # Broader search
            docs = self.retrieve("闪购 选品 定价 运营", top_k=top_k)

        if not docs:
            return ""

        parts = ["## 📚 知识库参考\n以下为历史上类似活动的经验参考：\n"]
        for i, doc in enumerate(docs, 1):
            parts.append(f"### {i}. {doc['title']}")
            # Truncate long content for prompt context
            content = doc.get("content", "")
            if len(content) > 800:
                content = content[:800] + "..."
            parts.append(content)
            parts.append("")

        return "\n".join(parts)

    def get_stats(self) -> dict:
        """Return knowledge base statistics."""
        if self.use_chroma and self._collection:
            try:
                count = self._collection.count()
            except Exception:
                count = len(self._docs)
        else:
            count = len(self._docs)

        return {
            "total_documents": count,
            "use_chroma": self.use_chroma,
            "persist_dir": str(self.persist_dir),
        }

    # ── Helpers ──

    def _simple_search(self, query: str, doc_type: str | None, top_k: int) -> List[dict]:
        """Simple keyword-based search for in-memory fallback."""
        query_terms = set(query.lower().split())
        scored = []
        for doc in self._docs:
            if doc_type and doc.get("doc_type") != doc_type:
                continue
            content_lower = doc["content"].lower()
            title_lower = doc["title"].lower()
            score = sum(
                2 if term in title_lower else 1 if term in content_lower else 0
                for term in query_terms
            )
            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "doc_id": d["id"],
                "title": d["title"],
                "content": d["content"],
                "doc_type": d.get("doc_type", ""),
                "tags": d.get("tags", []),
                "relevance": round(s / max(1, sum(1 for _ in query_terms) * 2), 2),
            }
            for s, d in scored[:top_k]
        ]


# ── Playbook Seed Data ──

SEED_PLAYBOOKS = [
    {
        "title": "午间闪购·水果品类·提GMV策略",
        "content": """
## 策略摘要
针对午间时段（11:00-13:00）的水果闪购活动，通过精选高周转水果 + 组合定价 + 限时折扣，
在望京商圈实现GMV提升35%。

## 选品策略
- 优先选择周转天数<3天的应季水果（草莓、蓝莓、车厘子）
- 搭配1-2款引流水果（香蕉、苹果）低毛利拉流量
- 控制水果品类毛利率不低于18%

## 定价策略
- 引流款：低于竞品均价10-15%
- 利润款：与竞品持平，强调品质和新鲜度
- 组合装：2-3款水果打包，客单价提升至35-45元

## 关键指标
- 客单价：38元
- 毛利率：22%
- 转化率：18%
- 复购率：12%

## 注意事项
- 水果库存需实时同步，避免超卖
- 夏季水果需冷链配送，注意履约时效
- 午间时段推送文案突出"新鲜"和"快速送达"
""",
        "doc_type": "playbook",
        "tags": ["水果", "午间", "GMV", "望京"],
    },
    {
        "title": "晚高峰·饮料品类·拉新策略",
        "content": """
## 策略摘要
晚高峰时段（17:00-19:00）针对办公商圈（国贸、中关村），通过饮料品类做新客拉新活动，
新客首单立减 + 社交裂变，单次活动拉新成本控制在8元/人。

## 选品策略
- 咖啡、功能饮料等高复购品类作为主推
- 搭配网红饮料（气泡水、椰子水）吸引年轻用户
- 排除库存<50的低周转SKU

## 定价策略
- 新客首单：满20减8
- 老客复购：满30减5
- 社交裂变：分享得3元券（次日可用）

## 关键指标
- 新客获客成本：7.5元
- 7日复购率：22%
- 分享率：15%

## 注意事项
- 新客券需限制使用时段，防止被刷
- 办公商圈周末流量低，活动集中在工作日
- 裂变文案需简洁有力，突出"马上送到办公室"
""",
        "doc_type": "playbook",
        "tags": ["饮料", "晚高峰", "拉新", "国贸", "中关村"],
    },
    {
        "title": "周末·零食品类·清库存策略",
        "content": """
## 策略摘要
周末时段（周六日全天）针对居民商圈（三里屯），通过零食组合装 + 阶梯折扣清临期库存，
3天消化80%临期库存，毛利率维持在15%以上。

## 选品策略
- 筛选剩余保质期<30天的零食SKU
- 按品牌/品类组合打包（进口零食包、国产经典包）
- 搭配1款热销非临期品作为钩子

## 定价策略
- 临期30天：7折
- 临期15天：5折
- 临期7天：3折 + 买一送一
- 组合包：原价100元，活动价59元

## 关键指标
- 库存消化率：82%
- 毛利率：16%
- 客诉率：<2%（需标注保质期）

## 注意事项
- 页面必须显著标注保质期和"清仓特惠"
- 禁止将临期品混入正常商品推荐流
- 配送时效要求更高（用户期望尽快收到）
""",
        "doc_type": "playbook",
        "tags": ["零食", "周末", "清库存", "三里屯"],
    },
    {
        "title": "异常诊断·CTR骤降·修复案例",
        "content": """
## 问题描述
望京商圈某次活动中，CTR从正常的8.5%骤降至3.2%，曝光量正常但点击量断崖式下跌。

## 诊断过程
1. 漏斗定位：曝光量正常（12万/天）→ 点击层异常（CTR 3.2%）
2. 素材排查：发现推送文案误用了上一期活动的旧价格，用户看到的价格与落地页不符
3. 人群排查：定向无问题，排除人群错配
4. 竞品排查：竞品同期未有大规模投放

## 根因
文案中的折扣信息与落地页实际价格不一致，导致用户预览后不点击。
运营在复制上期活动模板时未更新折扣数字。

## 修复方案
1. 立即暂停活动，修正文案中的价格信息（2小时内完成）
2. 加强活动上线前的文案审核流程，增加"价格一致性校验"卡点
3. 系统侧增加自动校验：文案中的数字与定价方案自动比对

## 修复后效果
- 修复后CTR恢复至8.2%（基本回到正常水平）
- 因中断损失的曝光量约3万，通过延长活动时间1天补偿

## 预防措施
- 建立文案-定价自动校验机制
- 活动模板增加"关键数字"高亮标记
- 上线前强制预览检查
""",
        "doc_type": "anomaly_fix",
        "tags": ["CTR", "文案", "异常", "望京"],
    },
    {
        "title": "异常诊断·CVR下降·修复案例",
        "content": """
## 问题描述
国贸商圈某次活动中，加购到支付的转化率从19%降至8%，但加购量正常。

## 诊断过程
1. 漏斗定位：加购层正常 → 支付层异常（CVR 8%）
2. 结算链路排查：支付接口正常，但优惠券系统响应时间从200ms升至3s
3. 优惠券排查：大量用户领取了优惠券但在结算页无法使用（门槛判断延迟）
4. 库存排查：部分热门SKU库存不足，用户加购后无法结算

## 根因
双根因：(1) 优惠券服务Redis连接池耗尽，导致门槛判断超时；
(2) 头3个热门SKU库存<10件，但前端仍显示"有货"

## 修复方案
1. 紧急扩容Redis连接池（从10扩至50）
2. 库存同步从5分钟调整为实时（WebSocket推送）
3. 库存<20件时前端显示"仅剩X件"并限制加购数量
4. 优惠券结算增加3秒超时兜底（超时自动适用）

## 修复后效果
- CVR恢复至17%（略低于正常水平，用户信心受损需时间恢复）
- 优惠券服务p99延迟从3s降至200ms

## 预防措施
- 活动前做库存压测，确保热门SKU备货充足
- 优惠券系统增加熔断机制
- 库存预警阈值从<10调整为<30
""",
        "doc_type": "anomaly_fix",
        "tags": ["CVR", "优惠券", "库存", "异常", "国贸"],
    },
    {
        "title": "选品策略·高毛利组合·ROI提升",
        "content": """
## 策略摘要
通过"高毛利品+引流品"组合选品策略，在保持曝光和点击的同时提升整体毛利率。
适用场景：活动目标为ROI提升或毛利改善。

## 商品组合原则
- 引流品（占比30%）：低毛利（<15%）、高转化、高曝光品类（如香蕉、矿泉水）
- 利润品（占比50%）：高毛利（>30%）、中等转化、稳定需求（如进口零食、精酿啤酒）
- 钩子品（占比20%）：超高毛利（>40%）、低转化但高客单价（如礼盒、限定款）

## 定价梯队
- 第一梯队（引流）：低于市场均价10-15%
- 第二梯队（利润）：与市场均价持平
- 第三梯队（钩子）：高于市场均价5-10%，强调稀缺性

## 效果验证
- 整体毛利率从18%提升至24%
- GMV保持稳定（引流品GMV下降被利润品提升抵消）
- 客单价从28元提升至35元

## 注意事项
- 引流品数量控制在5-8个，过多会稀释利润
- 钩子品必须有足够的差异化（产地直采、限定包装等）
- 定期轮换引流品，避免用户只买引流品不复购
""",
        "doc_type": "strategy",
        "tags": ["ROI", "选品", "定价", "毛利"],
    },
]


def seed_knowledge_base(kb: KnowledgeBase | None = None) -> KnowledgeBase:
    """Seed the knowledge base with initial playbook documents."""
    if kb is None:
        kb = KnowledgeBase()

    existing_count = kb.get_stats().get("total_documents", 0)
    if existing_count > 0:
        logger.info(f"[RAG] Knowledge base already has {existing_count} documents. Skipping seed.")
        return kb

    kb.ingest_batch(SEED_PLAYBOOKS)
    logger.info(f"[RAG] Seeded {len(SEED_PLAYBOOKS)} playbook documents")
    return kb
