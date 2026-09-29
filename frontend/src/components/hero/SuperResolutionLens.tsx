import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The "super-resolution lens" (ADR-006).
 *
 * A blocky low-resolution image fills the frame. A soft radial window follows the
 * cursor and reveals the sharp image beneath it. The lens ONLY exists while the
 * cursor is actually over the image — there is no idle demo sweep, and no visible
 * ring or outline when nothing is being hovered.
 *
 * LENS SIZING — expressed in real-world `cm`, not viewport fractions. CSS resolves
 * `cm` at the spec-defined 96px/in reference, so the lens reads as a compact,
 * fixed-size instrument aperture rather than something that balloons on a large
 * hero. FULL_RADIUS_CM is the entire visible effect (soft edge included);
 * CORE_RADIUS_CM is where the image is essentially fully sharp; the gap between
 * the two is the feathered transition.
 *
 * PERFORMANCE CONTRACT — the brief forbids running the model on mouse movement, and
 * this goes further: no per-frame image work happens at all.
 *   - Both images are precomputed static assets, decoded once by the browser.
 *   - Reveal is a CSS `mask-image: radial-gradient`, composited on the GPU.
 *   - Pointer events only write to a ref; a single rAF loop lerps toward the target
 *     and writes two CSS custom properties. No React re-render per frame, and the
 *     loop only runs at all while the cursor is over the image.
 *   - No canvas, no getImageData, no layout thrash.
 *
 * NO-HOVER DEVICES — touch/coarse pointers and `prefers-reduced-motion` never
 * fake the lens with a touch-drag or an auto-playing sweep. They get the sharp
 * image shown plainly, and the rAF loop never starts.
 */

interface Props {
  sharpSrc: string;
  blockySrc: string;
  /** Full effect radius, including the feathered edge. */
  fullRadiusCm?: number;
  /** Radius of the essentially-fully-sharp core. Must be <= fullRadiusCm. */
  coreRadiusCm?: number;
  className?: string;
}

const LERP = 0.16;              // cursor follow easing — high enough to feel responsive
const FADE_MS = 420;            // how long the reveal takes to appear/disappear on enter/leave

