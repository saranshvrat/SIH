import { Link } from "react-router-dom";
import { SuperResolutionLens } from "./SuperResolutionLens";

// Lens sizing lives here, not scattered as magic numbers — see ADR-006.
const HERO_LENS_FULL_RADIUS_CM = 3;
const HERO_LENS_CORE_RADIUS_CM = 2.3;

export function Hero() {
  return (
    <section className="relative h-[100svh] min-h-[620px] w-full overflow-hidden">
      <SuperResolutionLens
        sharpSrc="/hero/hero-sharp.webp"
        blockySrc="/hero/hero-blocky.webp"
        fullRadiusCm={HERO_LENS_FULL_RADIUS_CM}
        coreRadiusCm={HERO_LENS_CORE_RADIUS_CM}
        className="absolute inset-0 h-full w-full"
      />

      <div className="pointer-events-none absolute inset-0 grid-field opacity-30" />

      <div className="relative z-10 mx-auto flex h-full max-w-7xl flex-col justify-between px-6 py-8 md:px-10">
        <header className="pointer-events-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Mark />
            <span className="font-semibold tracking-tight">PixelForge</span>
          </div>
          <nav className="flex items-center gap-6 text-sm text-dim">
            <a href="#pipeline" className="hidden transition-colors hover:text-bright sm:block">Pipeline</a>
            <a href="#trust" className="hidden transition-colors hover:text-bright sm:block">Uncertainty</a>
            <Link to="/explore" className="btn-ghost !px-3 !py-1.5 text-xs">Launch</Link>
          </nav>
        </header>

      </div>

      {/* Closing-CTA copy, brought up into the hero (matches the section at the
          bottom of the landing page). Positioned absolute and given a z-index
          above the lens layer, since it sits directly on top of the hover effect. */}
      <div className="pointer-events-none absolute inset-0 z-20 mx-auto flex h-full max-w-2xl flex-col items-center justify-center px-6 text-center md:px-10">
        <h1 className="animate-fade-up text-balance text-4xl font-semibold tracking-tight md:text-5xl">
          Pick an area. See what is actually there.
        </h1>

        <p className="animate-fade-up mx-auto mt-5 max-w-lg leading-relaxed text-dim"
           style={{ animationDelay: "80ms" }}>
          No account required. Nothing is kept — your imagery and results are deleted
          automatically when your session ends.
        </p>

        <div className="animate-fade-up pointer-events-auto mt-9 flex flex-wrap items-center justify-center gap-3"
             style={{ animationDelay: "160ms" }}>
          <Link to="/explore" className="btn-primary">Explore satellite data</Link>
          <Link to="/process" className="btn-ghost">Upload your own imagery</Link>
        </div>
      </div>

      <div className="absolute bottom-0 left-0 right-0 z-10 h-24 bg-gradient-to-t from-void to-transparent" />
    </section>
  );
}

function Mark() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" fill="none" aria-hidden>
      <rect x="1.5" y="1.5" width="29" height="29" rx="4" stroke="#2a3542" strokeWidth="1.5" />
      <rect x="7" y="7" width="8" height="8" fill="#1d8d96" />
      <rect x="17" y="7" width="8" height="8" fill="#38d0d8" opacity="0.55" />
      <rect x="7" y="17" width="8" height="8" fill="#38d0d8" opacity="0.8" />
      <g fill="#7fe8ee">
        <rect x="17" y="17" width="3.6" height="3.6" />
        <rect x="21.4" y="17" width="3.6" height="3.6" opacity="0.7" />
        <rect x="17" y="21.4" width="3.6" height="3.6" opacity="0.7" />
        <rect x="21.4" y="21.4" width="3.6" height="3.6" />
      </g>
    </svg>
  );
}
