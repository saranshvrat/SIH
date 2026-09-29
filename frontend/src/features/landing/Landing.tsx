import { Link } from "react-router-dom";
import { Hero } from "@/components/hero/Hero";
import { useModels } from "@/api/queries/hooks";
import { Badge } from "@/components/common/Primitives";

const PIPELINE = [
  {
    n: "01",
    t: "Acquire",
    d: "Click a point (or type coordinates) and pull the least-cloudy Sentinel-2 scene from Microsoft Planetary Computer's public archive, or upload your own georeferenced product.",
  },
  {
    n: "02",
    t: "Prepare",
    d: "Windowed read over the AOI, band ordering enforced, cloud fraction measured from the scene classification layer.",
  },
  {
    n: "03",
    t: "Characterise",
    d: "Spectral indices describe the land cover, which selects an appropriate model — and shows you why it chose it.",
  },
  {
    n: "04",
    t: "Reconstruct",
    d: "Tiled inference with overlap blending. Coordinate system, transform and band identity are verified afterwards, not assumed.",
  },
  {
    n: "05",
    t: "Assess",
    d: "Consistency against the original observation, spectral angle, and full-reference metrics wherever ground truth genuinely exists.",
  },
  {
    n: "06",
    t: "Interpret",
    d: "Vegetation and water indices computed on both the reconstruction and the original, so the difference is visible.",
  },
];

const APPLICATIONS = [
  {
    t: "Urban planning",
    d: "Resolve individual structures and road networks that blur together at 10 m.",
  },
  {
    t: "Agriculture",
    d: "Field-boundary delineation and crop vigour at sub-plot scale.",
  },
  {
    t: "Disaster response",
    d: "Rapid damage assessment where high-resolution tasking is not available yet.",
  },
  {
    t: "Environmental monitoring",
    d: "Track water bodies, deforestation fronts and coastal change over time.",
  },
];

