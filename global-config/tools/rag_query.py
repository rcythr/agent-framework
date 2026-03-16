"""
Core tool: rag_query — query a LangIndex-based RAG API for context retrieval.

Sends a natural-language query to the configured RAG endpoint and returns
the most relevant document chunks.  The RAG_API_URL environment variable
must point to a running LangIndex (or compatible) server.
"""
import os

import httpx


def get_tool() -> dict:
    return {
        "name": "rag_query",
        "description": (
            "Query the project's knowledge base (RAG) for relevant context. "
            "Use this to find documentation, past decisions, architecture notes, "
            "or any other indexed content before making changes. "
            "Returns a list of relevant text chunks with their sources."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language question or search phrase.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of chunks to return (default: 5).",
                },
            },
            "required": ["query"],
        },
        "execute": _execute,
    }


def _execute(query: str, top_k: int = 5) -> str:
    api_url = os.getenv("RAG_API_URL", "")
    if not api_url:
        return (
            "RAG_API_URL is not configured. "
            "Set the RAG_API_URL environment variable to enable knowledge-base queries."
        )

    try:
        r = httpx.post(
            f"{api_url.rstrip('/')}/query",
            json={"query": query, "top_k": top_k},
            timeout=30.0,
        )
        r.raise_for_status()
        data = r.json()

        chunks = data.get("results") or data.get("chunks") or []
        if not chunks:
            return "No results found."

        parts = []
        for i, chunk in enumerate(chunks, 1):
            source = chunk.get("source") or chunk.get("metadata", {}).get("source", "unknown")
            text = chunk.get("text") or chunk.get("content", "")
            score = chunk.get("score")
            score_str = f" (score: {score:.3f})" if score is not None else ""
            parts.append(f"[{i}] {source}{score_str}\n{text}")

        return "\n\n---\n\n".join(parts)
    except httpx.HTTPStatusError as exc:
        return f"RAG query failed (HTTP {exc.response.status_code}): {exc.response.text}"
    except Exception as exc:
        return f"RAG query error: {exc}"
