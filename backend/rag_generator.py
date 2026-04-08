"""
rag_generator.py
----------------
Builds a structured RAG (Retrieval-Augmented Generation) context payload
from ranked email candidates.

This module is responsible for the **context-building** step of the pipeline:
it formats the top-K email excerpts into a structured prompt context string
that the GHCP LLM (running in the VS Code extension) uses to generate the
final grounded answer.

The actual LLM call is made in the TypeScript extension layer — this module
only produces the context payload that travels back in the JSON response.
"""

from __future__ import annotations

from backend.embedder import RankedEmail

#: Maximum characters of body snippet to include per email in the RAG context.
RAG_SNIPPET_MAX_CHARS = 400


def build_rag_context(ranked_emails: list[RankedEmail]) -> str:
    """Build a human-readable RAG context block from ranked email results.

    The context is formatted so that the GHCP LLM can cite individual emails
    by their ``[Email N]`` reference markers.

    Parameters
    ----------
    ranked_emails:
        Top-K ranked email results from the embedding step.

    Returns
    -------
    str
        A formatted string containing one block per email, suitable for
        inclusion in the LLM system/user prompt.
    """
    blocks: list[str] = []
    for i, ranked in enumerate(ranked_emails, start=1):
        email = ranked.email
        snippet = email.snippet[:RAG_SNIPPET_MAX_CHARS].rstrip()
        if len(email.snippet) > RAG_SNIPPET_MAX_CHARS:
            snippet += "…"

        block = (
            f"[Email {i}]\n"
            f"Subject: {email.subject}\n"
            f"From: {email.sender}\n"
            f"Date: {email.date}\n"
            f"Folder: {email.folder}\n"
            f"Similarity score: {ranked.score:.3f}\n"
            f"Excerpt: {snippet}"
        )
        blocks.append(block)

    return "\n\n".join(blocks)
