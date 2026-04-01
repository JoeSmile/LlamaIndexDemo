"""File hashing and memory logging."""
import gc
import hashlib
import os

import psutil
from colorama import Fore

from rag.config import logger


def get_file_sha256(file_path: str) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def clear_memory() -> None:
    gc.collect()
    proc = psutil.Process(os.getpid())
    mem = proc.memory_info().rss / 1024 / 1024
    logger.info(Fore.CYAN + f"RSS MB: {mem:.2f}")


def count_sources_with_hash(content_hash: str, sources: dict[str, str]) -> int:
    return sum(1 for h in sources.values() if h == content_hash)
