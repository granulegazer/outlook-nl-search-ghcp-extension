"""
criteria_extractor.py
---------------------
Parses and validates structured search criteria produced by the GHCP LLM.

The VS Code extension sends a JSON payload with a ``criteria`` key whose value
is the raw JSON object extracted by the LLM.  This module normalises and
validates that object into a well-typed :class:`SearchCriteria` dataclass so
that the rest of the backend can consume it safely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


@dataclass
class SearchCriteria:
    """Validated search criteria derived from the LLM extraction step."""

    sender: str | None = None
    recipient: str | None = None
    subject_keywords: list[str] = field(default_factory=list)
    body_keywords: list[str] = field(default_factory=list)
    date_start: date | None = None
    date_end: date | None = None
    folder: str | None = None
    read_state: str | None = None          # "read" | "unread" | None
    has_attachment: bool | None = None
    importance: str | None = None          # "high" | "normal" | "low" | None
    categories: list[str] = field(default_factory=list)


def _parse_date(value: Any) -> date | None:
    """Parse a date string in ISO 8601 format (YYYY-MM-DD).  Returns *None* on failure."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _coerce_list(value: Any) -> list[str]:
    """Return *value* as a list of stripped non-empty strings."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _coerce_bool(value: Any) -> bool | None:
    """Coerce *value* to bool or *None*."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        if value.lower() in {"true", "yes", "1"}:
            return True
        if value.lower() in {"false", "no", "0"}:
            return False
    return None


def _coerce_str(value: Any) -> str | None:
    """Return *value* stripped as str, or *None* if blank/missing."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _validate_read_state(value: Any) -> str | None:
    if isinstance(value, str) and value.strip().lower() in {"read", "unread"}:
        return value.strip().lower()
    return None


def _validate_importance(value: Any) -> str | None:
    if isinstance(value, str) and value.strip().lower() in {"high", "normal", "low"}:
        return value.strip().lower()
    return None


def parse_criteria(raw: dict[str, Any]) -> SearchCriteria:
    """Parse and validate a raw criteria dict from the LLM into a :class:`SearchCriteria`.

    Unknown fields are silently ignored.  Missing fields fall back to safe
    defaults (``None`` / empty list).

    Parameters
    ----------
    raw:
        The JSON object produced by the LLM criteria-extraction step.

    Returns
    -------
    SearchCriteria
        A validated, normalised criteria object.
    """
    return SearchCriteria(
        sender=_coerce_str(raw.get("sender")),
        recipient=_coerce_str(raw.get("recipient")),
        subject_keywords=_coerce_list(raw.get("subject_keywords", [])),
        body_keywords=_coerce_list(raw.get("body_keywords", [])),
        date_start=_parse_date(raw.get("date_start")),
        date_end=_parse_date(raw.get("date_end")),
        folder=_coerce_str(raw.get("folder")),
        read_state=_validate_read_state(raw.get("read_state")),
        has_attachment=_coerce_bool(raw.get("has_attachment")),
        importance=_validate_importance(raw.get("importance")),
        categories=_coerce_list(raw.get("categories", [])),
    )


def last_month_range() -> tuple[date, date]:
    """Return the ``(start, end)`` date range for the previous calendar month."""
    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_of_last_month = first_of_this_month - timedelta(days=1)
    first_of_last_month = last_of_last_month.replace(day=1)
    return first_of_last_month, last_of_last_month


def last_n_days_range(n: int) -> tuple[date, date]:
    """Return the ``(start, end)`` date range for the last *n* days (inclusive)."""
    today = date.today()
    start = today - timedelta(days=n - 1)
    return start, today


def resolve_relative_dates(criteria: SearchCriteria, raw: dict[str, Any]) -> SearchCriteria:
    """Post-process relative-date references that the LLM may not resolve automatically.

    If the LLM left ``date_start``/``date_end`` as *None* but the original
    query contains phrases like "last month" or "last N days", fill them in.
    This is a best-effort heuristic — the LLM usually resolves these itself.

    Parameters
    ----------
    criteria:
        Already-parsed criteria object.
    raw:
        The original raw criteria dict (used to detect un-resolved references).

    Returns
    -------
    SearchCriteria
        The criteria object, possibly with updated date fields.
    """
    query_hint = str(raw.get("_query_hint", "")).lower()

    if criteria.date_start is None and criteria.date_end is None:
        if re.search(r"\blast\s+month\b", query_hint):
            criteria.date_start, criteria.date_end = last_month_range()
        else:
            match = re.search(r"\blast\s+(\d+)\s+days?\b", query_hint)
            if match:
                n = int(match.group(1))
                criteria.date_start, criteria.date_end = last_n_days_range(n)

    return criteria
