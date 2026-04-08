import * as vscode from "vscode";
import { callPythonBackend, BackendRequest, BackendResponse } from "./pythonBackend";

const PARTICIPANT_ID = "outlook-nl-search.search";

/**
 * System prompt used to extract structured search criteria from a natural-language query.
 */
const CRITERIA_EXTRACTION_SYSTEM_PROMPT = `You are a search-criteria extractor for an Outlook email search assistant.
Given a natural-language email search query, extract structured JSON search criteria.

Return ONLY valid JSON with this exact schema (use null for absent fields):
{
  "sender": string | null,
  "recipient": string | null,
  "subject_keywords": string[],
  "body_keywords": string[],
  "date_start": string | null,   // ISO 8601 date, e.g. "2026-03-01"
  "date_end": string | null,     // ISO 8601 date, e.g. "2026-03-31"
  "folder": string | null,       // e.g. "Inbox", "Sent Items"
  "read_state": "read" | "unread" | null,
  "has_attachment": boolean | null,
  "importance": "high" | "normal" | "low" | null,
  "categories": string[]
}

Examples:
- "emails from Alice about Q1 budget last month" →
  {"sender":"Alice","recipient":null,"subject_keywords":["Q1","budget"],"body_keywords":[],"date_start":"<first day of last month>","date_end":"<last day of last month>","folder":null,"read_state":null,"has_attachment":null,"importance":null,"categories":[]}
- "unread emails with attachments in Inbox" →
  {"sender":null,"recipient":null,"subject_keywords":[],"body_keywords":[],"date_start":null,"date_end":null,"folder":"Inbox","read_state":"unread","has_attachment":true,"importance":null,"categories":[]}`;

/**
 * System prompt used to generate a grounded answer from retrieved email chunks.
 */
const RAG_SYSTEM_PROMPT = `You are an Outlook email search assistant integrated into VS Code via GitHub Copilot.
You have been given a user's natural-language search query and a list of relevant email excerpts retrieved from their local Outlook profile.

Your task:
1. Summarise the search results in a concise, helpful answer.
2. List the most relevant emails with: subject, sender, date, and a one-sentence excerpt.
3. If no emails match, tell the user clearly.
4. Always cite email references using the format: **[Subject]** — From: Sender (Date).
5. Never hallucinate email content; only use the provided excerpts.`;

/**
 * Parse today's date components to resolve relative date expressions in the LLM output.
 */
function todayContext(): string {
  const now = new Date();
  return `Today is ${now.toISOString().slice(0, 10)} (${now.toLocaleDateString("en-US", { weekday: "long" })}).`;
}

/**
 * Extract structured search criteria from a natural-language query using the GHCP LLM.
 */
async function extractCriteria(
  query: string,
  model: vscode.LanguageModelChat,
  token: vscode.CancellationToken
): Promise<Record<string, unknown>> {
  const messages = [
    vscode.LanguageModelChatMessage.User(
      `${CRITERIA_EXTRACTION_SYSTEM_PROMPT}\n\n${todayContext()}\n\nQuery: ${query}`
    ),
  ];

  let raw = "";
  const response = await model.sendRequest(messages, {}, token);
  for await (const chunk of response.text) {
    raw += chunk;
  }

  // Extract JSON block (the model may wrap it in markdown fences)
  const jsonMatch = raw.match(/```(?:json)?\s*([\s\S]*?)```/) || raw.match(/(\{[\s\S]*\})/);
  const jsonStr = jsonMatch ? jsonMatch[1].trim() : raw.trim();

  try {
    return JSON.parse(jsonStr) as Record<string, unknown>;
  } catch {
    // Return an empty criteria object if parsing fails
    return {
      sender: null,
      recipient: null,
      subject_keywords: [],
      body_keywords: [],
      date_start: null,
      date_end: null,
      folder: null,
      read_state: null,
      has_attachment: null,
      importance: null,
      categories: [],
    };
  }
}

/**
 * Generate a grounded RAG answer from email excerpts using the GHCP LLM.
 */
async function generateAnswer(
  query: string,
  emails: BackendResponse["emails"],
  model: vscode.LanguageModelChat,
  token: vscode.CancellationToken,
  stream: vscode.ChatResponseStream
): Promise<void> {
  if (emails.length === 0) {
    stream.markdown(
      "No emails matched your query in your local Outlook profile. " +
        "Try broadening the search criteria (e.g., remove date filters or use fewer keywords)."
    );
    return;
  }

  // Build context from top-K email excerpts
  const emailContext = emails
    .map(
      (e, i) =>
        `[Email ${i + 1}]\nSubject: ${e.subject}\nFrom: ${e.sender}\nDate: ${e.date}\nFolder: ${e.folder}\nSnippet: ${e.snippet}\nScore: ${e.score.toFixed(3)}`
    )
    .join("\n\n");

  const messages = [
    vscode.LanguageModelChatMessage.User(
      `${RAG_SYSTEM_PROMPT}\n\nUser query: "${query}"\n\nRetrieved emails:\n${emailContext}`
    ),
  ];

  const response = await model.sendRequest(messages, {}, token);
  for await (const chunk of response.text) {
    stream.markdown(chunk);
  }
}

/**
 * Main chat participant request handler.
 */
async function handleRequest(
  request: vscode.ChatRequest,
  _context: vscode.ChatContext,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken
): Promise<void> {
  const query = request.prompt.trim();
  if (!query) {
    stream.markdown(
      "Please provide a search query. For example:\n\n" +
        "`@outlook-search find emails from Alice about Q1 budget last month`"
    );
    return;
  }

  // Select an available GHCP LLM
  const [model] = await vscode.lm.selectChatModels({
    vendor: "copilot",
    family: "gpt-4o",
  });

  if (!model) {
    stream.markdown(
      "⚠️ No GitHub Copilot language model is available. " +
        "Please ensure the GitHub Copilot Chat extension is installed and you are signed in."
    );
    return;
  }

  stream.progress("Extracting search criteria…");

  let criteria: Record<string, unknown>;
  try {
    criteria = await extractCriteria(query, model, token);
  } catch (err) {
    stream.markdown(`⚠️ Failed to extract search criteria: ${String(err)}`);
    return;
  }

  stream.progress("Searching your local Outlook profile…");

  const backendRequest: BackendRequest = {
    query,
    criteria,
    top_k: 10,
  };

  let backendResponse: BackendResponse;
  try {
    backendResponse = await callPythonBackend(backendRequest);
  } catch (err) {
    stream.markdown(
      `⚠️ Failed to query the Python backend: ${String(err)}\n\n` +
        "Ensure Python 3.11+ is installed, dependencies are installed (`pip install -r backend/requirements.txt`), " +
        "and Microsoft Outlook is running on Windows."
    );
    return;
  }

  if (backendResponse.error) {
    stream.markdown(`⚠️ Backend error: ${backendResponse.error}`);
    return;
  }

  stream.progress("Generating answer…");

  await generateAnswer(query, backendResponse.emails, model, token, stream);
}

export function activate(context: vscode.ExtensionContext): void {
  const participant = vscode.chat.createChatParticipant(PARTICIPANT_ID, handleRequest);
  participant.iconPath = vscode.Uri.joinPath(context.extensionUri, "images", "outlook-icon.png");
  context.subscriptions.push(participant);
}

export function deactivate(): void {
  // Nothing to clean up
}
