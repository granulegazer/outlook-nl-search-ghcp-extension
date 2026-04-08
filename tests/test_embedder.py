"""
test_embedder.py
----------------
Unit tests for :mod:`backend.embedder`.

These tests mock ``sentence_transformers`` and ``faiss`` to avoid requiring
GPU or heavyweight ML dependencies in CI.  The mocks produce deterministic
embeddings so the ranking logic can be verified without real inference.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from backend.embedder import (
    DEFAULT_TOP_K,
    _cosine_numpy,
    _email_text,
    _normalise,
    rank_emails,
)
from tests.conftest import make_email


# ---------------------------------------------------------------------------
# Fixtures — mock sentence-transformers
# ---------------------------------------------------------------------------


def _make_mock_model(embeddings: np.ndarray) -> MagicMock:
    """Build a mock SentenceTransformer whose ``encode`` returns *embeddings*."""
    model = MagicMock()
    call_count = [0]

    def encode_side_effect(texts, **kwargs):  # type: ignore[override]
        count = call_count[0]
        call_count[0] += 1
        # First call is the document batch; subsequent calls are query
        if isinstance(texts, list) and len(texts) == 1:
            # Query encoding — return a single vector
            return embeddings[0:1]
        return embeddings

    model.encode.side_effect = encode_side_effect
    return model


# ---------------------------------------------------------------------------
# _normalise
# ---------------------------------------------------------------------------


class TestNormalise:
    def test_unit_vector_unchanged(self) -> None:
        v = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        result = _normalise(v)
        np.testing.assert_allclose(result, v, atol=1e-6)

    def test_non_unit_vector_normalised(self) -> None:
        v = np.array([[3.0, 4.0]], dtype=np.float32)
        result = _normalise(v)
        norm = np.linalg.norm(result[0])
        assert abs(norm - 1.0) < 1e-6

    def test_zero_vector_does_not_divide_by_zero(self) -> None:
        v = np.array([[0.0, 0.0, 0.0]], dtype=np.float32)
        result = _normalise(v)
        # All-zero input → all-zero output (norm clamped to 1.0)
        np.testing.assert_allclose(result, v, atol=1e-6)

    def test_batch_normalisation(self) -> None:
        v = np.array([[1.0, 0.0], [0.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        result = _normalise(v)
        for row in result:
            assert abs(np.linalg.norm(row) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# _cosine_numpy
# ---------------------------------------------------------------------------


class TestCosineNumpy:
    def test_identical_vectors(self) -> None:
        q = np.array([1.0, 0.0], dtype=np.float32)
        d = np.array([[1.0, 0.0]], dtype=np.float32)
        scores = _cosine_numpy(q, d)
        assert abs(scores[0] - 1.0) < 1e-6

    def test_orthogonal_vectors(self) -> None:
        q = np.array([1.0, 0.0], dtype=np.float32)
        d = np.array([[0.0, 1.0]], dtype=np.float32)
        scores = _cosine_numpy(q, d)
        assert abs(scores[0]) < 1e-6

    def test_opposite_vectors(self) -> None:
        q = np.array([1.0, 0.0], dtype=np.float32)
        d = np.array([[-1.0, 0.0]], dtype=np.float32)
        scores = _cosine_numpy(q, d)
        assert abs(scores[0] - (-1.0)) < 1e-6

    def test_ranking_order(self) -> None:
        q = np.array([1.0, 0.0], dtype=np.float32)
        docs = np.array(
            [
                [0.0, 1.0],   # orthogonal  → score ≈ 0
                [1.0, 0.0],   # identical   → score = 1
                [0.707, 0.707],  # 45 deg    → score ≈ 0.707
            ],
            dtype=np.float32,
        )
        scores = _cosine_numpy(q, docs)
        ranked = np.argsort(scores)[::-1]
        assert ranked[0] == 1  # identical → highest


# ---------------------------------------------------------------------------
# _email_text
# ---------------------------------------------------------------------------


class TestEmailText:
    def test_includes_subject_sender_snippet(self) -> None:
        email = make_email(subject="Hello", sender="alice@example.com", snippet="World")
        text = _email_text(email)
        assert "Hello" in text
        assert "alice@example.com" in text
        assert "World" in text

    def test_missing_fields_skipped(self) -> None:
        email = make_email(subject="Only Subject", sender="", snippet="")
        text = _email_text(email)
        assert "Only Subject" in text
        assert text.strip() == "Subject: Only Subject"


# ---------------------------------------------------------------------------
# rank_emails — numpy fallback (no FAISS)
# ---------------------------------------------------------------------------


class TestRankEmailsNumpyFallback:
    def _build_embeddings(self, n: int, dim: int = 8) -> np.ndarray:
        """Build deterministic, diverse unit embeddings for *n* documents."""
        rng = np.random.default_rng(42)
        vecs = rng.random((n, dim)).astype(np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / norms

    def test_returns_empty_for_no_candidates(self) -> None:
        with patch("backend.embedder._load_model"):
            result = rank_emails("query", [], top_k=5, use_faiss=False)
        assert result == []

    def test_returns_at_most_top_k(self, sample_emails) -> None:
        dim = 8
        n = len(sample_emails)
        all_vecs = self._build_embeddings(n + 1, dim)
        doc_vecs = all_vecs[:n]
        query_vec = all_vecs[n : n + 1]

        mock_model = MagicMock()
        call_log = []

        def encode(texts, **kwargs):
            call_log.append(len(texts))
            if len(texts) == 1:
                return query_vec
            return doc_vecs

        mock_model.encode.side_effect = encode

        with patch("backend.embedder._load_model", return_value=mock_model):
            results = rank_emails("find budget emails", sample_emails, top_k=3, use_faiss=False)

        assert len(results) <= 3

    def test_scores_are_sorted_descending(self, sample_emails) -> None:
        dim = 8
        n = len(sample_emails)
        all_vecs = self._build_embeddings(n + 1, dim)
        doc_vecs = all_vecs[:n]
        query_vec = all_vecs[n : n + 1]

        mock_model = MagicMock()

        def encode(texts, **kwargs):
            if len(texts) == 1:
                return query_vec
            return doc_vecs

        mock_model.encode.side_effect = encode

        with patch("backend.embedder._load_model", return_value=mock_model):
            results = rank_emails("find budget emails", sample_emails, top_k=5, use_faiss=False)

        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_score_attached_to_email_candidate(self, sample_emails) -> None:
        dim = 8
        n = len(sample_emails)
        all_vecs = self._build_embeddings(n + 1, dim)
        doc_vecs = all_vecs[:n]
        query_vec = all_vecs[n : n + 1]

        mock_model = MagicMock()

        def encode(texts, **kwargs):
            if len(texts) == 1:
                return query_vec
            return doc_vecs

        mock_model.encode.side_effect = encode

        with patch("backend.embedder._load_model", return_value=mock_model):
            results = rank_emails("find budget emails", sample_emails, top_k=5, use_faiss=False)

        for r in results:
            assert r.email.score == pytest.approx(r.score)

    def test_top_k_larger_than_candidates(self, sample_emails) -> None:
        dim = 8
        n = len(sample_emails)
        all_vecs = self._build_embeddings(n + 1, dim)
        doc_vecs = all_vecs[:n]
        query_vec = all_vecs[n : n + 1]

        mock_model = MagicMock()

        def encode(texts, **kwargs):
            if len(texts) == 1:
                return query_vec
            return doc_vecs

        mock_model.encode.side_effect = encode

        with patch("backend.embedder._load_model", return_value=mock_model):
            results = rank_emails(
                "find budget emails", sample_emails, top_k=100, use_faiss=False
            )

        # Should return all candidates (capped at len(candidates))
        assert len(results) == n

    def test_import_error_on_missing_sentence_transformers(self, sample_emails) -> None:
        with patch(
            "backend.embedder._load_model",
            side_effect=ImportError("sentence-transformers not installed"),
        ):
            with pytest.raises(ImportError, match="sentence-transformers"):
                rank_emails("query", sample_emails, top_k=5, use_faiss=False)
