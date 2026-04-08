import { spawn } from "child_process";
import * as path from "path";
import * as fs from "fs";

/** Structured search criteria extracted from the natural-language query. */
export interface SearchCriteria {
  sender: string | null;
  recipient: string | null;
  subject_keywords: string[];
  body_keywords: string[];
  date_start: string | null;
  date_end: string | null;
  folder: string | null;
  read_state: "read" | "unread" | null;
  has_attachment: boolean | null;
  importance: "high" | "normal" | "low" | null;
  categories: string[];
}

/** Request payload sent to the Python backend via stdin. */
export interface BackendRequest {
  query: string;
  criteria: Record<string, unknown>;
  top_k: number;
}

/** A single email result returned by the Python backend. */
export interface EmailResult {
  subject: string;
  sender: string;
  date: string;
  snippet: string;
  score: number;
  folder: string;
}

/** Response payload received from the Python backend via stdout. */
export interface BackendResponse {
  emails: EmailResult[];
  error: string | null;
}

/**
 * Resolve the absolute path of the Python backend entry point.
 * Supports both development (workspace root) and packaged extension layouts.
 */
function resolveBackendScript(extensionRoot?: string): string {
  const candidates = [
    // Packaged extension: backend is bundled alongside the extension
    extensionRoot ? path.join(extensionRoot, "backend", "main.py") : "",
    // Development: resolve relative to this compiled JS file (out/pythonBackend.js → backend/main.py)
    path.join(__dirname, "..", "backend", "main.py"),
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  // Fallback: let the OS report the error naturally
  return candidates[candidates.length - 1];
}

/**
 * Determine the Python executable to use.
 * Prefers `python3` on Unix-like systems and falls back to `python`.
 */
function resolvePythonExecutable(): string {
  return process.platform === "win32" ? "python" : "python3";
}

/**
 * Invoke the Python backend as a subprocess.
 *
 * The backend script reads a JSON-encoded {@link BackendRequest} from stdin
 * and writes a JSON-encoded {@link BackendResponse} to stdout.
 *
 * @param request - The search request to send to the backend.
 * @param extensionRoot - Optional path to the extension root for script resolution.
 * @param timeoutMs - Maximum time to wait for the backend (default 60 s).
 */
export function callPythonBackend(
  request: BackendRequest,
  extensionRoot?: string,
  timeoutMs = 60_000
): Promise<BackendResponse> {
  return new Promise((resolve, reject) => {
    const scriptPath = resolveBackendScript(extensionRoot);
    const pythonExe = resolvePythonExecutable();

    const child = spawn(pythonExe, [scriptPath], {
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });

    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });

    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`Python backend timed out after ${timeoutMs / 1000} seconds`));
    }, timeoutMs);

    child.on("close", (code) => {
      clearTimeout(timer);

      if (code !== 0) {
        reject(
          new Error(
            `Python backend exited with code ${code}.\nStderr: ${stderr.slice(0, 500)}`
          )
        );
        return;
      }

      // Extract the last JSON object from stdout (backend may emit log lines before it)
      const jsonMatch = stdout.match(/(\{[\s\S]*\})\s*$/);
      if (!jsonMatch) {
        reject(
          new Error(
            `Python backend produced no JSON output.\nStdout: ${stdout.slice(0, 500)}\nStderr: ${stderr.slice(0, 500)}`
          )
        );
        return;
      }

      try {
        const response = JSON.parse(jsonMatch[1]) as BackendResponse;
        resolve(response);
      } catch (err) {
        reject(new Error(`Failed to parse Python backend response: ${String(err)}`));
      }
    });

    child.on("error", (err) => {
      clearTimeout(timer);
      reject(new Error(`Failed to spawn Python backend (${pythonExe}): ${String(err)}`));
    });

    // Send the request payload to the backend via stdin
    child.stdin.write(JSON.stringify(request));
    child.stdin.end();
  });
}
