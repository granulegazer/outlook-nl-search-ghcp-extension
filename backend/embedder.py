"""
embedder.py
-----------
Embeds email candidates and a user query, then ranks the candidates by
cosine similarity using an in-memory FAISS index.

Design
~~~~~~
* Uses ``sentence-transformers`` for producing dense text embeddings.
* Builds a FAISS ``IndexFlatIP`` (inner-product) index over normalised
  vectors, which is equivalent to cosine similarity.
* The index is ephemeral — it is created per-request and never persisted.
* Falls back to numpy-only cosine similarity when FAISS is unavailable
  (e.g. ARM CI runners where ``faiss-cpu`` wheels are not published).

Privacy
~~~~~~~
Only the ``subject`` + ``snippet`` (≤ 500 chars) are embedded.
No full email bodies are processed or stored.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from backend.email_retriever import EmailCandidate

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default sentence-transformer model (small, fast, no GPU required).
DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"

#: How many top results to return by default.
DEFAULT_TOP_K = 10


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_model(model_name: str) -> "SentenceTransformer":  # type: ignore[name-defined]
    """Load a sentence-transformer model, raising a helpful error if missing."""
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "sentence-transformers is not installed.  "
            "Run: pip install sentence-transformers"
        ) from exc

    return SentenceTransformer(model_name)


def _normalise(vectors: np.ndarray) -> np.ndarray:
    """L2-normalise a 2-D float32 array row-wise (in-place safe copy)."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return (vectors / norms).astype(np.float32)


def _build_faiss_index(embeddings: np.ndarray) -> "faiss.IndexFlatIP":  # type: ignore[name-defined]
    """Build an in-memory FAISS inner-product index from *normalised* embeddings."""
    try:
        import faiss  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "faiss-cpu is not installed.  Run: pip install faiss-cpu"
        ) from exc

    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    return index


def _cosine_numpy(query_vec: np.ndarray, doc_vecs: np.ndarray) -> np.ndarray:
    """Pure-numpy cosine similarity between one query vector and N doc vectors.

    Parameters
    ----------
    query_vec:
        Shape ``(dim,)`` query embedding (assumed L2-normalised).
    doc_vecs:
        Shape ``(N, dim)`` document embeddings (assumed L2-normalised).

    Returns
    -------
    np.ndarray
        Shape ``(N,)`` similarity scores in [-1, 1].
    """
    return doc_vecs @ query_vec


def _email_text(email: EmailCandidate) -> str:
    """Build the text to embed for a single email candidate."""
    parts = []
    if email.subject:
        parts.append(f"Subject: {email.subject}")
    if email.sender:
        parts.append(f"From: {email.sender}")
    if email.snippet:
        parts.append(email.snippet)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class RankedEmail:
    """An email candidate with its cosine-similarity ranking score."""

    email: EmailCandidate
    score: float


def rank_emails(
    query: str,
    candidates: list[EmailCandidate],
    top_k: int = DEFAULT_TOP_K,
    model_name: str = DEFAULT_MODEL_NAME,
    use_faiss: bool = True,
) -> list[RankedEmail]:
    """Embed *query* and *candidates*, then return the top-*k* ranked results.

    Parameters
    ----------
    query:
        The original natural-language search query.
    candidates:
        Email candidates pre-filtered by metadata criteria.
    top_k:
        Maximum number of results to return.
    model_name:
        Sentence-transformer model identifier.
    use_faiss:
        When *True* (default), use FAISS for ANN search.
        Falls back to numpy cosine similarity automatically if FAISS is
        unavailable.

    Returns
    -------
    list[RankedEmail]
        Top-*k* ranked emails, highest score first.
    """
    if not candidates:
        return []

    model = _load_model(model_name)

    # Build corpus texts
    texts = [_email_text(c) for c in candidates]

    # Embed everything
    doc_embeddings: np.ndarray = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    query_embedding: np.ndarray = model.encode([query], convert_to_numpy=True, show_progress_bar=False)[0]

    # Normalise for cosine similarity
    doc_vecs = _normalise(doc_embeddings)
    query_vec = _normalise(query_embedding.reshape(1, -1))[0]

    k = min(top_k, len(candidates))

    if use_faiss:
        try:
            index = _build_faiss_index(doc_vecs)
            distances, indices = index.search(query_vec.reshape(1, -1), k)
            ranked = [
                RankedEmail(email=candidates[idx], score=float(dist))
                for idx, dist in zip(indices[0], distances[0])
                if idx >= 0
            ]
            # Attach scores to the EmailCandidate objects for serialisation
            for r in ranked:
                r.email.score = r.score
            return ranked
        except ImportError:
            pass  # Fall through to numpy fallback

    # Numpy fallback
    scores = _cosine_numpy(query_vec, doc_vecs)
    top_indices = np.argsort(scores)[::-1][:k]
    ranked = [
        RankedEmail(email=candidates[i], score=float(scores[i]))
        for i in top_indices
    ]
    for r in ranked:
        r.email.score = r.score
    return ranked
