import { api, PixelForgeError } from "@/api/client";
import { useAoiPreview, useCreateJob, useModels } from "@/api/queries/hooks";
import { Badge, ErrorBox } from "@/components/common/Primitives";
import { MapCanvas, squareFromPoint } from "@/components/map/MapCanvas";
import type { GeoJSONPolygon, SceneSummary } from "@/types/api";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

const PRESETS = [
  { name: "Durgapur, WB", center: [87.32, 23.52] as [number, number] },
  { name: "Kolkata", center: [88.36, 22.57] as [number, number] },
  { name: "Delhi NCR", center: [77.21, 28.61] as [number, number] },
  { name: "Mumbai", center: [72.87, 19.08] as [number, number] },
];

// 128 x 128 px @ Sentinel-2's native 10 m resolution -> 512 x 512 px @ 2.5 m once
// super-resolved. Small enough to fit every model's single-tile limit (see
// model_catalog.yaml), so a point click never needs to be tiled.
const SAMPLE_SIZE_M = 1280;

function centerOfAoi(aoi: GeoJSONPolygon): [number, number] {
  const ring = aoi.coordinates[0];
  const lons = ring.map((c) => c[0]);
  const lats = ring.map((c) => c[1]);
  return [
    (Math.min(...lons) + Math.max(...lons)) / 2,
    (Math.min(...lats) + Math.max(...lats)) / 2,
  ];
}

const today = () => new Date().toISOString().slice(0, 10);
const monthsAgo = (n: number) => {
  const d = new Date(); d.setMonth(d.getMonth() - n);
  return d.toISOString().slice(0, 10);
};

