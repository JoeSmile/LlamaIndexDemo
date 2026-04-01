"""Query engine and chat loop."""
import os

from colorama import Fore
from llama_index.core import VectorStoreIndex

from rag.models import embed_model, llm
from rag.store import vector_store


def _response_to_text(res) -> str:
    """Normalize different LlamaIndex response objects into plain text."""
    if hasattr(res, "response") and res.response:
        return str(res.response)
    if hasattr(res, "message") and getattr(res.message, "content", None):
        return str(res.message.content)
    return str(res)


def _is_true(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _read_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        val = int(raw)
    except ValueError:
        val = default
    return max(val, minimum)


def _print_source_nodes(res) -> None:
    """Print retrieved source chunks for debugging/verification."""
    nodes = getattr(res, "source_nodes", None) or []
    if not nodes:
        print(Fore.YELLOW + "\n[Sources] none")
        return

    full_output = _is_true(os.getenv("RAG_QUERY_FULL_OUTPUT", "0"))
    max_nodes = len(nodes) if full_output else int(os.getenv("RAG_QUERY_MAX_SOURCE_NODES", "5"))
    max_chars = 0 if full_output else int(os.getenv("RAG_QUERY_SOURCE_MAX_CHARS", "2000"))
    print(Fore.CYAN + f"\n[Sources] showing {min(len(nodes), max_nodes)}/{len(nodes)}")
    for i, sn in enumerate(nodes[:max_nodes], 1):
        meta = getattr(getattr(sn, "node", None), "metadata", {}) or {}
        text = ""
        if hasattr(sn, "get_content"):
            try:
                text = sn.get_content()
            except Exception:
                text = ""
        if not text:
            text = getattr(getattr(sn, "node", None), "text", "") or ""
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars] + "...(truncated)"
        source_key = meta.get("source_key", "unknown")
        score = getattr(sn, "score", None)
        score_txt = f"{score:.4f}" if isinstance(score, (int, float)) else "n/a"
        print(Fore.CYAN + f"\n[{i}] source_key={source_key} score={score_txt}")
        print(Fore.WHITE + text)


async def query_answer(question: str) -> None:
    index = VectorStoreIndex.from_vector_store(vector_store, embed_model=embed_model)
    top_k = _read_int_env("RAG_QUERY_TOP_K", 5, minimum=1)
    engine = index.as_query_engine(llm=llm, similarity_top_k=top_k)
    res = await engine.aquery(question)
    print(Fore.WHITE + "\nAnswer:", _response_to_text(res))
    if _is_true(os.getenv("RAG_QUERY_SHOW_SOURCES", "1")):
        _print_source_nodes(res)


async def chat_loop() -> None:
    index = VectorStoreIndex.from_vector_store(vector_store, embed_model=embed_model)
    top_k = _read_int_env("RAG_CHAT_TOP_K", _read_int_env("RAG_QUERY_TOP_K", 5), minimum=1)
    engine = index.as_chat_engine(llm=llm, similarity_top_k=top_k)
    print(Fore.MAGENTA + "\nChat (exit / quit to leave)")
    while True:
        # Use plain prompt text to avoid terminal/ANSI editing glitches.
        q = input("> ")
        if q.lower() in ("exit", "quit"):
            print(Fore.GREEN + "bye")
            break
        res = await engine.achat(q)
        print(Fore.WHITE + "Bot:", _response_to_text(res))
        if _is_true(os.getenv("RAG_QUERY_SHOW_SOURCES", "0")):
            _print_source_nodes(res)
