"""CLI entry: usage + command dispatch."""
import asyncio
import os
import sys

from colorama import Fore

from rag.ingest import batch_process
from rag.query import chat_loop, query_answer


def print_usage() -> None:
    print(
        Fore.YELLOW
        + """
Usage:
  python llama.py ingest <file/dir> ...   batch ingest (dedup + source_key)
  python llama.py query [--full] <question>  one-shot QA
  python llama.py chat [--full]              REPL chat

Env (.env):
  DASHSCOPE_API_KEY     required
  RAG_INGEST_ROOT       stable root for relative keys (recommended in prod)
  RAG_CHROMA_PATH       Chroma dir (default ./qwen_rag_data)
  RAG_CACHE_PATH        cache JSON (default ./file_hash_cache.json)
  RAG_MAX_FILE_MB       max file size MB (0 = no limit)
  RAG_RESOLVE_SYMLINKS  set 1 to resolve symlinks
"""
    )


def main() -> None:
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "ingest":
        asyncio.run(batch_process(sys.argv[2:]))
    elif cmd == "query":
        args = sys.argv[2:]
        if "--full" in args:
            os.environ["RAG_QUERY_FULL_OUTPUT"] = "1"
            os.environ["RAG_QUERY_SHOW_SOURCES"] = "1"
            args = [a for a in args if a != "--full"]
        asyncio.run(query_answer(" ".join(args)))
    elif cmd == "chat":
        args = sys.argv[2:]
        if "--full" in args:
            os.environ["RAG_QUERY_FULL_OUTPUT"] = "1"
            os.environ["RAG_QUERY_SHOW_SOURCES"] = "1"
        asyncio.run(chat_loop())
    else:
        print(Fore.RED + "unknown command")
        sys.exit(1)


if __name__ == "__main__":
    main()
