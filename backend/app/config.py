from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 锚到 backend/.env，而不是相对 cwd 找 —— 「从别的目录启动」时相对路径会静默失效，
# 而失效的后果不是报错：配置回落默认值，`database_url` 变成 sqlite:///./lifelab.db，
# 于是拿一个空 SQLite 当生产库用。计划任务（WorkingDirectory 由任务定义决定）、
# 服务、将来搬到 VPS 的 systemd 都会踩这个坑。
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8")

    database_url: str = "sqlite:///./lifelab.db"
    app_name: str = "LifeLab API"
    # 公网部署默认关闭 /docs 与 /openapi.json：不暴露全量接口清单和
    # 「Try it out」控制台。本地要看时在 backend/.env 写 ENABLE_DOCS=true 重启。
    enable_docs: bool = False

    llm_api_key: str = ""
    llm_base_url: str = "https://api.edgefn.net/v1"
    llm_model: str = "DeepSeek-V4-Flash"

    # ── LLM 调用埋点（可观测） ────────────────────────────
    # 每次 LLM 调用追一行 JSONL（见 app/llm.py）。用量汇总：scripts/llm_usage.py
    llm_trace_enabled: bool = True
    llm_trace_path: str = ""  # 空 = ~/.lifelab/llm_calls.jsonl

    # 向量化配置（记忆检索用）
    # 优先 Ollama 本地；未设置 ollama 地址则回退 OpenAI 兼容 API
    embedding_provider: str = "auto"  # auto | ollama | openai
    embedding_ollama_url: str = "http://localhost:11434"
    embedding_ollama_model: str = "bge-m3"
    embedding_api_key: str = ""  # OpenAI 兼容 embedding 的 key（如 SiliconFlow）
    embedding_api_url: str = ""
    embedding_api_model: str = ""
    embedding_dim: int = 2560  # halfvec 维度（qwen3-embedding:4b = 2560）

    # ── RAG 检索策略 ──────────────────────────────────────
    # 相似度门槛：低于此值的候选直接丢弃（宁缺毋滥）。
    # qwen3-embedding 实测：正相关 top ≥0.64，无关查询 top ≤0.52
    rag_min_similarity: float = 0.56
    # 时间权重：final = (1-w)*相似度 + w*时间新鲜度（加法混合）
    rag_time_weight: float = 0.2
    # 候选量：先取 top_k * multiplier（上限 max_candidates）再过滤排序
    rag_candidate_multiplier: int = 3
    rag_max_candidates: int = 20
    # 同 kind 去重阈值：新条目与已有条目余弦 ≥ 此值时跳过写入
    rag_dedup_threshold: float = 0.92

    # ── 画像晋升（向量库 → 用户背景候选） ────────────────
    # 交叉验证过的聚类阈值：pattern 0.75（跨主题链式误聚在 0.70 出现）；
    # insight 0.86（0.82 会把「凌晨」与「上午」主题连成一簇）
    promotion_pattern_similarity: float = 0.75
    promotion_insight_similarity: float = 0.86
    promotion_max_candidates: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()
