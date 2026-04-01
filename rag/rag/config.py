"""Environment, paths, logging."""
import logging
import os
import sys

from colorama import init
from dotenv import load_dotenv

init(autoreset=True)
load_dotenv()
os.environ.pop("OPENAI_API_KEY", None)

PERSIST_DIR = os.getenv("RAG_CHROMA_PATH", "./qwen_rag_data")
LOG_PATH = os.getenv("RAG_LOG_PATH", "./rag_process.log")
META_CACHE_PATH = os.getenv("RAG_CACHE_PATH", "./file_hash_cache.json")
RETRY_TIMES = int(os.getenv("RAG_RETRY_TIMES", "3"))
BATCH_SIZE = int(os.getenv("RAG_BATCH_SIZE", "4"))
MAX_FILE_MB = float(os.getenv("RAG_MAX_FILE_MB", "0"))

IGNORE_SUFFIX = {".zip", ".rar", ".7z", ".exe", ".dll", ".ds_store", ".tmp"}
IGNORE_DIR = {"__pycache__", ".venv", "venv", ".git", "qwen_rag_data", ".idea"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("rag")
