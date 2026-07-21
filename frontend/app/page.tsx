"use client";

import { useEffect, useState } from "react";

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
type ScanStatus = "idle" | "queued" | "running" | "completed" | "failed";

type ScanSnapshot = {
  scan_id: string;
  status: Exclude<ScanStatus, "idle">;
  total_posts: number;
  processed_posts: number;
  results: ScoredPost[];
  warnings: string[];
  error: string | null;
};

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
  const [scanId, setScanId] = useState<string | null>(null);
  const [scanStatus, setScanStatus] = useState<ScanStatus>("idle");
  const [totalPosts, setTotalPosts] = useState(0);
  const [processedPosts, setProcessedPosts] = useState(0);

  // A scan continues in the worker after POST /scans returns. Poll its Redis-
  // backed API snapshot until the worker marks it completed or failed.
  useEffect(() => {
    if (!scanId) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function pollScan() {
      try {
        const res = await fetch(`${API_BASE}/scans/${scanId}`);
        if (!res.ok) throw new Error(await getApiError(res));
        const data: ScanSnapshot = await res.json();
        if (cancelled) return;

        setScanStatus(data.status);
        setTotalPosts(data.total_posts);
        setProcessedPosts(data.processed_posts);
        setResults(data.results);
        setWarnings(data.warnings);

        // Terminal states stop polling. Active states schedule another check
        // instead of holding one long HTTP request open.
        if (data.status === "failed") {
          setError(data.error ?? "The scan failed.");
          return;
        }
        if (data.status !== "completed") {
          timer = setTimeout(pollScan, 1500);
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Could not read scan progress");
        // A temporary network/backend failure should not lose the scan; retry
        // more slowly while preserving its scan ID.
        timer = setTimeout(pollScan, 3000);
      }
    }

    pollScan();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [scanId]);

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
      const res = await fetch(`${API_BASE}/scans`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          domain: derived.domain,
          rubric: derived.rubric,
          subreddits: finalSubs,
        }),
      });
      if (!res.ok) throw new Error(await getApiError(res));
      const data: { scan_id: string; status: "queued" } = await res.json();
      // Moving to results immediately is possible because the worker owns the
      // slow fetch-and-score work; the polling effect follows its progress.
      setResults([]);
      setWarnings([]);
      setTotalPosts(0);
      setProcessedPosts(0);
      setScanStatus(data.status);
      setScanId(data.scan_id);
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
    setScanId(null);
    setScanStatus("idle");
    setTotalPosts(0);
    setProcessedPosts(0);
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
              Maximum {MAX_SUBREDDITS} subreddits per scan.
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
              {scanStatus === "queued" && "Scan queued — waiting for a worker"}
              {scanStatus === "running" &&
                `Scoring posts: ${processedPosts} of ${totalPosts || "..."}`}
              {scanStatus === "completed" &&
                `${results.length} posts ranked, highest relevance first`}
              {scanStatus === "failed" && "Scan failed"}
            </div>
            <button
              onClick={reset}
              className="rounded border border-[var(--border)] px-4 py-2 text-sm text-[var(--ink-dim)] transition hover:text-[var(--ink)]"
            >
              Start over
            </button>
          </div>

          {(scanStatus === "queued" || scanStatus === "running") && (
            <div className="mb-6 max-w-xl">
              <div className="h-2 overflow-hidden rounded-full bg-[var(--signal-dim)]">
                <div
                  className="h-full rounded-full bg-[var(--signal)] transition-all duration-500"
                  style={{
                    width: totalPosts
                      ? `${Math.round((processedPosts / totalPosts) * 100)}%`
                      : "8%",
                  }}
                />
              </div>
              <p className="mt-2 text-xs text-[var(--ink-dim)]">
                You can leave this page open while the worker processes the scan.
              </p>
            </div>
          )}

          {warnings.length > 0 && (
            <div className="mb-4 space-y-1">
              {warnings.map((w, i) => (
                <div key={i} className="text-xs text-[var(--warn)]">
                  {w}
                </div>
              ))}
            </div>
          )}

          {results.length > 0 && (
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
          )}
        </section>
      )}
    </main>
  );
}