export function SuperResolutionLens({
  sharpSrc, blockySrc, fullRadiusCm = 3, coreRadiusCm = 2.3, className = "",
}: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const targetRef = useRef({ x: 0.5, y: 0.5 });
  const currentRef = useRef({ x: 0.5, y: 0.5 });
  const rafRef = useRef<number>();

  const [loaded, setLoaded] = useState(false);
  const [coarse, setCoarse] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [hovering, setHovering] = useState(false);

  // Static presentation for devices with no reliable hover, and for anyone who
  // has asked for less motion — never a simulated lens on either.
  const isInteractive = !coarse && !reducedMotion;

  useEffect(() => {
    // Touch/pen devices have no hover — fall back to the static sharp image.
    const mq = window.matchMedia("(hover: none), (pointer: coarse)");
    const apply = () => setCoarse(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReducedMotion(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    // Decode both images before showing anything, so the reveal never flashes an
    // undecoded sharp layer.
    let cancelled = false;
    Promise.all([sharpSrc, blockySrc].map(
      (src) => new Promise<void>((resolve) => {
        const img = new Image();
        img.onload = () => resolve();
        img.onerror = () => resolve();      // degrade to whatever rendered
        img.src = src;
      }),
    )).then(() => { if (!cancelled) setLoaded(true); });
    return () => { cancelled = true; };
  }, [sharpSrc, blockySrc]);

  // The rAF loop exists ONLY while the cursor is actually over the image on an
  // interactive device. No idle sweep, nothing animates when unhovered.
  useEffect(() => {
    const el = rootRef.current;
    if (!el || !isInteractive || !hovering) return;

    const tick = () => {
      const c = currentRef.current;
      const t = targetRef.current;
      c.x += (t.x - c.x) * LERP;
      c.y += (t.y - c.y) * LERP;
      el.style.setProperty("--lens-x", `${(c.x * 100).toFixed(2)}%`);
      el.style.setProperty("--lens-y", `${(c.y * 100).toFixed(2)}%`);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [isInteractive, hovering]);

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!isInteractive) return;
    const r = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const y = (e.clientY - r.top) / r.height;
    targetRef.current = { x, y };
    if (!hovering) {
      // Snap the current position to the entry point so the lens fades in
      // exactly where the cursor is, instead of sliding in from center.
      currentRef.current = { x, y };
      const el = rootRef.current;
      if (el) {
        el.style.setProperty("--lens-x", `${(x * 100).toFixed(2)}%`);
        el.style.setProperty("--lens-y", `${(y * 100).toFixed(2)}%`);
      }
      setHovering(true);
    }
  }, [isInteractive, hovering]);

  const onPointerLeave = useCallback(() => {
    setHovering(false);
  }, []);

  // Core stays solid out to (core / full) of the radius; the remainder — the
  // last ~0.7cm at the spec defaults — is the feathered transition to fully
  // low-res. Using native `cm` units means the lens is a fixed physical size
  // regardless of hero height or viewport, and needs no manual DPI math.
  const fullR = `${fullRadiusCm}cm`;
  const coreStop = `${((coreRadiusCm / fullRadiusCm) * 100).toFixed(1)}%`;
  const revealMask =
    `radial-gradient(circle ${fullR} at var(--lens-x) var(--lens-y),
     #000 0%, #000 ${coreStop}, transparent 100%)`;

  const showLens = isInteractive && hovering;

  return (
    <div
      ref={rootRef}
      onPointerMove={onPointerMove}
      onPointerLeave={onPointerLeave}
      className={`relative overflow-hidden select-none ${isInteractive ? "cursor-crosshair" : ""} ${className}`}
      style={{ "--lens-x": "50%", "--lens-y": "50%" } as React.CSSProperties}
      aria-label={isInteractive
        ? "Interactive demonstration: move the cursor to reveal super-resolved detail"
        : "Super-resolved satellite imagery"}
      role="img"
    >
      {/* Layer 1 — the low-resolution observation. Skipped entirely on static
          devices: the spec wants the sharp image as the primary visible frame,
          not a blocky one the viewer can never reveal. */}
      {isInteractive && (
        <img
          src={blockySrc} alt="" aria-hidden draggable={false}
          className={`absolute inset-0 h-full w-full object-cover transition-opacity duration-700
                      ${loaded ? "opacity-100" : "opacity-0"}`}
          style={{ imageRendering: "pixelated" }}
        />
      )}

      {/* Layer 2 — the sharp image. Masked to the lens on interactive devices
          (and only actually visible through it while hovering); shown in full,
          statically, on non-interactive devices. */}
      <img
        src={sharpSrc} alt="" aria-hidden draggable={false}
        className={`absolute inset-0 h-full w-full object-cover duration-700
                    ${loaded ? "opacity-100" : "opacity-0"}`}
        style={
          isInteractive
            ? {
                WebkitMaskImage: revealMask,
                maskImage: revealMask,
                opacity: loaded ? (showLens ? 1 : 0) : 0,
                transition: `opacity ${FADE_MS}ms ease, mask-position 0ms`,
              }
            : undefined
        }
      />

      {/* Layer 3 — illumination. Lifts the revealed region without washing it out.
          Fades with the lens itself — never visible when unhovered. */}
      {isInteractive && (
        <div
          className="pointer-events-none absolute inset-0 mix-blend-plus-lighter"
          style={{
            opacity: showLens ? 1 : 0,
            transition: `opacity ${FADE_MS}ms ease`,
            background:
              `radial-gradient(circle ${fullR} at var(--lens-x) var(--lens-y),
               rgba(56,208,216,0.12) 0%, rgba(56,208,216,0.03) 45%, transparent 70%)`,
          }}
        />
      )}

      {/* Vignette — keeps text legible over any imagery. */}
      <div className="pointer-events-none absolute inset-0"
           style={{ background:
             "radial-gradient(ellipse at 50% 45%, transparent 35%, rgba(5,7,10,0.55) 78%, rgba(5,7,10,0.94) 100%)" }} />

      {!loaded && (
        <div className="absolute inset-0 grid place-items-center bg-abyss">
          <div className="label-mono animate-pulse">loading imagery…</div>
        </div>
      )}
    </div>
  );
}
