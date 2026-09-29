import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useJobResults, useRunAnalysis } from "@/api/queries/hooks";
import { api, PixelForgeError } from "@/api/client";
import { CompareViewer, panesFromResults } from "@/components/viewer/CompareViewer";
import { MetricsPanel, RunSummary } from "./MetricsPanel";
import { UncertaintyPanel } from "./UncertaintyPanel";
import { Analyst } from "@/features/chat/Analyst";
import {
  Badge, ErrorBox, Panel, ProvenanceNotice, Skeleton, Stat, Unavailable,
} from "@/components/common/Primitives";
import { formatBytes, timeUntil } from "@/lib/utils";

const TABS = ["Compare", "Metrics", "Uncertainty", "Analysis", "Export"] as const;
type Tab = typeof TABS[number];

export function Results() {
  const { jobId } = useParams();
  const { data, isLoading, error } = useJobResults(jobId);
  const [tab, setTab] = useState<Tab>("Compare");
  const [analystOpen, setAnalystOpen] = useState(true);

  if (isLoading) {
    return <div className="mx-auto max-w-7xl space-y-4 px-6 py-10">
      <Skeleton className="h-8 w-64" /><Skeleton className="h-[60vh] w-full" />
    </div>;
  }
  if (error) {
    const e = error as PixelForgeError;
    return <div className="mx-auto max-w-2xl px-6 py-16">
      <ErrorBox code={e.code} message={e.message} details={e.details} />
      <Link to="/explore" className="btn-ghost mt-4">Back to explorer</Link>
    </div>;
  }
  if (!data) return null;

  const panes = panesFromResults(data.input.artifacts, data.runs.filter((r) => r.status === "completed"));

  return (
    <div className="mx-auto max-w-[1800px] px-4 py-6 md:px-6">
      {/* header */}
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="label-mono mb-1">Results · {data.job_id}</div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {data.terrain
              ? `${data.terrain.class.charAt(0).toUpperCase()}${data.terrain.class.slice(1)} scene`
              : "Super-resolution results"}
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge tone="ok">{data.runs.filter((r) => r.status === "completed").length} run(s)</Badge>
            {data.partial_success && <Badge tone="warn">partial success</Badge>}
            {data.terrain && (
              <Badge tone="neutral">
                {(data.terrain.confidence * 100).toFixed(0)}% {data.terrain.class}
              </Badge>
            )}
            <Badge tone="neutral">expires in {timeUntil(data.expires_at)}</Badge>
          </div>
        </div>
        <button onClick={() => setAnalystOpen(!analystOpen)} className="btn-ghost text-xs">
          {analystOpen ? "Hide" : "Show"} analyst
        </button>
      </div>

      {/* U0 — always present, never dismissible */}
      <div className="mb-5">
        <ProvenanceNotice text={data.provenance.disclaimer} />
      </div>

      <div className={`grid gap-5 ${analystOpen ? "xl:grid-cols-[1fr_360px]" : ""}`}>
        <div className="min-w-0">
          <div className="mb-4 flex gap-1 overflow-x-auto border-b border-line">
            {TABS.map((t) => (
              <button key={t} onClick={() => setTab(t)}
                className={`whitespace-nowrap border-b-2 px-4 py-2.5 text-sm transition-colors
                  ${tab === t ? "border-signal text-signal"
                              : "border-transparent text-muted hover:text-bright"}`}>
                {t}
              </button>
            ))}
          </div>

          {tab === "Compare" && (
            <div className="space-y-5">
              <div className="h-[58vh] min-h-[420px] overflow-hidden rounded-lg border border-line">
                <CompareViewer panes={panes} className="h-full" />
              </div>
              <p className="text-xs leading-relaxed text-muted">
                All panes share one zoom and pan, and were rendered with the same contrast
                stretch computed from the original observation — so what you see is a
                difference in the models, not in their presentation.
              </p>
              {data.runs.filter((r) => r.status === "completed").map((r) => (
                <Panel key={r.run_id} title={r.model_display_name}
                       subtitle={r.is_baseline
                         ? "Reference baseline — adds no information beyond the original observation"
                         : `${r.model_key} · ${r.model_version ?? "version n/a"}`}>
                  <RunSummary run={r} />
                  {r.warnings.length > 0 && (
                    <ul className="mt-4 space-y-1">
                      {r.warnings.map((w, i) => (
                        <li key={i} className="border-l-2 border-warn/40 pl-3 text-xs text-warn">{w}</li>
                      ))}
                    </ul>
                  )}
                </Panel>
              ))}
              {data.runs.filter((r) => r.status === "failed").map((r) => (
                <Panel key={r.run_id} title={`${r.model_display_name} — failed`}>
                  <ErrorBox code={r.error?.code} message={r.error?.message ?? "Inference failed."} />
                </Panel>
              ))}
            </div>
          )}

          {tab === "Metrics" && <MetricsPanel runs={data.runs} />}

          {tab === "Uncertainty" && (
            <UncertaintyPanel info={data.uncertainty} artifacts={data.job_artifacts} />
          )}

          {tab === "Analysis" && <AnalysisTab jobId={data.job_id} data={data} />}

          {tab === "Export" && <ExportTab jobId={data.job_id} data={data} />}
        </div>

        {analystOpen && (
          <aside className="h-[70vh] overflow-hidden rounded-lg border border-line bg-panel xl:sticky xl:top-20">
            <Analyst jobId={data.job_id} />
          </aside>
        )}
      </div>
    </div>
  );
}

