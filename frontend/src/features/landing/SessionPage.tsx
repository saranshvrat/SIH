import { Link } from "react-router-dom";
import { useClearSession, useSession } from "@/api/queries/hooks";
import { Badge, Panel, Skeleton, Stat } from "@/components/common/Primitives";
import { formatBytes, timeUntil } from "@/lib/utils";
import { useState } from "react";

/**
 * The visible face of the ephemeral data model (ADR-001): exactly what PixelForge is
 * holding, for how long, and a button that destroys it.
 */
export function SessionPage() {
  const { data, isLoading } = useSession();
  const clear = useClearSession();
  const [confirming, setConfirming] = useState(false);

  if (isLoading) return <div className="mx-auto max-w-4xl px-6 py-10">
    <Skeleton className="h-40 w-full" /></div>;
  if (!data) return null;

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <div className="label-mono mb-2">This session</div>
      <h1 className="mb-2 text-3xl font-semibold tracking-tight">Your data</h1>
      <p className="mb-8 max-w-2xl text-sm leading-relaxed text-dim">
        PixelForge has no accounts. Everything below belongs to this browser session only
        and is deleted automatically — or immediately, if you ask.
      </p>

      <Panel title="Currently stored" className="mb-5">
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
          <Stat label="Jobs" value={data.counts.jobs} />
          <Stat label="Scenes" value={data.counts.scenes} />
          <Stat label="Files" value={data.counts.files} />
          <Stat label="Total size" value={formatBytes(data.bytes_stored)} />
        </div>
        <div className="mt-5 grid gap-3 border-t border-line pt-5 text-xs sm:grid-cols-2">
          <Fact label="What is stored" value={data.privacy.what_is_stored} />
          <Fact label="Where" value={data.privacy.where} />
          <Fact label="How long" value={data.privacy.how_long} />
          <Fact label="Session expires" value={`in ${timeUntil(data.expires_at)}`} />
        </div>
      </Panel>

      <Panel title="Jobs in this session" className="mb-5">
        {data.jobs.length === 0 ? (
          <p className="text-sm text-muted">
            Nothing yet.{" "}
            <Link to="/explore" className="text-signal underline underline-offset-2">
              Pick an area to process →
            </Link>
          </p>
        ) : (
          <ul className="divide-y divide-line">
            {data.jobs.map((j) => (
              <li key={j.job_id} className="flex items-center justify-between gap-4 py-3">
                <div className="min-w-0">
                  <Link to={j.status === "COMPLETED" ? `/results/${j.job_id}` : `/process/${j.job_id}`}
                        className="truncate font-mono text-xs text-bright hover:text-signal">
                    {j.job_id}
                  </Link>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-2xs text-muted">
                    <span>{j.models?.join(", ") || "—"}</span>
                    {j.terrain && <span>· {j.terrain}</span>}
                    <span>· expires in {timeUntil(j.expires_at)}</span>
                  </div>
                </div>
                <Badge tone={j.status === "COMPLETED" ? "ok"
                           : j.status === "FAILED" ? "danger" : "signal"}>
                  {j.status.toLowerCase()}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Delete everything now">
        <p className="mb-4 text-sm leading-relaxed text-dim">
          Permanently removes every database record and every stored file belonging to this
          session. This cannot be undone.
        </p>
        {!confirming ? (
          <button onClick={() => setConfirming(true)}
                  className="btn border border-danger/50 text-danger hover:bg-danger/10">
            Clear my data
          </button>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <button onClick={() => { clear.mutate(); setConfirming(false); }}
                    disabled={clear.isPending}
                    className="btn bg-danger text-void hover:bg-danger/90">
              {clear.isPending ? "Deleting…" : "Yes, delete everything"}
            </button>
            <button onClick={() => setConfirming(false)} className="btn-ghost">Cancel</button>
          </div>
        )}
        {clear.isSuccess && (
          <p className="mt-3 text-xs text-ok">
            Deleted {clear.data.rows_deleted} record(s) and {clear.data.objects_deleted} file(s).
          </p>
        )}
      </Panel>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="label-mono mb-1">{label}</div>
      <p className="leading-relaxed text-dim">{value}</p>
    </div>
  );
}