export function Explore() {
  const navigate = useNavigate();
  const { data: modelData } = useModels();

  const [aoi, setAoi] = useState<GeoJSONPolygon | null>(null);
  const [center, setCenter] = useState<[number, number]>(PRESETS[0].center);
  const [latInput, setLatInput] = useState("");
  const [lonInput, setLonInput] = useState("");
  const [dateFrom, setDateFrom] = useState(monthsAgo(3));
  const [dateTo, setDateTo] = useState(today());
  const [maxCloud, setMaxCloud] = useState(20);
  const [scenes, setScenes] = useState<SceneSummary[] | null>(null);
  const [activeScene, setActiveScene] = useState<string | null>(null);
  const [searchError, setSearchError] = useState<PixelForgeError | null>(null);
  const [searching, setSearching] = useState(false);


  const availableModels = useMemo(
    () => (modelData?.models ?? []).filter((m) => m.status === "available"),
    [modelData]);
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const effectiveModels = selectedModels.length
    ? selectedModels
    : availableModels.slice(0, 1).map((m) => m.key);

  const preview = useAoiPreview(aoi, effectiveModels[0]);
  const createJob = useCreateJob();
  const aoiError = preview.error as PixelForgeError | undefined;

  const handleAoi = useCallback((g: GeoJSONPolygon | null) => {
    setAoi(g); setScenes(null); setActiveScene(null); setSearchError(null);
  }, []);

  // Keep the coordinate boxes in sync with wherever the AOI actually came from — a map
  // click, a preset, or a previous manual entry — so what's displayed is always what will
  // be sent for processing.
  useEffect(() => {
    if (!aoi) return;
    const [lon, lat] = centerOfAoi(aoi);
    setLatInput(lat.toFixed(5));
    setLonInput(lon.toFixed(5));
  }, [aoi]);

  function goToPoint() {
    const lat = parseFloat(latInput);
    const lon = parseFloat(lonInput);
    if (Number.isNaN(lat) || Number.isNaN(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) return;
    setCenter([lon, lat]);
    handleAoi(squareFromPoint(lon, lat, SAMPLE_SIZE_M));
  }

  async function search() {
    if (!aoi) return;
    setSearching(true); setSearchError(null);
    try {
      const res = await api.searchScenes({
        aoi, date_from: dateFrom, date_to: dateTo, max_cloud_cover: maxCloud, limit: 20,
      });
      setScenes(res.scenes);
      if (res.scenes[0]?.scene_id) setActiveScene(res.scenes[0].scene_id);
    } catch (e) {
      setSearchError(e as PixelForgeError);
      setScenes(null);
    } finally { setSearching(false); }
  }

  async function process() {
    if (!activeScene || !aoi) return;
    const created = await api.createAoi(aoi, "Explorer selection", effectiveModels[0]);
    const job = await createJob.mutateAsync({
      scene_id: activeScene, aoi_id: created.aoi_id, models: effectiveModels,
      options: { compute_metrics: true, compute_uncertainty: true, analyses: ["ndvi"] },
      idempotency_key: `${activeScene}:${created.aoi_id}:${effectiveModels.join(",")}`,
    });
    navigate(`/process/${job.job_id}`);
  }

  return (
    <div className="flex flex-col lg:grid lg:h-[calc(100vh-6.25rem)] lg:grid-cols-[1fr_400px] lg:grid-rows-1">
      <MapCanvas
        aoi={aoi} onAoiChange={handleAoi} drawMode="point" sampleSizeM={SAMPLE_SIZE_M}
        center={center}
        footprints={(scenes ?? []).map((s) => ({
          id: s.scene_id ?? "", geometry: s.footprint, active: s.scene_id === activeScene }))}
        className="h-[50vh] shrink-0 lg:h-full"
      />

      <aside className="flex flex-col overflow-y-auto border-t border-line bg-abyss lg:h-full lg:min-h-0 lg:border-l lg:border-t-0">
        {/* 1. area */}
        <Section step="01" title="Area of interest">
          <p className="mb-3 text-xs leading-relaxed text-muted">
            Click anywhere on the map to pick a point, or type coordinates directly. Either
            way, a {(SAMPLE_SIZE_M / 1000).toFixed(2)} km square centred on that point is
            sampled at Sentinel-2's native 10 m resolution and sent for processing.
          </p>

          <div className="mb-3 grid grid-cols-2 gap-2">
            <Field label="Latitude">
              <input type="number" step="any" value={latInput}
                     onChange={(e) => setLatInput(e.target.value)}
                     onKeyDown={(e) => e.key === "Enter" && goToPoint()}
                     placeholder="23.52000" className="input font-mono" />
            </Field>
            <Field label="Longitude">
              <input type="number" step="any" value={lonInput}
                     onChange={(e) => setLonInput(e.target.value)}
                     onKeyDown={(e) => e.key === "Enter" && goToPoint()}
                     placeholder="87.32000" className="input font-mono" />
            </Field>
          </div>

          <div className="mb-3 flex gap-2">
            <button onClick={goToPoint} disabled={!latInput || !lonInput}
                    className="btn-ghost flex-1 text-xs">
              Use this point
            </button>
            {aoi && (
              <button onClick={() => handleAoi(null)}
                className="rounded-md border border-line-bright px-3 py-2 text-xs text-muted hover:text-danger">
                Clear
              </button>
            )}
          </div>

          <div className="mb-3 flex flex-wrap gap-1.5">
            {PRESETS.map((p) => (
              <button key={p.name} onClick={() => setCenter([...p.center] as [number, number])}
                className="rounded border border-line px-2 py-1 text-2xs text-muted transition-colors hover:border-signal/50 hover:text-bright">
                {p.name}
              </button>
            ))}
          </div>

          {aoi && (
            <p className="mb-3 font-mono text-2xs text-dim">
              Center: {parseFloat(latInput).toFixed(5)}°, {parseFloat(lonInput).toFixed(5)}°
            </p>
          )}

          {aoi && aoiError && (
            <ErrorBox code={aoiError.code} message={aoiError.message} details={aoiError.details} />
          )}

          {aoi && preview.data && (
            <div className="space-y-2.5 rounded-md border border-line bg-panel p-3">
              <Row label="Area" value={`${preview.data.area_km2.toFixed(2)} km²`} />
              <Row label="Input" value={`${preview.data.estimated_lr_px.width} × ${preview.data.estimated_lr_px.height} px @ 10 m`} />
              <Row label="Output" value={`${preview.data.estimated_sr_px.width} × ${preview.data.estimated_sr_px.height} px`} />
              <Row label="Est. size" value={`${preview.data.estimated_output_mb} MB`} />
              <Row label="Projection" value={preview.data.utm_crs} />
              {preview.data.warnings.map((w, i) => (
                <p key={i} className="border-l-2 border-warn/50 pl-2 text-2xs leading-relaxed text-warn">{w}</p>
              ))}
            </div>
          )}
        </Section>

        {/* 2. scene */}
        <Section step="02" title="Sentinel-2 scene"
                 disabled={!aoi || !!aoiError}>
          <div className="mb-3 grid grid-cols-2 gap-2">
            <Field label="From">
              <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
                     className="input" />
            </Field>
            <Field label="To">
              <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)}
                     className="input" />
            </Field>
          </div>
          <Field label={`Max cloud cover · ${maxCloud}%`}>
            <input type="range" min={0} max={100} value={maxCloud}
                   onChange={(e) => setMaxCloud(+e.target.value)}
                   className="w-full accent-signal" />
          </Field>
          <button onClick={search} disabled={!aoi || searching || !!aoiError}
                  className="btn-ghost mt-3 w-full">
            {searching ? "Searching…" : "Search scenes"}
          </button>

          {searchError && (
            <div className="mt-3">
              <ErrorBox code={searchError.code} message={searchError.message}
                        details={searchError.details} onRetry={search} />
            </div>
          )}

          {scenes && scenes.length === 0 && (
            <p className="mt-3 text-xs text-muted">
              No scenes matched. Widen the date range or raise the cloud limit.
            </p>
          )}

          {scenes && scenes.length > 0 && (
            <>
              <p className="mt-3 text-2xs leading-relaxed text-muted">
                Cloud cover is for the whole ~110 km tile, not your area.
              </p>
              <ul className="mt-2 space-y-1.5">
                {scenes.map((s) => (
                  <li key={s.scene_id}>
                    <button onClick={() => setActiveScene(s.scene_id)}
                      className={`w-full rounded-md border p-2.5 text-left transition-colors
                        ${activeScene === s.scene_id
                          ? "border-signal bg-signal/10" : "border-line hover:border-line-bright"}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-xs text-bright">
                          {s.sensed_at?.slice(0, 10) ?? "—"}
                        </span>
                        <Badge tone={(s.cloud_cover ?? 0) < 10 ? "ok" : "warn"}>
                          {s.cloud_cover?.toFixed(0) ?? "?"}% cloud
                        </Badge>
                      </div>
                      <div className="mt-1 truncate font-mono text-2xs text-muted">
                        {s.tile_id ?? s.external_id}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Section>

        {/* 3. models */}
        <Section step="03" title="Models" disabled={!activeScene}>
          <p className="mb-2 text-2xs leading-relaxed text-muted">
            Select as many models as you like — each one runs on the same area, and every
            result lands together in the Compare tab afterwards.
          </p>
          <div className="space-y-1.5">
            {(modelData?.models ?? []).map((m) => {
              const on = effectiveModels.includes(m.key);
              const usable = m.status === "available";
              return (
                <button key={m.key} disabled={!usable}
                  onClick={() => setSelectedModels((prev) =>
                    prev.includes(m.key) ? prev.filter((k) => k !== m.key) : [...prev, m.key])}
                  className={`w-full rounded-md border p-2.5 text-left transition-colors
                    ${!usable ? "cursor-not-allowed border-line bg-void/40 opacity-55"
                      : on ? "border-signal bg-signal/10" : "border-line hover:border-line-bright"}`}>
                  <div className="flex items-center justify-between gap-2">
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
                    {usable
                      ? <Badge tone={m.is_baseline ? "neutral" : "signal"}>
                          {m.is_baseline ? "baseline" : `${m.output_gsd_m} m`}</Badge>
                      : <Badge tone="neutral" className="lowercase">
                          {(m.unavailable_reason ?? "").replace(/_/g, " ").toLowerCase()}</Badge>}
                  </div>
                  {!usable && (
                    <p className="mt-1 text-2xs leading-relaxed text-muted">
                      This model is listed for transparency but cannot run in this deployment.
                    </p>
                  )}
                </button>
              );
            })}
          </div>
          <p className="mt-3 text-2xs leading-relaxed text-muted">
            {effectiveModels.length > 1
              ? `${effectiveModels.length} models selected — they'll process in the same job and compare side by side.`
              : "Select two or more models to obtain an uncertainty map from their disagreement."}
          </p>
        </Section>

        <div className="mt-auto border-t border-line bg-panel p-4">
          {createJob.error && (
            <div className="mb-3">
              <ErrorBox code={(createJob.error as PixelForgeError).code}
                        message={(createJob.error as PixelForgeError).message}
                        details={(createJob.error as PixelForgeError).details} />
            </div>
          )}
          <button onClick={process}
                  disabled={!activeScene || !aoi || createJob.isPending || !!aoiError}
                  className="btn-primary w-full">
            {createJob.isPending ? "Starting…" : "Process area"}
          </button>
          <p className="mt-2 text-center text-2xs text-muted">
            Results are kept for this browser session only.
          </p>
        </div>
      </aside>
    </div>
  );
}

function Section({ step, title, children, disabled }: {
  step: string; title: string; children: React.ReactNode; disabled?: boolean;
}) {
  return (
    <section className={`border-b border-line p-4 transition-opacity ${disabled ? "opacity-45" : ""}`}>
      <div className="mb-3 flex items-center gap-2">
        <span className="font-mono text-2xs text-signal-dim">{step}</span>
        <h2 className="text-sm font-medium text-bright">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="text-muted">{label}</span>
      <span className="font-mono text-bright">{value}</span>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="label-mono mb-1 block">{label}</span>
      {children}
    </label>
  );
}
