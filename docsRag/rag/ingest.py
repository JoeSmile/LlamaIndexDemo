"""Batch file scan, dedup, and ingestion pipeline."""
import asyncio
import os
from typing import Any

from colorama import Fore
from llama_index.core import Document
from llama_index.core.ingestion import IngestionPipeline
from tqdm import tqdm

from rag.cache import CACHE_FORMAT, load_hash_cache, migrate_legacy_cache, save_hash_cache
from rag.config import (
    BATCH_SIZE,
    IGNORE_DIR,
    IGNORE_SUFFIX,
    MAX_FILE_MB,
    RETRY_TIMES,
    logger,
)
from rag.file_handlers import FILE_HANDLERS
from rag.models import embed_model
from rag.paths import (
    build_doc_metadata,
    make_source_key,
    normalize_abs_path,
    resolve_ingest_root,
    stable_doc_id,
)
from rag.store import delete_vectors_by_source_key, vector_store
from rag.utils import clear_memory, count_sources_with_hash, get_file_sha256


def apply_content_dedup_logic(
    source_key: str,
    file_name: str,
    file_hash: str,
    sources: dict[str, str],
    indexed_hashes: set[str],
    ingest_root: str,
) -> bool:
    cached = sources.get(source_key)

    if cached == file_hash:
        logger.info(
            Fore.GREEN + f"skip (unchanged): {file_name} [{source_key}]"
        )
        return True

    if file_hash in indexed_hashes:
        if cached is not None and cached != file_hash:
            delete_vectors_by_source_key(source_key)
            if count_sources_with_hash(cached, sources) == 1:
                indexed_hashes.discard(cached)
        sources[source_key] = file_hash
        save_hash_cache(sources, ingest_root)
        logger.info(
            Fore.GREEN
            + f"skip (duplicate content): {file_name} [{source_key}] sha256={file_hash[:12]}..."
        )
        return True

    if cached is not None and cached != file_hash:
        delete_vectors_by_source_key(source_key)
        if count_sources_with_hash(cached, sources) == 1:
            indexed_hashes.discard(cached)

    return False


def attach_document_identity(
    docs: list, meta: dict[str, Any], doc_id: str
) -> None:
    for i, doc in enumerate(docs):
        doc.metadata.update(meta)
        part_id = doc_id if len(docs) == 1 else f"{doc_id}_p{i}"
        for attr in ("id_", "doc_id"):
            if hasattr(doc, attr):
                try:
                    setattr(doc, attr, part_id)
                except Exception:
                    pass


