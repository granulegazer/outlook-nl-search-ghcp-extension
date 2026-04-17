# outlook-nl-search-ghcp-extension

A **GitHub Copilot (GHCP) Chat extension** that lets you search your local Outlook emails using **natural language**, powered by a Semantic / RAG pipeline.

## Features

- Type a plain-English query such as *"find emails from Alice about Q1 budget last month"* inside VS Code Copilot Chat using `@outlook-search`.
- The extension:
  1. **Extracts search criteria** (dates, sender, subject keywords, folder, etc.) using the GHCP LLM.
  2. **Retrieves candidate emails** from your local Outlook profile via MAPI/COM (`pywin32`) — all data stays local.
  3. **Ranks them semantically** using sentence-transformer embeddings and a FAISS in-memory index.
  4. **Generates a grounded answer** via the GHCP LLM, citing matching email subjects, senders, and dates.

## Requirements

| Requirement | Detail |
|---|---|
| Operating System | Windows (Outlook MAPI/COM via `pywin32`) |
| VS Code | ≥ 1.90 with GitHub Copilot Chat extension |
| Python | ≥ 3.11 |
| Outlook | Desktop Outlook with a configured local profile |

## Quick Start

### 1 — Install Python dependencies

```bash
pip install -r backend/requirements.txt
```

### 2 — Install and activate the VS Code extension

```bash
npm install
npm run compile
# Then press F5 in VS Code to launch the Extension Development Host
```

### 3 — Use the chat participant

Open Copilot Chat (`Ctrl+Alt+I`) and type:

```
@outlook-search find emails from Alice about Q1 budget last month
```

## Architecture

```
User NL query (@outlook-search in GHCP Chat)
        │
        ▼
[1] Criteria extraction  ── GHCP LLM (TypeScript/VS Code API)
        │  dates · sender · subject · body · folder · read-state · attachments
        ▼
[2] Candidate retrieval  ── pywin32 MAPI/COM → local Outlook profile
        │  metadata-filtered email list
        ▼
[3] Embedding & ranking  ── sentence-transformers + FAISS (in-memory, per-session)
        │  cosine similarity → top-K emails
        ▼
[4] Answer generation    ── GHCP LLM RAG (top-K chunks as context)
        │  grounded answer + citations (subject · sender · date)
        ▼
Ranked results + NL summary shown inside GHCP Chat
```

## Search criteria supported

| Category | Examples |
|---|---|
| Date | exact date, date range, last N days, before/after |
| Sender | name, email address, domain |
| Recipient | To, Cc, Bcc |
| Subject | contains words or phrase |
| Body | contains words, exact phrase |
| Attachments | has attachment, file type, filename |
| Read state | read / unread |
| Importance | high / normal / low |
| Folder | Inbox, Sent, Archive, custom folder |
| Categories | Outlook category/tag |

## Privacy & security

- Emails stay **local** — no data is sent to external servers beyond GHCP (approved LLM provider).
- Only **subject + body snippet (truncated to 500 chars)** is sent for embedding/generation — no full email blobs.
- No persistent storage of email content — **FAISS index is in-memory per session**.

## Project structure

```
├── src/
│   ├── extension.ts          # VS Code extension entry point & chat participant
│   └── pythonBackend.ts      # Subprocess communication with Python backend
├── backend/
│   ├── main.py               # CLI entry point (stdin JSON → stdout JSON)
│   ├── criteria_extractor.py # Parse structured criteria from LLM JSON
│   ├── email_retriever.py    # MAPI/COM email retrieval (pywin32)
│   ├── embedder.py           # Embeddings + FAISS ranking
│   ├── rag_generator.py      # RAG context builder
│   └── requirements.txt
└── tests/
    ├── conftest.py
    ├── test_criteria_extractor.py
    ├── test_email_retriever.py
    └── test_embedder.py
```

## Running tests

```bash
pip install -r backend/requirements.txt
pytest tests/ -v
```