export function Landing() {
  const { data } = useModels();
  const models = data?.models ?? [];

  return (
    <main>
      <Hero />

      {/* --- premise ------------------------------------------------------ */}
      <section className="mx-auto max-w-7xl px-6 py-24 md:px-10 md:py-32">
        <div className="grid gap-12 md:grid-cols-12">
          <div className="md:col-span-5">
            <div className="label-mono mb-4">The problem</div>
            <h2 className="text-balance text-3xl font-semibold leading-tight tracking-tight md:text-4xl">
              High-resolution imagery is expensive. Medium-resolution imagery is
              everywhere.
            </h2>
          </div>
          <div className="space-y-5 text-pretty leading-relaxed text-dim md:col-span-6 md:col-start-7">
            <p>
              Sentinel-2 revisits every point on Earth every five days, free of
              charge, at 10 metres per pixel. At that scale a narrow road, a
              single dwelling or a field boundary occupies a fraction of a pixel
              and simply is not there.
            </p>
            <p>
              PixelForge reconstructs that missing detail with deep learning,
              targeting better than 4 metres — while keeping the output a real
              geospatial product: correct coordinate system, correct footprint,
              spectrally consistent with what the satellite actually measured.
            </p>
            <p className="text-bright">
              And because reconstruction is inference rather than observation,
              PixelForge treats{" "}
              <em className="not-italic text-signal">
                saying what it does not know
              </em>{" "}
              as a first-class feature.
            </p>
          </div>
        </div>
      </section>

      {/* --- pipeline ----------------------------------------------------- */}
      <section id="pipeline" className="border-y border-line bg-abyss/50">
        <div className="mx-auto max-w-7xl px-6 py-24 md:px-10">
          <div className="label-mono mb-3">Pipeline</div>
          <h2 className="mb-14 max-w-2xl text-3xl font-semibold tracking-tight md:text-4xl">
            Every stage is visible while it runs.
          </h2>
          <div className="grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-2 lg:grid-cols-3">
            {PIPELINE.map((s) => (
              <div
                key={s.n}
                className="group bg-panel p-6 transition-colors hover:bg-raised"
              >
                <div className="mb-3 font-mono text-2xs text-signal-dim transition-colors group-hover:text-signal">
                  {s.n}
                </div>
                <h3 className="mb-2 font-medium text-bright">{s.t}</h3>
                <p className="text-sm leading-relaxed text-muted">{s.d}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* --- models ------------------------------------------------------- */}
      <section className="mx-auto max-w-7xl px-6 py-24 md:px-10">
        <div className="label-mono mb-3">Model comparison</div>
        <h2 className="mb-4 max-w-2xl text-3xl font-semibold tracking-tight md:text-4xl">
          Several models. One comparison you can trust.
        </h2>
        <p className="mb-10 max-w-2xl leading-relaxed text-dim">
          Different architectures reconstruct differently. PixelForge runs them
          on identical preprocessed input and renders them with an identical
          contrast stretch, so what you see is a difference in the models — not
          a difference in presentation.
        </p>

        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-raised">
              <tr className="text-left">
                <th className="px-4 py-3 font-medium text-dim">Model</th>
                <th className="hidden px-4 py-3 font-medium text-dim sm:table-cell">
                  Output
                </th>
                <th className="hidden px-4 py-3 font-medium text-dim md:table-cell">
                  Suited to
                </th>
                <th className="px-4 py-3 text-right font-medium text-dim">
                  Status
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {models.map((m) => (
                <tr key={m.key} className="bg-panel/60">
                  <td className="px-4 py-3.5">
                    <div className="font-medium text-bright">
                      {m.display_name}
                    </div>
                    <div className="mt-0.5 line-clamp-1 text-xs text-muted">
                      {m.description}
                    </div>
                  </td>
                  <td className="hidden px-4 py-3.5 font-mono text-xs text-dim sm:table-cell">
                    {m.status === "available"
                      ? `${m.output_gsd_m} m · ×${m.scale_factor}`
                      : "—"}
                  </td>
                  <td className="hidden px-4 py-3.5 text-xs text-muted md:table-cell">
                    {m.is_baseline
                      ? "reference baseline"
                      : m.best_for.length
                        ? m.best_for.join(", ")
                        : "—"}
                  </td>
                  <td className="px-4 py-3.5 text-right">
                    {m.status === "available" ? (
                      <Badge tone="ok">ready</Badge>
                    ) : (
                      <Badge tone="neutral" className="lowercase">
                        {(m.unavailable_reason ?? "unavailable")
                          .replace(/_/g, " ")
                          .toLowerCase()}
                      </Badge>
                    )}
                  </td>
                </tr>
              ))}
              {models.length === 0 && (
                <tr>
                  <td
                    colSpan={4}
                    className="px-4 py-8 text-center text-sm text-muted"
                  >
                    Connect the API to see the live model catalogue.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="mt-4 text-xs text-muted">
          This table is rendered from the live API. Models that cannot actually
          run are shown with the reason — never as a claim.
        </p>
      </section>

      {/* --- trust -------------------------------------------------------- */}
      <section id="trust" className="border-y border-line bg-abyss/50">
        <div className="mx-auto grid max-w-7xl gap-12 px-6 py-24 md:grid-cols-12 md:px-10">
          <div className="md:col-span-5">
            <div className="label-mono mb-3">Uncertainty</div>
            <h2 className="text-balance text-3xl font-semibold leading-tight tracking-tight md:text-4xl">
              A model that invents a building is worse than no model at all.
            </h2>
          </div>
          <div className="space-y-6 md:col-span-6 md:col-start-7">
            <p className="leading-relaxed text-dim">
              Super-resolution is generative. It produces detail that is
              plausible given training data, not detail the sensor recorded. For
              planning, disaster response or anything consequential, the
              distinction matters enormously.
            </p>
            <div className="space-y-3">
              {[
                [
                  "Provenance travels with the file",
                  "Every GeoTIFF carries the model, version and a usage disclaimer in its metadata tags.",
                ],
                [
                  "Disagreement is measured, not guessed",
                  "Where several models run, per-pixel disagreement marks the areas where reconstruction is ambiguous.",
                ],
                [
                  "Nothing is labelled a probability until it is one",
                  "Uncertainty is reported as uncalibrated until validated against ground truth. The interface says so every time.",
                ],
                [
                  "Metrics that cannot be computed are not shown",
                  "Without ground truth, PSNR and SSIM are impossible. PixelForge returns null and explains why, rather than a comfortable number.",
                ],
              ].map(([t, d]) => (
                <div key={t} className="border-l-2 border-signal/40 pl-4">
                  <div className="text-sm font-medium text-bright">{t}</div>
                  <p className="mt-1 text-sm leading-relaxed text-muted">{d}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* --- applications ------------------------------------------------- */}
      <section className="mx-auto max-w-7xl px-6 py-24 md:px-10">
        <div className="label-mono mb-3">Applications</div>
        <h2 className="mb-12 text-3xl font-semibold tracking-tight md:text-4xl">
          Where sub-4-metre detail changes the decision.
        </h2>
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {APPLICATIONS.map((a) => (
            <div key={a.t} className="panel p-5">
              <h3 className="mb-2 font-medium text-bright">{a.t}</h3>
              <p className="text-sm leading-relaxed text-muted">{a.d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* --- CTA ----------------------------------------------------------
      <section className="border-t border-line">
        <div className="mx-auto max-w-7xl px-6 py-24 text-center md:px-10 md:py-32">
          <h2 className="mx-auto max-w-2xl text-balance text-4xl font-semibold tracking-tight md:text-5xl">
            Pick an area. See what is actually there.
          </h2>
          <p className="mx-auto mt-5 max-w-lg leading-relaxed text-dim">
            No account required. Nothing is kept — your imagery and results are deleted
            automatically when your session ends.
          </p>
          <div className="mt-9 flex flex-wrap justify-center gap-3">
            <Link to="/explore" className="btn-primary">Explore satellite data</Link>
            <Link to="/process" className="btn-ghost">Upload your own imagery</Link>
          </div>
        </div>
      </section> */}

      <footer className="border-t border-line bg-abyss">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-6 py-8 text-xs text-muted md:flex-row md:items-center md:justify-between md:px-10">
          <div>
            PixelForge · Smart India Hackathon 2026 · Problem statement 26142 ·
            Deep-learning super-resolution mapping from medium-resolution
            satellite imagery
          </div>
          <div className="flex gap-5">
            <Link to="/session" className="transition-colors hover:text-bright">
              Your data
            </Link>
            <a href="/docs" className="transition-colors hover:text-bright">
              API
            </a>
          </div>
        </div>
      </footer>
    </main>
  );
}
