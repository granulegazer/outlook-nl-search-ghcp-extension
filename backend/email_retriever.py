"""
email_retriever.py
------------------
Retrieves candidate emails from a local Microsoft Outlook profile using
MAPI/COM via the ``pywin32`` library.

This module is **Windows-only**.  On non-Windows platforms (e.g. Linux CI),
it raises :class:`OutlookUnavailableError` so that callers can handle the
situation gracefully (typically by returning an empty result set with an
explanatory error message).

Design notes
~~~~~~~~~~~~
* All email access is read-only — the module never modifies Outlook data.
* Only ``subject`` + a ``body`` snippet (≤ 500 characters) are extracted to
  minimise the data footprint; full email bodies are never stored.
* The FAISS index is built in-memory per session; nothing is persisted to disk.
"""

from __future__ import annotations

import platform
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from backend.criteria_extractor import SearchCriteria

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum number of characters extracted from an email body.
BODY_SNIPPET_MAX_CHARS = 500

#: Outlook item type constant for mail items.
_OL_MAIL_ITEM = 43

#: Maximum emails to pull from Outlook before applying semantic ranking
#: (guards against very large mailboxes slowing down the pipeline).
MAX_CANDIDATE_EMAILS = 500


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class EmailCandidate:
    """A single email candidate with metadata and a body snippet."""

    subject: str
    sender: str
    date: date
    snippet: str
    folder: str
    is_read: bool
    has_attachment: bool
    importance: str        # "high" | "normal" | "low"
    categories: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)

    # Computed after ranking
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary for the response payload."""
        return {
            "subject": self.subject,
            "sender": self.sender,
            "date": self.date.isoformat() if isinstance(self.date, date) else str(self.date),
            "snippet": self.snippet,
            "folder": self.folder,
            "score": self.score,
        }


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OutlookUnavailableError(RuntimeError):
    """Raised when Outlook / MAPI is not available on the current platform."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _importance_label(ol_importance: int) -> str:
    """Convert an Outlook importance integer to a label string."""
    return {0: "low", 1: "normal", 2: "high"}.get(ol_importance, "normal")


def _parse_outlook_date(value: Any) -> date:
    """Convert a COM date-time value to a :class:`datetime.date`."""
    if isinstance(value, datetime):
        return value.date()
    # pywin32 may return a pywintypes.datetime which behaves like datetime
    try:
        return value.date()
    except AttributeError:
        pass
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return date.today()


def _get_recipients(mail_item: Any) -> list[str]:
    """Extract all recipient display names / email addresses from a mail item."""
    recipients: list[str] = []
    try:
        for i in range(1, mail_item.Recipients.Count + 1):
            recip = mail_item.Recipients.Item(i)
            addr = getattr(recip, "Address", "") or getattr(recip, "Name", "")
            if addr:
                recipients.append(addr)
    except Exception:  # noqa: BLE001
        pass
    return recipients


def _matches_criteria(item: EmailCandidate, criteria: SearchCriteria) -> bool:
    """Return *True* if *item* satisfies all non-None criteria filters."""
    # Sender
    if criteria.sender:
        sender_lower = item.sender.lower()
        if criteria.sender.lower() not in sender_lower:
            return False

    # Recipient
    if criteria.recipient:
        crit_recip_lower = criteria.recipient.lower()
        if not any(crit_recip_lower in r.lower() for r in item.recipients):
            return False

    # Date range
    if criteria.date_start and item.date < criteria.date_start:
        return False
    if criteria.date_end and item.date > criteria.date_end:
        return False

    # Read state
    if criteria.read_state == "read" and not item.is_read:
        return False
    if criteria.read_state == "unread" and item.is_read:
        return False

    # Has attachment
    if criteria.has_attachment is True and not item.has_attachment:
        return False
    if criteria.has_attachment is False and item.has_attachment:
        return False

    # Importance
    if criteria.importance and item.importance != criteria.importance:
        return False

    # Subject keywords
    subject_lower = item.subject.lower()
    for kw in criteria.subject_keywords:
        if kw.lower() not in subject_lower:
            return False

    # Body keywords
    snippet_lower = item.snippet.lower()
    for kw in criteria.body_keywords:
        if kw.lower() not in snippet_lower:
            return False

    # Categories
    item_cats_lower = [c.lower() for c in item.categories]
    for cat in criteria.categories:
        if cat.lower() not in item_cats_lower:
            return False

    return True


