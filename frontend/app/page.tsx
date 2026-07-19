"use client";

import { useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type Derived = {
  domain: string;
  rubric: string;
  subreddits: string[];
};

type ScoredPost = {
  title: string;
  url: string;
  score: number;
  reason: string;
  community: string;
};

type Stage = "input" | "subreddits" | "results";

const MAX_SUBREDDITS = 5;

async function getApiError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail
        .map((item: { msg?: string }) => item.msg)
        .filter(Boolean)
        .join("; ");
    }
  } catch {
    // Fall through to a useful status-based message for non-JSON responses.
  }
  return `Request failed (${res.status})`;
}

export default function SiftDashboard() {
  const [stage, setStage] = useState<Stage>("input");
  const [userText, setUserText] = useState("");
  const [derived, setDerived] = useState<Derived | null>(null);
  const [subredditsText, setSubredditsText] = useState("");
  const [results, setResults] = useState<ScoredPost[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleAnalyze() {
    if (!userText.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/interpret`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_text: userText }),
      });
      if (!res.ok) throw new Error(await getApiError(res));
      const data: Derived = await res.json();
      setDerived(data);
      setSubredditsText(data.subreddits.join("\n"));
      setStage("subreddits");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  async function handleStartTracking() {
    if (!derived) return;
    const finalSubs = subredditsText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);

    if (finalSubs.length === 0) {
      setError("Add at least one subreddit before scanning.");
      return;
    }
    if (finalSubs.length > MAX_SUBREDDITS) {
      setError(
        `You entered ${finalSubs.length} subreddits. Phase 1 currently supports a maximum of ${MAX_SUBREDDITS} per scan. Remove ${finalSubs.length - MAX_SUBREDDITS} and try again.`,
      );
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/score`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          domain: derived.domain,
          rubric: derived.rubric,
          subreddits: finalSubs,
        }),
      });
      if (!res.ok) throw new Error(await getApiError(res));
      const data: { results: ScoredPost[]; warnings: string[] } = await res.json();
      setResults(data.results);
      setWarnings(data.warnings);
      setStage("results");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  function reset() {
    setStage("input");
    setUserText("");
    setDerived(null);
    setResults([]);
    setWarnings([]);
    setError(null);
  }

  return (
    <main className="min-h-screen px-6 py-12 md:px-16">
      <header className="mb-12 flex items-baseline gap-3">
        <h1 className="font-mono-tech text-2xl font-bold tracking-tight text-[var(--ink)]">
          sift
        </h1>
        <span className="text-sm text-[var(--ink-dim)]">
          signal detection for Reddit
        </span>
      </header>

      {error && (
        <div className="mb-6 rounded border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {stage === "input" && (
        <section className="max-w-2xl">
          <label className="mb-2 block text-sm text-[var(--ink-dim)]">
            What are you trying to find?
          </label>
              <textarea
            value={userText}
            onChange={(e) => setUserText(e.target.value)}
            placeholder="e.g. People talking about staying in sync with a partner, date night ideas, conversation habits, and moments where a tool like ours would be a direct fit..."
            rows={8}
            className="w-full rounded border border-[var(--border)] bg-[var(--panel)] p-4 text-[var(--ink)] placeholder:text-[var(--ink-dim)] focus:border-[var(--signal)] focus:outline-none"
          />
          <button
            onClick={handleAnalyze}
            disabled={loading || !userText.trim()}
            className="mt-4 rounded bg-[var(--signal)] px-5 py-2.5 font-medium text-[#04211D] transition hover:opacity-90 disabled:opacity-40"
          >
            {loading ? "Analyzing..." : "Analyze"}
          </button>
        </section>
      )}

      {stage === "subreddits" && derived && (
        <section className="max-w-2xl space-y-8">
          <div>
            <div className="mb-1 text-xs uppercase tracking-wide text-[var(--ink-dim)]">
              What Sift understood
            </div>
            <p className="text-[var(--ink)]">{derived.domain}</p>
          </div>
          <div>
            <div className="mb-1 text-xs uppercase tracking-wide text-[var(--ink-dim)]">
              How Sift will judge relevance
            </div>
            <p className="text-[var(--ink)]">{derived.rubric}</p>
          </div>
          <div>
            <div className="mb-1 text-xs uppercase tracking-wide text-[var(--ink-dim)]">
              Subreddits to scan (one per line — add, edit, or remove)
            </div>
            <textarea
              value={subredditsText}
              onChange={(e) => setSubredditsText(e.target.value)}
              rows={8}
                className="w-full rounded border border-[var(--border)] bg-[var(--panel)] p-4 font-mono-tech text-sm text-[var(--ink)] focus:border-[var(--signal)] focus:outline-none"
              />
              <p className="mt-2 text-xs text-[var(--ink-dim)]">
                Maximum {MAX_SUBREDDITS} subreddits per Phase 1 scan.
              </p>
          </div>
          <div className="flex gap-3">
            <button
              onClick={handleStartTracking}
              disabled={loading}
              className="rounded bg-[var(--signal)] px-5 py-2.5 font-medium text-[#04211D] transition hover:opacity-90 disabled:opacity-40"
            >
              {loading ? "Scanning..." : "Scan posts"}
            </button>
            <button
              onClick={reset}
              className="rounded border border-[var(--border)] px-5 py-2.5 text-[var(--ink-dim)] transition hover:text-[var(--ink)]"
            >
              Start over
            </button>
          </div>
        </section>
      )}

      {stage === "results" && (
        <section>
          <div className="mb-6 flex items-center justify-between">
            <div className="text-sm text-[var(--ink-dim)]">
              {results.length} posts ranked, highest relevance first
            </div>
            <button
              onClick={reset}
              className="rounded border border-[var(--border)] px-4 py-2 text-sm text-[var(--ink-dim)] transition hover:text-[var(--ink)]"
            >
              Start over
            </button>
          </div>

          {warnings.length > 0 && (
            <div className="mb-4 space-y-1">
              {warnings.map((w, i) => (
                <div key={i} className="text-xs text-[var(--warn)]">
                  {w}
                </div>
              ))}
            </div>
          )}

          <div className="overflow-x-auto rounded border border-[var(--border)]">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] text-xs uppercase tracking-wide text-[var(--ink-dim)]">
                  <th className="px-4 py-3">Relatability score</th>
                  <th className="px-4 py-3">Title</th>
                  <th className="px-4 py-3">Relatability reason</th>
                  <th className="px-4 py-3">Link</th>
                </tr>
              </thead>
              <tbody>
                {results.map((post, i) => (
                  <tr key={i} className="border-b border-[var(--border)] last:border-0">
                    <td className="px-4 py-3 align-top">
                      <div className="font-mono-tech text-[var(--signal)]">
                        {post.score}
                      </div>
                      <div className="mt-1 h-1 w-16 rounded-full bg-[var(--signal-dim)]">
                        <div
                          className="h-1 rounded-full bg-[var(--signal)]"
                          style={{ width: `${post.score}%` }}
                        />
                      </div>
                    </td>
                    <td className="max-w-xs px-4 py-3 align-top text-[var(--ink)]">
                      {post.title}
                      <div className="mt-1 text-xs text-[var(--ink-dim)]">
                        r/{post.community}
                      </div>
                    </td>
                    <td className="max-w-sm px-4 py-3 align-top text-[var(--ink-dim)]">
                      {post.reason}
                    </td>
                    <td className="px-4 py-3 align-top">
                      <a
                        href={post.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[var(--signal)] underline underline-offset-2 hover:opacity-80"
                      >
                        open
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  );
}
