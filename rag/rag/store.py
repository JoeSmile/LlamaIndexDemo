"""Chroma persistent client and vector store."""
import chromadb
from colorama import Fore
from llama_index.vector_stores.chroma import ChromaVectorStore

from rag.config import PERSIST_DIR, logger

chroma_client = chromadb.PersistentClient(path=PERSIST_DIR)
chroma_collection = chroma_client.get_or_create_collection("qwen_rag")
vector_store = ChromaVectorStore(chroma_collection=chroma_collection)


def delete_vectors_by_source_key(source_key: str) -> None:
    try:
        chroma_collection.delete(where={"source_key": {"$eq": source_key}})
    except Exception as e:
        logger.warning(
            Fore.YELLOW + f"delete by source_key failed: {source_key} | {e}"
        )