def _iter_folder(ol_folder: Any, criteria: SearchCriteria) -> list[EmailCandidate]:
    """Iterate mail items in an Outlook MAPIFolder and return matching candidates."""
    candidates: list[EmailCandidate] = []
    try:
        items = ol_folder.Items
        items.Sort("[ReceivedTime]", True)  # newest first
    except Exception:  # noqa: BLE001
        return candidates

    count = 0
    for item in items:
        if count >= MAX_CANDIDATE_EMAILS:
            break
        try:
            if item.Class != _OL_MAIL_ITEM:
                continue

            body_raw = getattr(item, "Body", "") or ""
            snippet = re.sub(r"\s+", " ", body_raw[:BODY_SNIPPET_MAX_CHARS]).strip()

            candidate = EmailCandidate(
                subject=getattr(item, "Subject", "") or "",
                sender=getattr(item, "SenderEmailAddress", "") or getattr(item, "SenderName", ""),
                date=_parse_outlook_date(getattr(item, "ReceivedTime", datetime.now())),
                snippet=snippet,
                folder=ol_folder.Name,
                is_read=not getattr(item, "UnRead", True),
                has_attachment=getattr(item, "Attachments", None) is not None
                and item.Attachments.Count > 0,
                importance=_importance_label(getattr(item, "Importance", 1)),
                categories=[
                    c.strip()
                    for c in (getattr(item, "Categories", "") or "").split(";")
                    if c.strip()
                ],
                recipients=_get_recipients(item),
            )

            if _matches_criteria(candidate, criteria):
                candidates.append(candidate)
                count += 1
        except Exception:  # noqa: BLE001  # skip corrupted/protected items
            continue

    return candidates


def _resolve_folder(namespace: Any, folder_name: str | None) -> list[Any]:
    """Return a list of MAPIFolder objects matching *folder_name*.

    If *folder_name* is *None*, returns all top-level folders in the default
    store (typically Inbox, Sent Items, Drafts, etc.).
    """
    folders: list[Any] = []
    try:
        default_store = namespace.Stores.Item(1)
        root = default_store.GetRootFolder()
    except Exception:
        # Fallback: use the default inbox's parent
        try:
            inbox = namespace.GetDefaultFolder(6)  # olFolderInbox = 6
            root = inbox.Parent
        except Exception:
            return folders

    def _walk(folder: Any, target: str | None) -> None:
        name = getattr(folder, "Name", "")
        if target is None or name.lower() == target.lower():
            folders.append(folder)
        try:
            sub_folders = folder.Folders
            for i in range(1, sub_folders.Count + 1):
                _walk(sub_folders.Item(i), target)
        except Exception:
            pass

    _walk(root, folder_name)
    return folders


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def retrieve_emails(criteria: SearchCriteria) -> list[EmailCandidate]:
    """Retrieve candidate emails from the local Outlook profile.

    Parameters
    ----------
    criteria:
        Validated search criteria.  Metadata filters are applied at retrieval
        time; keyword and semantic filters are applied downstream.

    Returns
    -------
    list[EmailCandidate]
        A list of matching email candidates, newest first, capped at
        :data:`MAX_CANDIDATE_EMAILS`.

    Raises
    ------
    OutlookUnavailableError
        On non-Windows platforms or when Outlook / pywin32 is not installed.
    """
    if platform.system() != "Windows":
        raise OutlookUnavailableError(
            "Outlook MAPI/COM access requires Windows with Microsoft Outlook installed."
        )

    try:
        import win32com.client  # type: ignore[import-untyped]
    except ImportError as exc:
        raise OutlookUnavailableError(
            "pywin32 is not installed.  Run: pip install pywin32"
        ) from exc

    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
    except Exception as exc:
        raise OutlookUnavailableError(
            f"Failed to connect to Outlook via MAPI: {exc}"
        ) from exc

    folders = _resolve_folder(namespace, criteria.folder)
    if not folders:
        return []

    candidates: list[EmailCandidate] = []
    for folder in folders:
        candidates.extend(_iter_folder(folder, criteria))
        if len(candidates) >= MAX_CANDIDATE_EMAILS:
            break

    return candidates[:MAX_CANDIDATE_EMAILS]
