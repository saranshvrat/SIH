import { Badge, ProvenanceNotice, Stat, Unavailable } from "@/components/common/Primitives";
import type { ArtifactRef, UncertaintyInfo } from "@/types/api";

export function UncertaintyPanel({ info, artifacts }: {
  info: UncertaintyInfo; artifacts: ArtifactRef[];
}) {
  if (!info.available) {
    return (
      <Unavailable
        title="No uncertainty estimate for this job"
        reason={info.reason_text ?? info.reason}
        hint={info.reason === "NEEDS_MULTIPLE_RUNS" ? (
          <span>
            PixelForge derives uncertainty from genuine disagreement between independent
            model outputs. With a single model there is nothing to disagree with, so no map
            is produced — rather than a plausible-looking one invented from the image itself.
            Run the job again with two or more models selected.
          </span>
        ) : undefined}
      />
    );
  }

  const preview = artifacts.find((a) => a.kind === "uncertainty_preview");
  const legend = preview?.meta?.legend as { value: number; label: string }[] | undefined;

  return (
    <div className="space-y-5">
      {/* 🚨 Calibration status is shown FIRST, never as a footnote. */}
      <ProvenanceNotice text={
        info.calibrated
          ? "This uncertainty map has been calibrated against ground truth."
          : "These values are an uncalibrated, relative indicator of where the compared models disagree. They are NOT probabilities and have not been validated against ground truth."} />

      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="signal">{info.method?.replace(/_/g, " ")}</Badge>
        <Badge tone={info.calibrated ? "ok" : "warn"}>
          {info.calibrated ? "calibrated" : "uncalibrated"}
        </Badge>
        {info.source_run_ids && (
          <Badge tone="neutral">{info.source_run_ids.length} outputs compared</Badge>
        )}
      </div>

      {preview && (
        <figure className="overflow-hidden rounded-lg border border-line">
          <img src={preview.url} alt="Model disagreement map"
               className="w-full bg-void object-contain" />
          {legend && (
            <figcaption className="flex items-center justify-between gap-3 border-t border-line bg-panel px-3 py-2">
              <span className="font-mono text-2xs text-muted">{legend[0]?.label}</span>
              <div className="h-2 flex-1 rounded"
                   style={{ background: "linear-gradient(90deg,#000004,#51127c,#b73779,#fc8961,#fcfdbf)" }} />
              <span className="font-mono text-2xs text-muted">{legend[1]?.label}</span>
            </figcaption>
          )}
        </figure>
      )}

      {info.stats && (
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-4">
          <Stat label="Mean disagreement" value={info.stats.mean.toFixed(5)} />
          <Stat label="Median" value={info.stats.p50.toFixed(5)} />
          <Stat label="95th percentile" value={info.stats.p95.toFixed(5)} />
          <Stat label="Agreement score"
                value={info.confidence_score?.toFixed(3) ?? "—"} tone="muted" />
        </div>
      )}

      {info.confidence_definition && (
        <div className="rounded-lg border border-line bg-panel p-4">
          <div className="label-mono mb-2">How the agreement score is defined</div>
          <p className="text-xs leading-relaxed text-muted">{info.confidence_definition}</p>
        </div>
      )}

      {!!info.high_uncertainty_regions?.length && (
        <div>
          <h4 className="mb-2 text-sm font-medium text-bright">Least certain areas</h4>
          <ul className="space-y-1.5">
            {info.high_uncertainty_regions.slice(0, 5).map((r, i) => (
              <li key={i} className="flex items-center justify-between rounded border border-line bg-panel px-3 py-2 text-xs">
                <span className="text-dim">Region {i + 1}</span>
                <span className="font-mono text-muted">
                  {r.area_km2.toFixed(4)} km² · disagreement {r.mean_uncertainty.toFixed(5)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
