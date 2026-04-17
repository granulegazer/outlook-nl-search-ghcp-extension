"""
main.py
-------
CLI entry point for the Outlook NL-search Python backend.

The VS Code extension spawns this script as a subprocess, writing a
JSON-encoded request payload to stdin and reading a JSON-encoded response
from stdout.

Request schema (stdin)
~~~~~~~~~~~~~~~~~~~~~~
{
  "query":    string,                  // original NL query
  "criteria": { ... },                 // LLM-extracted criteria (see criteria_extractor.py)
  "top_k":    int                      // max results to return (default 10)
}

Response schema (stdout)
~~~~~~~~~~~~~~~~~~~~~~~~
{
  "emails": [
    {
      "subject": string,
      "sender":  string,
      "date":    string,               // ISO 8601 date
      "snippet": string,
      "folder":  string,
      "score":   float                 // cosine similarity score
    },
    ...
  ],
  "error": string | null              // non-null when a recoverable error occurred
}

Exit codes
~~~~~~~~~~
* 0 — success (response JSON written to stdout)
* 1 — unrecoverable error (message written to stderr)
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _read_request() -> dict[str, Any]:
    """Read and parse the JSON request from stdin."""
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("Empty request: no data received on stdin.")
    return json.loads(raw)


def _error_response(message: str) -> dict[str, Any]:
    """Build an error response payload."""
    return {"emails": [], "error": message}


def run(request: dict[str, Any]) -> dict[str, Any]:
    """Execute the full RAG pipeline and return the response dict.

    Parameters
    ----------
    request:
        Parsed request payload from the VS Code extension.

    Returns
    -------
    dict
        Response payload to be JSON-serialised and written to stdout.
    """
    # --- imports are deferred so that import errors surface as JSON error responses ---
    from backend.criteria_extractor import parse_criteria, resolve_relative_dates
    from backend.email_retriever import OutlookUnavailableError, retrieve_emails
    from backend.embedder import rank_emails

    query: str = request.get("query", "").strip()
    raw_criteria: dict[str, Any] = request.get("criteria", {})
    top_k: int = int(request.get("top_k", 10))

    if not query:
        return _error_response("No query provided.")

    # Attach the original query as a hint for relative-date resolution
    raw_criteria["_query_hint"] = query

    # 1. Parse and validate criteria
    criteria = parse_criteria(raw_criteria)
    criteria = resolve_relative_dates(criteria, raw_criteria)

    # 2. Retrieve candidate emails via MAPI/COM
    try:
        candidates = retrieve_emails(criteria)
    except OutlookUnavailableError as exc:
        return _error_response(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _error_response(f"Unexpected error during email retrieval: {exc}")

    if not candidates:
        return {"emails": [], "error": None}

    # 3. Embed and rank
    try:
        ranked = rank_emails(query, candidates, top_k=top_k, use_faiss=True)
    except ImportError as exc:
        # sentence-transformers or faiss-cpu not installed
        return _error_response(
            f"Embedding dependencies not available: {exc}. "
            "Run: pip install -r backend/requirements.txt"
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(f"Unexpected error during ranking: {exc}")

    # 4. Serialise results
    emails = [r.email.as_dict() for r in ranked]
    return {"emails": emails, "error": None}


def main() -> None:
    """Entry point: read stdin → run pipeline → write stdout."""
    try:
        request = _read_request()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps(_error_response(f"Failed to parse request: {exc}")), flush=True)
        sys.exit(1)

    response = run(request)
    print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
