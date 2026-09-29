import { Badge, Stat, Unavailable } from "@/components/common/Primitives";
import { formatMetric } from "@/lib/utils";
import type { RunResult } from "@/types/api";

const NO_REF_LABELS: Record<string, string> = {
  lr_consistency_psnr: "Consistency with observation",
  lr_consistency_ssim: "Structural agreement",
  spectral_angle_vs_lr: "Spectral angle vs observation",
  edge_energy_ratio: "Detail added vs interpolation",
};

const FULL_REF_LABELS: Record<string, string> = {
  psnr: "PSNR", ssim: "SSIM", sam: "SAM", rmse: "RMSE", lpips: "LPIPS",
};

export function MetricsPanel({ runs }: { runs: RunResult[] }) {
  const completed = runs.filter((r) => r.status === "completed");
  if (!completed.length) {
    return <Unavailable title="No completed runs" reason="No model produced output for this job." />;
  }
  const noRefKeys = Object.keys(completed[0].metrics.no_reference);
  const unavailableReason = completed[0].metrics.full_reference_unavailable_reason;

  return (
    <div className="space-y-6">
      {/* --- full reference ------------------------------------------------ */}
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-medium text-bright">Full-reference metrics</h3>
          <Badge tone={unavailableReason ? "neutral" : "ok"}>
            {unavailableReason ? "not computable" : "computed"}
          </Badge>
        </div>

        {unavailableReason ? (
          /* 🚨 The honest empty state. Never a zero, never a number from a slide. */
          <Unavailable
            title="PSNR, SSIM, SAM and RMSE cannot be computed for this area"
            reason={unavailableReason}
            hint={
              <span>
                These metrics compare the output against a known-correct high-resolution
                image. No such image exists for an arbitrary area of interest — if one did,
                super-resolution would be unnecessary. Use the no-reference metrics below,
                which measure fidelity against the original observation.
              </span>
            }
          />
        ) : (
          <MetricTable runs={completed} labels={FULL_REF_LABELS} group="full_reference" />
        )}
      </div>

      {/* --- no reference -------------------------------------------------- */}
      <div>
        <div className="mb-1 flex items-center justify-between">
          <h3 className="text-sm font-medium text-bright">No-reference metrics</h3>
          <Badge tone="ok">computed</Badge>
        </div>
        <p className="mb-3 text-xs leading-relaxed text-muted">
          These need no ground truth: the output is downsampled back to 10 m and compared
          with what the satellite actually recorded.
        </p>
        <MetricTable runs={completed} labels={NO_REF_LABELS} group="no_reference"
                     keys={noRefKeys} />
      </div>

      {/* --- explanations --------------------------------------------------- */}
      <details className="rounded-lg border border-line bg-panel">
        <summary className="cursor-pointer px-4 py-3 text-sm text-dim hover:text-bright">
          What do these metrics mean?
        </summary>
        <dl className="space-y-3 border-t border-line px-4 py-3">
          {completed.flatMap((r) =>
            Object.entries(r.metrics.no_reference)).slice(0, 4).map(([k, m]) => (
            <div key={k}>
              <dt className="font-mono text-2xs uppercase tracking-wider text-signal">
                {NO_REF_LABELS[k] ?? k}
              </dt>
              <dd className="mt-1 text-xs leading-relaxed text-muted">{m.explanation}</dd>
            </div>
          ))}
        </dl>
      </details>
    </div>
  );
}

function MetricTable({ runs, labels, group, keys }: {
  runs: RunResult[]; labels: Record<string, string>;
  group: "full_reference" | "no_reference"; keys?: string[];
}) {
  const rowKeys = keys ?? Object.keys(labels);
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full text-sm">
        <thead className="bg-raised">
          <tr>
            <th className="px-3 py-2 text-left font-medium text-dim">Metric</th>
            {runs.map((r) => (
              <th key={r.run_id} className="px-3 py-2 text-right font-medium text-dim">
                <div className="text-bright">{r.model_display_name}</div>
                {r.is_baseline && <div className="font-mono text-2xs text-muted">baseline</div>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rowKeys.map((key) => (
            <tr key={key} className="bg-panel/50">
              <td className="px-3 py-2.5 text-dim">{labels[key] ?? key}</td>
              {runs.map((r) => {
                const m = group === "full_reference"
                  ? r.metrics.full_reference?.[key]
                  : r.metrics.no_reference[key];
                const delta = r.delta_vs_baseline?.[key];
                return (
                  <td key={r.run_id} className="px-3 py-2.5 text-right">
                    <span className="font-mono tabular-nums text-bright">
                      {formatMetric(m?.value)}
                    </span>
                    {m?.unit && <span className="ml-1 text-2xs text-muted">{m.unit}</span>}
                    {delta && (
                      <div className={`font-mono text-2xs ${delta.improved ? "text-ok" : "text-warn"}`}>
                        {delta.delta > 0 ? "+" : ""}{delta.delta.toFixed(2)} vs baseline
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RunSummary({ run }: { run: RunResult }) {
  const gi = run.geospatial_integrity;
  return (
    <div className="grid grid-cols-2 gap-5 sm:grid-cols-4">
      <Stat label="Output resolution" value={run.output.gsd_m ?? "—"} unit="m"
            tone={gi?.meets_ps_requirement ? "good" : "warn"}
            hint={gi?.meets_ps_requirement ? "meets the <4 m requirement" : undefined} />
      <Stat label="Scale factor" value={`×${run.output.scale_factor ?? "—"}`} />
      <Stat label="Runtime" value={run.performance.runtime_s ?? "—"} unit="s"
            hint={run.performance.device ?? undefined} />
      <Stat label="Coordinate system" value={run.output.crs ?? "—"}
            tone={gi?.crs_preserved ? "good" : "warn"}
            hint={gi?.crs_preserved ? "preserved through inference" : undefined} />
    </div>
  );
}
