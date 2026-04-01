"""DashScope LLM + embedding (singletons)."""
import os
import sys

from colorama import Fore
from llama_index.embeddings.dashscope import DashScopeEmbedding
from llama_index.llms.dashscope import DashScope, DashScopeGenerationModels

from rag.config import logger

api_key = os.getenv("DASHSCOPE_API_KEY")
if not api_key:
    logger.error(Fore.RED + "\u8bf7\u5728 .env \u4e2d\u914d\u7f6e DASHSCOPE_API_KEY")
    sys.exit(1)

llm = DashScope(
    model_name=DashScopeGenerationModels.QWEN_TURBO,
    api_key=api_key,
    temperature=0.1,
)

embed_batch_size = int(os.getenv("RAG_EMBED_BATCH_SIZE", "10"))
if embed_batch_size > 10:
    logger.warning(Fore.YELLOW + "RAG_EMBED_BATCH_SIZE > 10, clamped to 10 for DashScope")
    embed_batch_size = 10
elif embed_batch_size <= 0:
    logger.warning(Fore.YELLOW + "RAG_EMBED_BATCH_SIZE <= 0, fallback to 10")
    embed_batch_size = 10

try:
    embed_model = DashScopeEmbedding(
        model_name="text-embedding-v4",
        api_key=api_key,
        embed_batch_size=embed_batch_size,
    )
except TypeError:
    # Backward compatibility for versions without embed_batch_size arg.
    embed_model = DashScopeEmbedding(model_name="text-embedding-v4", api_key=api_key)
