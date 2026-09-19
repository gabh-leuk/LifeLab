"""向量化抽象层：支持 Ollama 本地 与 OpenAI 兼容 API，自动回退。

配置优先级（settings.embedding_provider）：
- "auto"（默认）：先试 Ollama（本地/免费），失败则回退 OpenAI 兼容 API
- "ollama"：只用 Ollama
- "openai"：只用 OpenAI 兼容 API（需 embedding_api_key）
"""

import json
import urllib.request
from functools import lru_cache

from app.config import get_settings

# 各 provider 的向量维度（Unicode 存储，仅供参考；写入前按 settings.embedding_dim 校验）
_PROVIDER_DIM = {
    "ollama_bge-m3": 1024,
    "openai_text-embedding-3-small": 1536,
    "openai_text-embedding-3-large": 3072,
}


class EmbeddingError(Exception):
    pass


def _post_json(url: str, payload: dict, headers: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise EmbeddingError(f"embedding HTTP 调用失败: {e}") from e


def _embed_ollama(text: str) -> list[float]:
    s = get_settings()
    data = _post_json(
        f"{s.embedding_ollama_url}/api/embed",
        {"model": s.embedding_ollama_model, "input": text},
        {},
    )
    # Ollama /api/embed 返回 {"embeddings": [[...]]} 或 {"embedding": [...]}
    emb = data.get("embeddings") or data.get("embedding")
    if not emb:
        raise EmbeddingError("Ollama 未返回 embedding")
    return emb[0] if isinstance(emb[0], list) else emb


def _embed_openai(text: str) -> list[float]:
    s = get_settings()
    if not s.embedding_api_key or not s.embedding_api_url:
        raise EmbeddingError("未配置 embedding 云 API（embedding_api_key/url）")
    data = _post_json(
        f"{s.embedding_api_url.rstrip('/')}/embeddings",
        {"model": s.embedding_api_model, "input": text},
        {"Authorization": f"Bearer {s.embedding_api_key}"},
    )
    try:
        return data["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError):
        raise EmbeddingError("embedding API 响应格式异常")


@lru_cache(maxsize=256)
def embed(text: str) -> list[float]:
    """输入文本 → 向量。按 settings.embedding_provider 选择来源。"""
    s = get_settings()
    provider = s.embedding_provider.lower()
    errors = []

    if provider in ("auto", "ollama"):
        try:
            vec = _embed_ollama(text)
            if len(vec) != s.embedding_dim:
                raise EmbeddingError(
                    f"Ollama 向量维度 {len(vec)} != 配置 {s.embedding_dim}"
                )
            return vec
        except EmbeddingError as e:
            errors.append(f"ollama: {e}")
            if provider == "ollama":
                raise

    if provider in ("auto", "openai"):
        try:
            vec = _embed_openai(text)
            if len(vec) != s.embedding_dim:
                raise EmbeddingError(f"API 向量维度 {len(vec)} != 配置 {s.embedding_dim}")
            return vec
        except EmbeddingError as e:
            errors.append(f"openai: {e}")
            if provider == "openai":
                raise

    raise EmbeddingError(" / ".join(errors) or "未配置任何 embedding provider")