async def process_single_file(
    file_path: str,
    ingest_root: str,
    sources: dict[str, str],
    indexed_hashes: set[str],
) -> None:
    abs_file = normalize_abs_path(file_path)
    suffix = os.path.splitext(abs_file)[-1].lower()
    file_name = os.path.basename(abs_file)
    source_key = make_source_key(abs_file, ingest_root)

    if MAX_FILE_MB > 0 and os.path.isfile(abs_file):
        size_mb = os.path.getsize(abs_file) / (1024 * 1024)
        if size_mb > MAX_FILE_MB:
            logger.warning(
                Fore.YELLOW + f"skip (size): {file_name} > {MAX_FILE_MB} MB"
            )
            return

    if suffix in IGNORE_SUFFIX:
        logger.warning(Fore.YELLOW + f"skip (suffix): {file_name}")
        return
    if suffix not in FILE_HANDLERS:
        logger.warning(Fore.YELLOW + f"skip (unsupported): {file_name}")
        return

    file_hash = get_file_sha256(abs_file)
    if apply_content_dedup_logic(
        source_key, file_name, file_hash, sources, indexed_hashes, ingest_root
    ):
        return

    handler = FILE_HANDLERS[suffix]
    retry_count = 0
    docs = None
    doc_id = stable_doc_id(source_key, file_hash)
    base_meta = build_doc_metadata(source_key, file_hash, file_name, ingest_root)

    while retry_count < RETRY_TIMES:
        try:
            logger.info(
                Fore.BLUE + f"ingest: {file_name} try={retry_count} key={source_key}"
            )
            reader = handler["reader"]
            if hasattr(reader, "load_data"):
                docs = reader.load_data(abs_file)
                attach_document_identity(docs, base_meta, doc_id)
            else:
                text = reader(abs_file)
                d = Document(text=text, metadata=base_meta.copy())
                attach_document_identity([d], base_meta, doc_id)
                docs = [d]
            break
        except Exception as e:
            retry_count += 1
            logger.error(
                Fore.RED + f"read fail {file_name}: {e} | retry {retry_count}/{RETRY_TIMES}"
            )
            await asyncio.sleep(1)

    if not docs:
        logger.critical(Fore.RED + f"gave up: {file_name}")
        return

    pipeline = IngestionPipeline(
        transformations=handler["split"] + [embed_model],
        vector_store=vector_store,
    )
    try:
        nodes = pipeline.run(documents=docs)
    except Exception as e:
        logger.error(Fore.RED + f"pipeline fail {file_name}: {e}")
        return

    logger.info(
        Fore.MAGENTA + f"chunks: {file_name} n={len(nodes)} | {source_key}"
    )
    try:
        with tqdm(total=len(nodes), desc=f"upsert-{file_name}", leave=False) as pbar:
            for i in range(0, len(nodes), BATCH_SIZE):
                batch = nodes[i : i + BATCH_SIZE]
                if hasattr(vector_store, "aadd"):
                    await vector_store.aadd(batch)
                else:
                    # Compatibility: some llama-index/chroma versions only expose sync add.
                    vector_store.add(batch)
                pbar.update(len(batch))
    except Exception as e:
        logger.error(Fore.RED + f"vector upsert fail {file_name}: {e}")
        return

    sources[source_key] = file_hash
    indexed_hashes.add(file_hash)
    save_hash_cache(sources, ingest_root)
    del docs, nodes, pipeline
    clear_memory()
    logger.info(Fore.GREEN + f"done: {file_name} [{source_key}]")


def scan_all_files(input_paths: list[str]) -> list[str]:
    all_files: list[str] = []
    for path in input_paths:
        ap = normalize_abs_path(path)
        if os.path.isfile(ap):
            all_files.append(ap)
        elif os.path.isdir(ap):
            for root, dirs, files in os.walk(ap):
                dirs[:] = [d for d in dirs if d not in IGNORE_DIR]
                for fn in files:
                    all_files.append(os.path.join(root, fn))
    return all_files


async def batch_process(input_paths: list[str]) -> None:
    if not input_paths:
        logger.error(Fore.RED + "ingest: need at least one path")
        return

    all_files = scan_all_files(input_paths)
    ingest_root = resolve_ingest_root(input_paths, all_files)

    raw_sources, fmt, cached_root = load_hash_cache()
    sources = dict(raw_sources)

    if fmt != CACHE_FORMAT:
        logger.info(Fore.CYAN + "Migrating legacy cache to source_key + v2 format")
        sources = migrate_legacy_cache(sources, ingest_root)
        save_hash_cache(sources, ingest_root)
    elif cached_root and normalize_abs_path(cached_root) != ingest_root:
        logger.warning(
            Fore.YELLOW
            + f"ingest_root differs from cache ({cached_root} vs {ingest_root}). "
            "Set RAG_INGEST_ROOT in .env to avoid duplicate indexing."
        )

    indexed_hashes = set(sources.values())
    logger.info(
        Fore.CYAN + f"files={len(all_files)} ingest_root={ingest_root}"
    )

    with tqdm(total=len(all_files), desc="ingest-all", leave=True) as gbar:
        for fp in all_files:
            try:
                await process_single_file(fp, ingest_root, sources, indexed_hashes)
            except Exception as e:
                logger.exception(Fore.RED + f"unexpected error on file {fp}: {e}")
            gbar.update(1)

    clear_memory()
    logger.info(Fore.GREEN + "batch ingest finished")
