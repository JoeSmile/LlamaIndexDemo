"""On-disk hash cache v2 (atomic write)."""
import json
import os

from colorama import Fore

from rag.config import META_CACHE_PATH, logger
from rag.paths import make_source_key, normalize_abs_path

CACHE_FORMAT = 2


def load_hash_cache() -> tuple[dict[str, str], int | None, str | None]:
    if not os.path.exists(META_CACHE_PATH):
        return {}, None, None
    with open(META_CACHE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and data.get("_format") == CACHE_FORMAT:
        sources = data.get("sources") or {}
        root = data.get("ingest_root")
        return {str(k): str(v) for k, v in sources.items()}, CACHE_FORMAT, root

    if isinstance(data, dict) and "_format" not in data:
        return {str(k): str(v) for k, v in data.items()}, 1, None

    logger.warning(Fore.YELLOW + "Unknown cache format, starting fresh mapping")
    return {}, None, None


def migrate_legacy_cache(
    legacy: dict[str, str], ingest_root: str
) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, h in legacy.items():
        if k.startswith("__") and "/" not in k:
            continue
        try:
            abs_k = normalize_abs_path(k)
            sk = make_source_key(abs_k, ingest_root)
            out[sk] = h
        except Exception as e:
            logger.warning(Fore.YELLOW + f"Migrate cache key skip: {k} | {e}")
    return out


def save_hash_cache(sources: dict[str, str], ingest_root: str) -> None:
    payload = {
        "_format": CACHE_FORMAT,
        "ingest_root": ingest_root,
        "sources": dict(sorted(sources.items())),
    }
    tmp = META_CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, META_CACHE_PATH)
