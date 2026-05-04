import { useCallback, useState } from "react";

type ScoreBreakdown = Record<string, number | string | undefined>;

export type Creator = {
  name: string;
  platform: string;
  handle: string;
  profile_url: string;
  bio: string;
  follower_count: number | null;
  recent_content_summary: string;
  source_url: string;
  recent_posts: Record<string, unknown>[];
  avg_likes?: number | null;
  avg_comments?: number | null;
  engagement_rate?: number | null;
  fit_score: number | null;
  reason: string | null;
  score_breakdown: ScoreBreakdown;
};

function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (Number.isInteger(n)) return n.toLocaleString();
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function pct(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `${(n * 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
}

function platformStyle(platform: string): string {
  const p = platform.toLowerCase();
  if (p.includes("tiktok"))
    return "bg-gradient-to-r from-fuchsia-600/90 to-pink-600/90 text-white";
  if (p.includes("instagram"))
    return "bg-gradient-to-r from-purple-600 via-pink-600 to-orange-500 text-white";
  if (p.includes("youtube"))
    return "bg-red-600/95 text-white";
  return "bg-slate-600 text-white";
}

function scoreHue(score: number): string {
  if (score >= 75) return "text-emerald-400";
  if (score >= 55) return "text-amber-400";
  return "text-rose-400";
}

function engagementBasisLabel(basis: string | undefined): string {
  if (!basis) return "";
  const labels: Record<string, string> = {
    median_engagement_over_median_views:
      "Median (likes + comments) ÷ median views across recent posts",
    median_per_post_over_views: "Median (likes + comments) ÷ views on recent posts",
    single_post_over_views: "Single post with views — approximate",
    median_likes_comments_over_followers: "Median post engagement ÷ follower count",
    mean_avg_fields_over_followers: "Average likes + comments ÷ followers",
    precomputed: "Pre-enriched estimate",
  };
  return labels[basis] || basis.replace(/_/g, " ");
}

function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function creatorsToCsv(rows: Creator[]): string {
  const cols = [
    "name",
    "platform",
    "handle",
    "profile_url",
    "bio",
    "follower_count",
    "recent_content_summary",
    "source_url",
    "fit_score",
    "reason",
  ] as const;

  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  };

  const header = cols.join(",");
  const lines = rows.map((r) =>
    cols.map((c) => esc(r[c as keyof Creator])).join(",")
  );
  return [header, ...lines].join("\n");
}

function downloadCsv(filename: string, rows: Creator[]) {
  const csv = creatorsToCsv(rows);
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

const BREAKDOWN_LABELS: [string, string][] = [
  ["relevance", "Relevance"],
  ["audience_fit", "Audience fit"],
  ["creator_size", "Creator size"],
  ["engagement_quality", "Engagement"],
  ["content_quality", "Content quality"],
  ["commercial_fit", "Commercial fit"],
  ["brand_safety", "Brand safety"],
];

function CreatorCard({ c, rank }: { c: Creator; rank: number }) {
  const [bioOpen, setBioOpen] = useState(false);
  const [postsOpen, setPostsOpen] = useState(false);
  const score = c.fit_score ?? 0;
  const bd = c.score_breakdown || {};

  return (
    <article className="creator-card">
      <div className="creator-card-inner">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-4">
            <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-slate-800 to-slate-900 font-display text-lg font-bold text-emerald-400 shadow-lg shadow-emerald-950/30 ring-2 ring-emerald-500/25">
              {rank}
            </span>
            <div>
              <h3 className="font-display text-xl font-semibold tracking-tight text-white md:text-2xl">
                {c.name}
              </h3>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span
                  className={`rounded-full px-3 py-0.5 text-xs font-semibold uppercase tracking-wide shadow-md ${platformStyle(c.platform)}`}
                >
                  {c.platform}
                </span>
                <span className="font-mono text-sm text-slate-400">{c.handle}</span>
              </div>
            </div>
          </div>
          <div className="rounded-2xl border border-slate-700/80 bg-slate-900/70 px-5 py-3 text-right shadow-inner shadow-black/20 ring-1 ring-white/5">
            <div className={`font-display text-4xl font-bold tabular-nums leading-none ${scoreHue(score)}`}>
              {c.fit_score ?? "—"}
            </div>
            <div className="mt-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">
              Fit score
            </div>
          </div>
        </div>

        <div className="mt-6 rounded-2xl border border-emerald-500/35 bg-gradient-to-br from-emerald-950/50 via-slate-950/70 to-slate-950/90 p-5 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04),0_0_40px_-12px_rgba(16,185,129,0.15)] ring-1 ring-emerald-500/20">
          <p className="font-display text-[11px] font-semibold uppercase tracking-[0.22em] text-emerald-400/95">
            Why this creator fits
          </p>
          <p className="mt-3 leading-relaxed text-slate-100">
            {c.reason?.trim()
              ? c.reason
              : "No fit summary was generated for this creator. Re-run the search or check API keys."}
          </p>
        </div>

        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          <div className="rounded-xl border border-slate-800/90 bg-slate-950/50 p-4 shadow-inner shadow-black/30 ring-1 ring-white/[0.03]">
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">
              Recent content
            </div>
            <p className="mt-2 text-sm leading-relaxed text-slate-200">
              {c.recent_content_summary || "—"}
            </p>
          </div>
          <div className="rounded-xl border border-slate-800/90 bg-slate-950/50 p-4 shadow-inner shadow-black/30 ring-1 ring-white/[0.03]">
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Metrics</div>
            <dl className="mt-2 space-y-1.5 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">Followers</dt>
                <dd className="tabular-nums text-slate-200">{fmtNum(c.follower_count)}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">Avg likes</dt>
                <dd className="tabular-nums text-slate-200">{fmtNum(c.avg_likes ?? null)}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">Avg comments</dt>
                <dd className="tabular-nums text-slate-200">{fmtNum(c.avg_comments ?? null)}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">Engagement rate</dt>
                <dd className="text-right">
                  <span className="tabular-nums text-slate-200">{pct(c.engagement_rate ?? null)}</span>
                  {bd.engagement_rate_basis ? (
                    <span className="mt-1 block max-w-[14rem] text-[11px] leading-snug text-slate-500">
                      {engagementBasisLabel(String(bd.engagement_rate_basis))}
                    </span>
                  ) : null}
                </dd>
              </div>
            </dl>
          </div>
        </div>

        <div className="mt-6 flex flex-wrap gap-3">
          <a
            href={c.profile_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-teal-600 to-emerald-600 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-emerald-950/40 transition hover:from-teal-500 hover:to-emerald-500"
          >
            Profile ↗
          </a>
          <a
            href={c.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-full border border-slate-600/90 bg-slate-800/60 px-5 py-2.5 text-sm font-medium text-slate-200 backdrop-blur-sm transition hover:border-emerald-500/40 hover:bg-slate-800/90"
          >
            Discovery link ↗
          </a>
        </div>

        <div className="mt-5 rounded-xl border border-slate-800/80 bg-slate-900/40 p-4 ring-1 ring-white/[0.03]">
          <button
            type="button"
            onClick={() => setBioOpen(!bioOpen)}
            className="flex w-full items-center justify-between text-left text-sm font-medium text-slate-300 transition hover:text-white"
          >
            Bio {bioOpen ? "▼" : "▶"}
          </button>
          {bioOpen && (
            <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-slate-400">
              {c.bio || "—"}
            </p>
          )}
        </div>

        <div className="mt-3 rounded-xl border border-slate-800/80 bg-slate-900/40 p-4 ring-1 ring-white/[0.03]">
          <button
            type="button"
            onClick={() => setPostsOpen(!postsOpen)}
            className="flex w-full items-center justify-between text-left text-sm font-medium text-slate-300 transition hover:text-white"
          >
            Recent posts ({c.recent_posts?.length ?? 0}) {postsOpen ? "▼" : "▶"}
          </button>
          {postsOpen && (
            <ul className="mt-3 space-y-2 text-sm text-slate-400">
              {(c.recent_posts || []).slice(0, 12).map((p, i) => (
                <li
                  key={i}
                  className="rounded-lg border border-slate-800/60 bg-slate-950/60 p-3 font-mono text-xs leading-relaxed"
                >
                  {JSON.stringify(p, null, 0).slice(0, 500)}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-8 border-t border-slate-800/80 pt-6">
          <h4 className="font-display text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
            Score breakdown
          </h4>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {BREAKDOWN_LABELS.map(([key, label]) => {
              const v = bd[key];
              const n = typeof v === "number" ? v : undefined;
              return (
                <div
                  key={key}
                  className="flex items-center justify-between rounded-lg border border-slate-800/50 bg-slate-900/40 px-3 py-2 text-sm transition hover:border-slate-700/80"
                >
                  <span className="text-slate-500">{label}</span>
                  <span className="tabular-nums font-medium text-slate-200">
                    {n !== undefined ? n : "—"}
                  </span>
                </div>
              );
            })}
            <div className="flex items-center justify-between rounded-lg border border-emerald-800/40 bg-emerald-950/30 px-3 py-2 text-sm ring-1 ring-emerald-500/20 sm:col-span-2">
              <span className="font-medium text-emerald-400/95">Fit score (weighted + calibrated)</span>
              <span className="tabular-nums font-bold text-emerald-400">
                {typeof bd.total === "number" ? bd.total : c.fit_score ?? "—"}
              </span>
            </div>
          </div>
        {bd.legacy_rubric_sum != null && (
          <p className="mt-2 text-xs text-slate-600">
            Underlying rubric sum (audit): {String(bd.legacy_rubric_sum)}
            {bd.weighted_rank_pre_calibration != null && (
              <> · weighted pre-calibration: {String(bd.weighted_rank_pre_calibration)}</>
            )}
          </p>
        )}
        {(bd.keyword_relevance !== undefined || bd.llm_relevance !== undefined) && (
          <p className="mt-3 text-xs text-slate-500">
            Keyword relevance: {fmtNum(bd.keyword_relevance as number)}
            {" · "}
            LLM relevance: {fmtNum(bd.llm_relevance as number)}
          </p>
        )}
        {bd.viral_skew_max_over_median != null && (
          <p className="mt-2 text-xs text-slate-500">
            Engagement consistency: max/median likes ≈ {String(bd.viral_skew_max_over_median)}
            {bd.engagement_consistency_multiplier != null && (
              <> · tier multiplier {String(bd.engagement_consistency_multiplier)}</>
            )}
            {bd.engagement_quality_raw_tier != null && (
              <> · raw engagement tier {String(bd.engagement_quality_raw_tier)} → final{" "}
              {String(bd.engagement_quality)}</>
            )}
            {bd.engagement_rate_basis && (
              <span className="block text-slate-600">Basis: {String(bd.engagement_rate_basis)}</span>
            )}
          </p>
        )}
        </div>
      </div>
    </article>
  );
}

function TableView({ rows }: { rows: Creator[] }) {
  return (
    <div className="table-shell overflow-x-auto">
      <table className="w-full min-w-[1100px] text-left text-sm">
        <thead>
          <tr className="border-b border-emerald-950/30 bg-slate-900/95">
            <th className="px-4 py-3 font-display text-xs uppercase tracking-wider text-slate-500">
              #
            </th>
            <th className="px-4 py-3 font-display text-xs uppercase tracking-wider text-slate-500">
              Name
            </th>
            <th className="px-4 py-3 font-display text-xs uppercase tracking-wider text-slate-500">
              Platform
            </th>
            <th className="px-4 py-3 font-display text-xs uppercase tracking-wider text-slate-500">
              Handle
            </th>
            <th className="px-4 py-3 font-display text-xs uppercase tracking-wider text-slate-500">
              Score
            </th>
            <th className="px-4 py-3 font-display text-xs uppercase tracking-wider text-slate-500">
              Reason
            </th>
            <th className="px-4 py-3 font-display text-xs uppercase tracking-wider text-slate-500">
              Followers
            </th>
            <th className="px-4 py-3 font-display text-xs uppercase tracking-wider text-slate-500">
              Summary
            </th>
            <th className="px-4 py-3 font-display text-xs uppercase tracking-wider text-slate-500">
              Links
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/90 bg-slate-950/30">
          {rows.map((c, i) => (
            <tr
              key={`${c.profile_url}-${i}`}
              className="transition hover:bg-emerald-950/10 hover:shadow-[inset_0_0_0_1px_rgba(16,185,129,0.08)]"
            >
              <td className="px-4 py-3 tabular-nums text-slate-500">{i + 1}</td>
              <td className="max-w-[140px] px-4 py-3 font-medium text-slate-100">{c.name}</td>
              <td className="px-4 py-3">
                <span
                  className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${platformStyle(c.platform)}`}
                >
                  {c.platform}
                </span>
              </td>
              <td className="px-4 py-3 font-mono text-xs text-slate-400">{c.handle}</td>
              <td className={`px-4 py-3 font-display font-bold tabular-nums ${scoreHue(c.fit_score ?? 0)}`}>
                {c.fit_score ?? "—"}
              </td>
              <td className="max-w-[220px] px-4 py-3 text-xs text-slate-400">
                <div className="line-clamp-3">{c.reason || "—"}</div>
              </td>
              <td className="px-4 py-3 tabular-nums text-slate-300">
                {fmtNum(c.follower_count)}
              </td>
              <td className="max-w-md px-4 py-3 text-slate-400">
                <div className="line-clamp-3">{c.recent_content_summary}</div>
              </td>
              <td className="px-4 py-3">
                <div className="flex flex-col gap-1">
                  <a href={c.profile_url} className="text-emerald-400 hover:underline" target="_blank" rel="noreferrer">
                    Profile
                  </a>
                  <a href={c.source_url} className="text-slate-500 hover:underline" target="_blank" rel="noreferrer">
                    Source
                  </a>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function App() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creators, setCreators] = useState<Creator[]>([]);
  const [lastQuery, setLastQuery] = useState("");
  const [view, setView] = useState<"cards" | "table">("cards");

  const run = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q }),
      });
      const data = await res.json();
      if (!res.ok) {
        const d = data.detail;
        const msg = Array.isArray(d)
          ? d.map((x: { msg?: string }) => x.msg || JSON.stringify(x)).join("; ")
          : d || data.message || res.statusText;
        throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
      }
      setCreators(data.creators || []);
      setLastQuery(data.query || q);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
      setCreators([]);
    } finally {
      setLoading(false);
    }
  }, [query]);

  return (
    <div className="relative min-h-screen overflow-x-hidden">
      <div className="pointer-events-none fixed inset-0 z-0">
        <div className="absolute left-[15%] top-[18%] h-[420px] w-[420px] -translate-x-1/2 rounded-full bg-emerald-500/12 blur-[120px]" />
        <div className="absolute right-[10%] top-[35%] h-[360px] w-[360px] rounded-full bg-teal-500/10 blur-[100px]" />
        <div className="absolute bottom-[5%] left-1/2 h-[280px] w-[600px] -translate-x-1/2 rounded-full bg-slate-800/40 blur-[90px]" />
      </div>

      <div className="relative z-10 mx-auto max-w-6xl px-4 py-12 md:px-8 md:py-16">
        <header className="text-center">
          <p className="font-display text-sm font-semibold uppercase tracking-[0.28em] text-emerald-400/95">
            Creator Sourcing Agent
          </p>
          <h1 className="mt-4 font-display text-4xl font-bold tracking-tight text-white drop-shadow-sm md:text-5xl md:leading-tight">
            Find creators your audience already trusts
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg leading-relaxed text-slate-400">
            Describe your niche or brand. We search the open web, enrich TikTok, Instagram, and YouTube
            profiles, score fit on a transparent 100-point rubric, and show everything here — same data as{" "}
            <span className="code-chip">creators.json</span> and <span className="code-chip">creators.csv</span>.
          </p>
        </header>

        <div className="mx-auto mt-14 max-w-3xl">
          <div className="search-pill">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
              placeholder="e.g. dog wellness creators, pet supplements Instagram under 50k"
              className="search-pill-input"
              disabled={loading}
            />
            <button
              type="button"
              onClick={run}
              disabled={loading || !query.trim()}
              className="search-pill-btn font-display"
            >
              {loading ? "Working…" : "Run search"}
            </button>
          </div>
          <p className="mt-4 text-center text-sm text-slate-500">
            Runs can take several minutes while search and social APIs finish.
          </p>
        </div>

        {error && (
          <div
            className="mx-auto mt-10 max-w-3xl rounded-2xl border border-rose-500/30 bg-rose-950/35 px-5 py-4 text-center text-rose-100 shadow-lg shadow-rose-950/20 backdrop-blur-md"
            role="alert"
          >
            {error}
          </div>
        )}

        {loading && (
          <div className="mx-auto mt-14 flex max-w-md flex-col items-center justify-center gap-5 rounded-2xl border border-slate-700/50 bg-slate-900/50 px-8 py-10 shadow-2xl backdrop-blur-md">
            <div className="h-14 w-14 animate-spin rounded-full border-2 border-slate-700 border-t-emerald-400 shadow-[0_0_24px_rgba(16,185,129,0.25)]" />
            <p className="text-center font-medium text-slate-300">Searching, enriching, scoring…</p>
            <p className="text-center text-sm text-slate-500">Hang tight — social APIs can be slow.</p>
          </div>
        )}

        {!loading && creators.length > 0 && (
          <section className="mt-16 md:mt-20">
            <div className="flex flex-col gap-6 border-b border-slate-800/80 pb-8 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="font-display text-xs font-semibold uppercase tracking-[0.22em] text-emerald-500/90">
                  Enriched results
                </p>
                <h2 className="mt-2 font-display text-3xl font-bold tracking-tight text-white md:text-4xl">
                  Ranked creators
                </h2>
                <p className="mt-3 max-w-xl text-slate-400">
                  <span className="font-semibold text-emerald-400/90">{creators.length}</span> profiles
                  scored for{" "}
                  <span className="rounded-full border border-slate-600/80 bg-slate-900/80 px-3 py-1 text-sm text-slate-200">
                    “{lastQuery}”
                  </span>
                </p>
              </div>
              <div className="flex flex-col gap-3 sm:items-end">
                <div className="results-toolbar">
                  <button
                    type="button"
                    onClick={() => setView("cards")}
                    className={`results-toolbar-btn ${view === "cards" ? "bg-gradient-to-r from-teal-700 to-emerald-700 text-white shadow-md" : "text-slate-400 hover:text-white"}`}
                  >
                    Cards
                  </button>
                  <button
                    type="button"
                    onClick={() => setView("table")}
                    className={`results-toolbar-btn ${view === "table" ? "bg-gradient-to-r from-teal-700 to-emerald-700 text-white shadow-md" : "text-slate-400 hover:text-white"}`}
                  >
                    Table
                  </button>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => downloadJson("creators.json", creators)}
                    className="rounded-full border border-slate-600/80 bg-slate-900/70 px-4 py-2 text-sm font-medium text-slate-200 backdrop-blur-sm transition hover:border-emerald-500/35 hover:text-white"
                  >
                    Download JSON
                  </button>
                  <button
                    type="button"
                    onClick={() => downloadCsv("creators.csv", creators)}
                    className="rounded-full border border-slate-600/80 bg-slate-900/70 px-4 py-2 text-sm font-medium text-slate-200 backdrop-blur-sm transition hover:border-emerald-500/35 hover:text-white"
                  >
                    Download CSV
                  </button>
                </div>
              </div>
            </div>

            {view === "cards" ? (
              <div className="mt-12 space-y-12 md:space-y-14">
                {creators.map((c, i) => (
                  <CreatorCard key={`${c.profile_url}-${i}`} c={c} rank={i + 1} />
                ))}
              </div>
            ) : (
              <div className="mt-12">
                <TableView rows={creators} />
              </div>
            )}

            <p className="mt-12 text-center text-sm text-slate-500">
              The server also persists JSON/CSV (see <span className="code-chip text-slate-300">/api/latest-files</span>
              ); locally that is <span className="code-chip text-slate-300">outputs/</span>, on Vercel a temp folder.
            </p>
          </section>
        )}
      </div>
    </div>
  );
}
