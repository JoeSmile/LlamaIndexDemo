"""Stable source_key, ingest root, document metadata."""
import hashlib
import os
from typing import Any

from colorama import Fore

from rag.config import logger


def _use_realpath() -> bool:
    return os.getenv("RAG_RESOLVE_SYMLINKS", "").strip() in ("1", "true", "True", "yes")


def normalize_abs_path(path: str) -> str:
    p = os.path.expanduser(path.strip())
    if _use_realpath():
        return os.path.realpath(p)
    return os.path.abspath(p)


def resolve_ingest_root(input_paths: list[str], scanned_files: list[str]) -> str:
    env_root = os.getenv("RAG_INGEST_ROOT", "").strip()
    if env_root:
        r = normalize_abs_path(env_root)
        if not os.path.isdir(r):
            logger.warning(
                Fore.YELLOW + f"RAG_INGEST_ROOT not a directory, fallback: {r}"
            )
        else:
            return r

    candidates: list[str] = []
    for p in input_paths:
        ap = normalize_abs_path(p)
        candidates.append(ap if os.path.isdir(ap) else os.path.dirname(ap))
    for f in scanned_files[: min(500, len(scanned_files))]:
        candidates.append(os.path.dirname(normalize_abs_path(f)))

    if not candidates:
        return normalize_abs_path(os.getcwd())

    try:
        return normalize_abs_path(os.path.commonpath(candidates))
    except ValueError:
        return normalize_abs_path(os.getcwd())


def make_source_key(abs_file: str, ingest_root: str) -> str:
    f = normalize_abs_path(abs_file)
    root = normalize_abs_path(ingest_root)
    try:
        if os.path.commonpath([f, root]) == root:
            rel = os.path.relpath(f, root)
            return rel.replace(os.sep, "/")
    except ValueError:
        pass
    h = hashlib.sha256(f.encode("utf-8")).hexdigest()[:24]
    return f"__external__/{h}"


def stable_doc_id(source_key: str, content_sha256: str) -> str:
    raw = f"{source_key}|{content_sha256}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_doc_metadata(
    source_key: str,
    content_sha256: str,
    file_name: str,
    ingest_root: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "source_key": source_key,
        "content_sha256": content_sha256,
        "file_name": file_name,
        "ingest_root": ingest_root,
        "doc_id": stable_doc_id(source_key, content_sha256),
    }
    if extra:
        for k, v in extra.items():
            if isinstance(v, (str, int, float, bool)):
                meta[k] = v
            else:
                meta[k] = str(v)
    return meta
