import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useJobStatus, useModels, useUpload } from "@/api/queries/hooks";
import { api, PixelForgeError } from "@/api/client";
import { Badge, ErrorBox, Panel } from "@/components/common/Primitives";
import { formatBytes, formatDuration } from "@/lib/utils";
import type { SceneSummary, Stage } from "@/types/api";

export function Process() {
  const { jobId } = useParams();
  return jobId ? <JobProgress jobId={jobId} /> : <UploadFlow />;
}

/* ------------------------------------------------------------------ upload */

function UploadFlow() {
  const navigate = useNavigate();
  const upload = useUpload();
  const { data: modelData } = useModels();
  const [scene, setScene] = useState<SceneSummary | null>(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<PixelForgeError | null>(null);
  const [dragging, setDragging] = useState(false);
  const [starting, setStarting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const available = (modelData?.models ?? []).filter((m) => m.status === "available");
  const [models, setModels] = useState<string[]>([]);
  const chosen = models.length ? models : available.slice(0, 1).map((m) => m.key);

  async function handleFile(file: File, force = false) {
    setError(null); setProgress(0);
    try {
      const result = await upload.mutateAsync({ file, force, onProgress: setProgress });
      setScene(result);
    } catch (e) { setError(e as PixelForgeError); }
  }

  async function start() {
    if (!scene?.scene_id) return;
    setStarting(true);
    try {
      const job = await api.createJob({
        scene_id: scene.scene_id, models: chosen,
        options: { compute_metrics: true, compute_uncertainty: true, analyses: ["ndvi"] },
      });
      navigate(`/process/${job.job_id}`);
    } catch (e) { setError(e as PixelForgeError); setStarting(false); }
  }

  const gsdMismatch = error?.code === "INVALID_RASTER_GSD";

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <div className="label-mono mb-2">Upload</div>
      <h1 className="mb-2 text-3xl font-semibold tracking-tight">Process your own imagery</h1>
      <p className="mb-8 text-sm leading-relaxed text-dim">
        A georeferenced Sentinel-2 product (GeoTIFF or JPEG2000) containing at least the
        blue, green, red and near-infrared bands at 10 m.
      </p>

      {!scene && (
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault(); setDragging(false);
            const f = e.dataTransfer.files[0];
            if (f) handleFile(f);
          }}
          onClick={() => inputRef.current?.click()}
          className={`cursor-pointer rounded-xl border-2 border-dashed p-14 text-center transition-colors
            ${dragging ? "border-signal bg-signal/[0.06]" : "border-line-bright hover:border-signal/50"}`}>
          <input ref={inputRef} type="file" accept=".tif,.tiff,.jp2" className="hidden"
                 onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])} />
          <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="1.4" className="mx-auto mb-4 text-muted">
            <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M3 15v3a3 3 0 0 0 3 3h12a3 3 0 0 0 3-3v-3" strokeLinecap="round" />
          </svg>
          <p className="text-sm text-bright">Drop a file here, or click to browse</p>
          <p className="mt-2 font-mono text-2xs text-muted">.tif · .tiff · .jp2 · up to 200 MB</p>
        </div>
      )}

      {upload.isPending && (
        <div className="mt-6">
          <div className="mb-2 flex justify-between font-mono text-2xs text-muted">
            <span>uploading</span><span>{(progress * 100).toFixed(0)}%</span>
          </div>
          <div className="h-1 overflow-hidden rounded bg-raised">
            <div className="h-full bg-signal transition-[width] duration-200"
                 style={{ width: `${progress * 100}%` }} />
          </div>
        </div>
      )}

      {error && (
        <div className="mt-6">
          <ErrorBox code={error.code} message={error.message} details={error.details} />
          {gsdMismatch && (
            <button onClick={() => inputRef.current?.files?.[0] &&
                                   handleFile(inputRef.current.files[0], true)}
                    className="btn-ghost mt-3 text-xs">
              Process anyway (metrics will not be comparable)
            </button>
          )}
        </div>
      )}

      {scene && (
        <div className="mt-6 space-y-5">
          <Panel title="File accepted" subtitle={scene.original_filename ?? undefined}>
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Meta label="CRS" value={scene.crs ?? "—"} />
              <Meta label="Resolution" value={scene.gsd_m ? `${scene.gsd_m} m` : "—"} />
              <Meta label="Size" value={formatBytes(scene.size_bytes)} />
              <Meta label="Bands" value={String(Object.keys(scene.band_mapping).length)} />
            </dl>

            {!!scene.validation?.warnings?.length && (
              <div className="mt-4 space-y-1.5">
                {scene.validation.warnings.map((w: string, i: number) => (
                  <p key={i} className="border-l-2 border-warn/50 pl-3 text-xs leading-relaxed text-warn">
                    {w}
                  </p>
                ))}
              </div>
            )}

            {!scene.band_mapping_confirmed && (
              <BandConfirm scene={scene} onConfirmed={setScene} />
            )}
          </Panel>

          <Panel title="Models">
            <p className="mb-2 text-2xs leading-relaxed text-muted">
              Select as many models as you like — each one runs on this file, and every
              result lands together in the Compare tab afterwards.
            </p>
            <div className="space-y-1.5">
              {(modelData?.models ?? []).map((m) => {
                const usable = m.status === "available";
                const on = chosen.includes(m.key);
                return (
                  <button key={m.key} disabled={!usable}
                    onClick={() => setModels((p) =>
                      p.includes(m.key) ? p.filter((k) => k !== m.key) : [...p, m.key])}
                    className={`flex w-full items-center justify-between gap-3 rounded-md border p-3 text-left transition-colors
                      ${!usable ? "cursor-not-allowed border-line opacity-55"
                        : on ? "border-signal bg-signal/10" : "border-line hover:border-line-bright"}`}>
                    <span className="flex items-center gap-2 text-sm text-bright">
                      <span
                        className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border
                          ${!usable ? "border-line-bright"
                            : on ? "border-signal bg-signal text-void" : "border-line-bright"}`}
                        aria-hidden
                      >
                        {on && usable ? "✓" : ""}
                      </span>
                      {m.display_name}
                    </span>
                    {usable ? <Badge tone={m.is_baseline ? "neutral" : "signal"}>
                                {m.is_baseline ? "baseline" : `${m.output_gsd_m} m`}</Badge>
                            : <Badge tone="neutral" className="lowercase">
                                {(m.unavailable_reason ?? "").replace(/_/g, " ").toLowerCase()}</Badge>}
                  </button>
                );
              })}
            </div>
            <p className="mt-3 text-2xs leading-relaxed text-muted">
              {chosen.length > 1
                ? `${chosen.length} models selected — they'll process together and compare side by side.`
                : "Select two or more models to obtain an uncertainty map from their disagreement."}
            </p>
          </Panel>

          <div className="flex gap-3">
            <button onClick={start}
                    disabled={starting || scene.status === "needs_confirmation"}
                    className="btn-primary flex-1">
              {starting ? "Starting…" : "Start processing"}
            </button>
            <button onClick={() => { setScene(null); setError(null); }} className="btn-ghost">
              Choose another file
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function BandConfirm({ scene, onConfirmed }: {
  scene: SceneSummary; onConfirmed: (s: SceneSummary) => void;
}) {
  const [mapping, setMapping] = useState(scene.band_mapping);
  const [saving, setSaving] = useState(false);
  const OPTIONS = ["B02", "B03", "B04", "B08", "B8A", "SCL"];

  return (
    <div className="mt-4 rounded-md border border-warn/35 bg-warn/[0.06] p-4">
      <p className="text-xs leading-relaxed text-warn">
        Band names were not found in this file's metadata. The mapping below was inferred
        from band order. Confirm it — processing with the wrong band order produces a
        result that looks plausible but is scientifically wrong.
      </p>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Object.entries(mapping).map(([idx, band]) => (
          <label key={idx} className="block">
            <span className="label-mono mb-1 block">Band {idx}</span>
            <select value={band} className="input"
                    onChange={(e) => setMapping({ ...mapping, [idx]: e.target.value })}>
              {OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>
        ))}
      </div>
      <button disabled={saving}
        onClick={async () => {
          setSaving(true);
          try { onConfirmed(await api.confirmBands(scene.scene_id!, mapping)); }
          finally { setSaving(false); }
        }}
        className="btn-ghost mt-4 text-xs">
        {saving ? "Confirming…" : "Confirm band mapping"}
      </button>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="label-mono">{label}</dt>
      <dd className="mt-1 font-mono text-sm text-bright">{value}</dd>
    </div>
  );
}

/* ---------------------------------------------------------------- progress */

const STAGE_LABELS: Record<string, string> = {
  QUEUED: "Queued",
  DOWNLOADING: "Acquiring imagery",
  PREPROCESSING: "Preparing bands",
  SCENE_ANALYSIS: "Characterising the scene",
  MODEL_SELECTION: "Selecting model",
  INFERENCE: "Super-resolution inference",
  QUALITY_ASSESSMENT: "Quality assessment",
  UNCERTAINTY: "Uncertainty estimation",
  POST_PROCESSING: "Post-processing",
  ANALYSIS: "Geospatial analysis",
};

function JobProgress({ jobId }: { jobId: string }) {
  const navigate = useNavigate();
  const { data: job, error } = useJobStatus(jobId);

  useEffect(() => {
    if (job?.status === "COMPLETED") {
      const t = setTimeout(() => navigate(`/results/${jobId}`), 900);
      return () => clearTimeout(t);
    }
  }, [job?.status, jobId, navigate]);

  if (error) {
    const e = error as PixelForgeError;
    return <div className="mx-auto max-w-2xl px-6 py-16">
      <ErrorBox code={e.code} message={e.message} details={e.details} />
    </div>;
  }
  if (!job) return <div className="mx-auto max-w-2xl px-6 py-16 text-sm text-muted">Loading…</div>;

  const failed = job.status === "FAILED";

  return (
    <div className="mx-auto max-w-2xl px-6 py-12">
      <div className="label-mono mb-2">Job {jobId}</div>
      <h1 className="mb-1 text-2xl font-semibold tracking-tight">
        {failed ? "Processing failed"
          : job.status === "COMPLETED" ? "Complete" : "Processing"}
      </h1>
      <p className="mb-8 font-mono text-xs text-muted">
        {(job.progress * 100).toFixed(0)}% · {job.stage_detail ?? job.current_stage}
      </p>

      <div className="mb-8 h-1 overflow-hidden rounded bg-raised">
        <div className={`h-full transition-[width] duration-500 ${failed ? "bg-danger" : "bg-signal"}`}
             style={{ width: `${job.progress * 100}%` }} />
      </div>

      {job.error && (
        <div className="mb-8">
          <ErrorBox code={job.error.code} message={job.error.message} />
          {job.error.retryable && (
            <p className="mt-2 text-xs text-muted">
              This looks temporary. Starting the job again may succeed.
            </p>
          )}
        </div>
      )}

      <ol className="space-y-px overflow-hidden rounded-lg border border-line">
        {job.stages.map((s) => <StageRow key={s.name} stage={s} />)}
      </ol>

      {job.terrain && (
        <div className="mt-6 rounded-lg border border-line bg-panel p-4">
          <div className="label-mono mb-2">Scene characterisation</div>
          <p className="text-sm text-bright">
            {job.terrain.class}{" "}
            <span className="font-mono text-xs text-muted">
              ({(job.terrain.confidence * 100).toFixed(0)}% of the area)
            </span>
          </p>
          <p className="mt-1.5 text-xs leading-relaxed text-muted">
            Derived from spectral index thresholds, not a trained classifier.
          </p>
        </div>
      )}

      <div className="mt-8 flex gap-3">
        {job.status === "COMPLETED" && (
          <button onClick={() => navigate(`/results/${jobId}`)} className="btn-primary">
            View results
          </button>
        )}
        {!["COMPLETED", "FAILED", "CANCELLED"].includes(job.status) && (
          <button onClick={() => api.cancelJob(jobId)} className="btn-ghost text-xs">
            Cancel
          </button>
        )}
        {failed && (
          <button onClick={() => navigate("/explore")} className="btn-ghost">
            Start over
          </button>
        )}
      </div>
    </div>
  );
}

function StageRow({ stage }: { stage: Stage }) {
  const icon = {
    done: <span className="text-ok">✓</span>,
    skipped: <span className="text-muted">–</span>,
    failed: <span className="text-danger">✕</span>,
    running: <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-signal border-t-transparent" />,
    pending: <span className="text-line-bright">○</span>,
  }[stage.status];

  return (
    <li className={`flex items-start gap-3 bg-panel px-4 py-3
                    ${stage.status === "pending" ? "opacity-45" : ""}`}>
      <span className="mt-0.5 w-4 shrink-0 text-center font-mono text-xs">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <span className={`text-sm ${stage.status === "running" ? "text-signal" : "text-bright"}`}>
            {STAGE_LABELS[stage.name] ?? stage.name}
          </span>
          {stage.duration_s != null && (
            <span className="font-mono text-2xs text-muted">{formatDuration(stage.duration_s)}</span>
          )}
        </div>
        {/* A skipped stage always explains itself — never a silent gap. */}
        {stage.status === "skipped" && stage.reason_text && (
          <p className="mt-1 text-xs leading-relaxed text-muted">{stage.reason_text}</p>
        )}
        {stage.status === "running" && stage.detail && (
          <p className="mt-1 font-mono text-2xs text-muted">{stage.detail}</p>
        )}
        {stage.status === "done" && stage.detail && (
          <p className="mt-1 text-xs leading-relaxed text-muted">{stage.detail}</p>
        )}
      </div>
    </li>
  );
}