function AnalysisTab({ jobId, data }: { jobId: string; data: any }) {
  const run = useRunAnalysis(jobId);
  const existing: string[] = data.analyses.map((a: any) => a.type);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-2">
        {["ndvi", "ndwi", "ndbi"].map((t) => (
          <button key={t} onClick={() => run.mutate({ type: t })}
                  disabled={run.isPending || existing.includes(t)}
                  className="btn-ghost text-xs uppercase">
            {existing.includes(t) ? `${t} ✓` : `Run ${t}`}
          </button>
        ))}
      </div>

      {data.analyses.length === 0 && (
        <Unavailable title="No analysis has been run yet"
                     reason="Choose an index above. It is computed on both the reconstruction and the original 10 m imagery, so you can see what changed." />
      )}

      {data.analyses.map((a: any) => (
        <Panel key={a.analysis_id} title={a.sr.label ?? a.type.toUpperCase()}
               subtitle={a.sr.formula}>
          <div className="grid gap-5 md:grid-cols-2">
            {["sr", "lr"].map((which) => {
              const art = a.artifacts?.find((x: any) => x.kind === `analysis_preview_${which}`);
              const stats = which === "sr" ? a.sr : a.lr;
              return (
                <figure key={which}>
                  <figcaption className="label-mono mb-2">
                    {which === "sr" ? `Super-resolved · ${stats.gsd_m} m`
                                    : `Original observation · ${stats.gsd_m} m`}
                  </figcaption>
                  {art && <img src={art.url} alt="" className="w-full rounded border border-line bg-void" />}
                  <div className="mt-3 grid grid-cols-3 gap-3">
                    <Stat label="Mean" value={stats.stats?.mean?.toFixed(3) ?? "—"} />
                    <Stat label="Median" value={stats.stats?.median?.toFixed(3) ?? "—"} />
                    <Stat label="Std dev" value={stats.stats?.std?.toFixed(3) ?? "—"} />
                  </div>
                </figure>
              );
            })}
          </div>

          {a.comparison_with_lr && (
            <div className="mt-5 rounded-lg border border-line bg-abyss/60 p-4">
              <div className="label-mono mb-2">Difference from the original</div>
              <p className="font-mono text-sm text-bright">
                mean Δ {a.comparison_with_lr.mean_delta?.toFixed(4) ?? "—"}
              </p>
              <p className="mt-2 text-xs leading-relaxed text-muted">
                {a.comparison_with_lr.caveat}
              </p>
            </div>
          )}
        </Panel>
      ))}
    </div>
  );
}

function ExportTab({ jobId, data }: { jobId: string; data: any }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);

  async function make(format: "json" | "zip" | "pdf") {
    setBusy(format); setNotes([]);
    try {
      const r = await api.createReport(jobId, format);
      setNotes(r.notes ?? []);
      window.open(r.download_url, "_blank");
    } catch (e: any) { setNotes([e.message]); }
    finally { setBusy(null); }
  }

  const files = [...data.job_artifacts,
                 ...data.runs.flatMap((r: any) => r.artifacts)];

  return (
    <div className="space-y-5">
      <Panel title="Reports">
        <div className="flex flex-wrap gap-2">
          {(["zip", "json", "pdf"] as const).map((f) => (
            <button key={f} onClick={() => make(f)} disabled={!!busy}
                    className="btn-ghost text-xs uppercase">
              {busy === f ? "Building…" : f === "zip" ? "Full bundle (.zip)" : `Report (.${f})`}
            </button>
          ))}
        </div>
        {notes.map((n, i) => (
          <p key={i} className="mt-3 text-xs leading-relaxed text-warn">{n}</p>
        ))}
        <p className="mt-3 text-xs leading-relaxed text-muted">
          The bundle contains every raster as a Cloud-Optimized GeoTIFF with provenance in
          its metadata tags, plus the original 10 m observation so results can always be
          checked against what the sensor recorded.
        </p>
      </Panel>

      <Panel title="Individual files" subtitle={`${files.length} artefacts`}>
        <ul className="divide-y divide-line">
          {files.map((a: any) => (
            <li key={a.artifact_id} className="flex items-center justify-between gap-4 py-2.5">
              <div className="min-w-0">
                <div className="truncate text-sm text-bright">{a.kind.replace(/_/g, " ")}</div>
                <div className="font-mono text-2xs text-muted">
                  {a.format} · {formatBytes(a.bytes)}
                  {a.width && ` · ${a.width}×${a.height}`}
                  {a.crs && ` · ${a.crs}`}
                </div>
              </div>
              <a href={a.download_url} target="_blank" rel="noreferrer"
                 className="btn-ghost shrink-0 !px-3 !py-1.5 text-xs">Download</a>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
