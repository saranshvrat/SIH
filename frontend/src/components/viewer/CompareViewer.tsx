import { useCallback, useEffect, useRef, useState } from "react";
import type { ArtifactRef } from "@/types/api";

/**
 * Comparison viewer: swipe, side-by-side, and single-pane.
 *
 * All panes share ONE pan/zoom transform, so they are always showing the same ground
 * position. Previews were rendered server-side with a stretch computed once on the
 * low-resolution input, so a visible difference is a difference in the model — not in
 * the contrast applied to it.
 */

export interface Pane { id: string; label: string; sublabel?: string; src: string }
type Mode = "swipe" | "side" | "single";

export function CompareViewer({ panes, className = "" }: {
  panes: Pane[]; className?: string;
}) {
  const [mode, setMode] = useState<Mode>("swipe");
  const [split, setSplit] = useState(50);
  const [left, setLeft] = useState(0);
  const [right, setRight] = useState(panes.length > 1 ? 1 : 0);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  const splitDragRef = useRef(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (right >= panes.length) setRight(Math.max(0, panes.length - 1)); },
            [panes.length, right]);

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    setZoom((z) => Math.min(12, Math.max(1, z * (e.deltaY < 0 ? 1.13 : 1 / 1.13))));
  }, []);

  const onPointerDown = (e: React.PointerEvent) => {
    if (splitDragRef.current) return;
    dragRef.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (splitDragRef.current && boxRef.current) {
      const r = boxRef.current.getBoundingClientRect();
      setSplit(Math.min(100, Math.max(0, ((e.clientX - r.left) / r.width) * 100)));
      return;
    }
    if (!dragRef.current) return;
    setPan({ x: e.clientX - dragRef.current.x, y: e.clientY - dragRef.current.y });
  };
  const endDrag = () => { dragRef.current = null; splitDragRef.current = false; };
  const reset = () => { setZoom(1); setPan({ x: 0, y: 0 }); setSplit(50); };

  const transform = `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`;
  const imgStyle: React.CSSProperties = {
    transform, transformOrigin: "center center",
    // Above ~2.5x the reconstructed pixel grid is the honest thing to show.
    imageRendering: zoom > 2.5 ? "pixelated" : "auto",
    transition: dragRef.current ? "none" : "transform 90ms linear",
  };

  if (panes.length === 0) {
    return <div className="grid h-full place-items-center text-sm text-muted">
      No imagery to display.</div>;
  }

  return (
    <div className={`flex flex-col ${className}`}>
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-3 py-2">
        <div className="flex overflow-hidden rounded border border-line">
          {(["swipe", "side", "single"] as Mode[]).map((m) => (
            <button key={m} onClick={() => setMode(m)}
              className={`px-2.5 py-1 font-mono text-2xs uppercase tracking-wider transition-colors
                ${mode === m ? "bg-signal/20 text-signal" : "text-muted hover:text-bright"}`}>
              {m}
            </button>
          ))}
        </div>

        <select value={left} onChange={(e) => setLeft(+e.target.value)}
                className="rounded border border-line bg-void px-2 py-1 text-2xs text-bright">
          {panes.map((p, i) => <option key={p.id} value={i}>{p.label}</option>)}
        </select>
        {mode !== "single" && (
          <>
            <span className="font-mono text-2xs text-muted">vs</span>
            <select value={right} onChange={(e) => setRight(+e.target.value)}
                    className="rounded border border-line bg-void px-2 py-1 text-2xs text-bright">
              {panes.map((p, i) => <option key={p.id} value={i}>{p.label}</option>)}
            </select>
          </>
        )}

        <div className="ml-auto flex items-center gap-2">
          <span className="font-mono text-2xs text-muted">{zoom.toFixed(1)}×</span>
          <button onClick={reset} className="rounded border border-line px-2 py-1 font-mono text-2xs text-muted hover:text-bright">
            reset
          </button>
        </div>
      </div>

      <div ref={boxRef} onWheel={onWheel} onPointerDown={onPointerDown}
           onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerLeave={endDrag}
           className="relative flex-1 cursor-grab overflow-hidden bg-void active:cursor-grabbing">

        {mode === "side" ? (
          <div className="grid h-full grid-cols-2 gap-px bg-line">
            {[left, right].map((idx, i) => (
              <div key={i} className="relative overflow-hidden bg-void">
                <img src={panes[idx]?.src} alt={panes[idx]?.label} draggable={false}
                     style={imgStyle} className="h-full w-full object-contain" />
                <PaneLabel pane={panes[idx]} />
              </div>
            ))}
          </div>
        ) : mode === "single" ? (
          <div className="relative h-full">
            <img src={panes[left]?.src} alt={panes[left]?.label} draggable={false}
                 style={imgStyle} className="h-full w-full object-contain" />
            <PaneLabel pane={panes[left]} />
          </div>
        ) : (
          <div className="relative h-full">
            <img src={panes[left]?.src} alt={panes[left]?.label} draggable={false}
                 style={imgStyle} className="absolute inset-0 h-full w-full object-contain" />
            <div className="absolute inset-0 overflow-hidden"
                 style={{ clipPath: `inset(0 0 0 ${split}%)` }}>
              <img src={panes[right]?.src} alt={panes[right]?.label} draggable={false}
                   style={imgStyle} className="h-full w-full object-contain" />
            </div>
            <div onPointerDown={(e) => { e.stopPropagation(); splitDragRef.current = true; }}
                 className="absolute inset-y-0 z-10 w-1 cursor-col-resize bg-signal/80"
                 style={{ left: `${split}%` }}>
              <div className="absolute left-1/2 top-1/2 flex h-8 w-8 -translate-x-1/2 -translate-y-1/2
                              items-center justify-center rounded-full border border-signal bg-void/90">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#38d0d8" strokeWidth="2.5">
                  <path d="M9 6 3 12l6 6M15 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
            </div>
            <PaneLabel pane={panes[left]} />
            <PaneLabel pane={panes[right]} side="right" />
          </div>
        )}
      </div>
    </div>
  );
}

function PaneLabel({ pane, side = "left" }: { pane?: Pane; side?: "left" | "right" }) {
  if (!pane) return null;
  return (
    <div className={`pointer-events-none absolute bottom-3 ${side === "left" ? "left-3" : "right-3"}
                     rounded border border-line bg-void/85 px-2.5 py-1.5 backdrop-blur`}>
      <div className="font-mono text-2xs uppercase tracking-wider text-bright">{pane.label}</div>
      {pane.sublabel && <div className="mt-0.5 font-mono text-2xs text-muted">{pane.sublabel}</div>}
    </div>
  );
}

export function panesFromResults(
  inputArtifacts: ArtifactRef[],
  runs: { run_id: string; model_display_name: string; is_baseline: boolean;
          output: { gsd_m: number | null }; artifacts: ArtifactRef[] }[],
  combo = "rgb_true",
): Pane[] {
  const panes: Pane[] = [];
  const lr = inputArtifacts.find((a) => a.kind === `preview_lr_${combo}`);
  if (lr) panes.push({ id: "lr", label: "Original", sublabel: "10 m observed", src: lr.url });
  for (const r of runs) {
    const p = r.artifacts.find((a) => a.kind === `preview_${combo}`);
    if (p) panes.push({
      id: r.run_id, label: r.model_display_name,
      sublabel: `${r.output.gsd_m ?? "?"} m${r.is_baseline ? " · baseline" : " · reconstructed"}`,
      src: p.url,
    });
  }
  return panes;
}
