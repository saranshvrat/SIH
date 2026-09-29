import type { GeoJSONPolygon } from "@/types/api";
import maplibregl, { type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";

/**
 * MapLibre canvas with click-to-sample AOI selection.
 *
 * A single click drops a fixed-size square, centred on that point, as the AOI — no
 * separate "start drawing" mode, since a plain click is unambiguous (MapLibre only fires
 * "click" for an actual click, never for a pan-drag). The hover coordinate readout shows
 * where a click will land before the user commits to it, and the same coordinate can be
 * typed directly (see Explore.tsx, which imports `squareFromPoint` from this file so both
 * paths build the exact same square).
 *
 * Implementation notes (each of these was a real bug):
 *  - maplibre-gl.css sets `.maplibregl-map { position: relative }`. That has the same
 *    specificity as Tailwind's `.absolute`, so whichever stylesheet loads last wins. Because
 *    the map chunk is lazy-loaded, MapLibre's CSS won, the container collapsed to 0px tall
 *    and the map was invisible. The container is therefore positioned with INLINE styles,
 *    which no stylesheet can override.
 *  - Overlay sources/layers (AOI, draft, footprints) live inside the style itself and the
 *    two basemaps are toggled with layer visibility. Calling `setStyle()` wipes every custom
 *    layer, which used to make the AOI vanish when the basemap was switched.
 *  - A ResizeObserver keeps the WebGL canvas in sync with its container.
 */

export type DrawMode = "none" | "point";

interface Props {
  onAoiChange: (geom: GeoJSONPolygon | null) => void;
  aoi: GeoJSONPolygon | null;
  drawMode: DrawMode;
  onDrawComplete?: () => void;
  /** Edge length, in metres, of the square sampled around a clicked/typed point. */
  sampleSizeM?: number;
  footprints?: { id: string; geometry: any; active: boolean }[];
  center?: [number, number];
  zoom?: number;
  className?: string;
}

type Basemap = "sentinel2" | "satellite" | "dark";

const DEFAULT_BASEMAP: Basemap = "sentinel2";
const BASEMAP_LAYER: Record<Basemap, string> = {
  sentinel2: "s2",
  satellite: "esri",
  dark: "osm",
};
const BASEMAP_SOURCES = Object.values(BASEMAP_LAYER);
const BASEMAP_LABEL: Record<Basemap, string> = {
  sentinel2: "sentinel-2",
  satellite: "hi-res",
  dark: "dark",
};

// Sentinel-2 basemap, served live from Microsoft's Planetary Computer Data API
// (titiler-pgstac) rather than a pre-baked mosaic. We register a "virtual mosaic" search
// (least-cloud Sentinel-2 L2A, global) once, then request tiles against the returned
// search id. Same catalogue/collection the point-sampling pipeline reads from — see
// PC_* constants below and the `wsx4_spatrad_eval_lat_lon` reference notebook, which
// fetches individual crops from this same STAC API.
const PC_REGISTER_URL = "https://planetarycomputer.microsoft.com/api/data/v1/mosaic/register";
const PC_COLLECTION = "sentinel-2-l2a";
const PC_MAX_CLOUD_COVER = 20;
const PC_ATTRIBUTION =
  `<a href="https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a" target="_blank" rel="noopener">Sentinel-2 L2A</a> via ` +
  `<a href="https://planetarycomputer.microsoft.com" target="_blank" rel="noopener">Microsoft Planetary Computer</a> ` +
  `(ESA/Copernicus Sentinel data, least-cloud-first mosaic)`;

function pcTileUrl(searchId: string): string {
  return (
    `https://planetarycomputer.microsoft.com/api/data/v1/mosaic/tiles/${searchId}/WebMercatorQuad/{z}/{x}/{y}@1x` +
    `?assets=B04&assets=B03&assets=B02&nodata=0&collection=${PC_COLLECTION}` +
    `&color_formula=Gamma+RGB+3.2+Saturation+0.8+Sigmoidal+RGB+25+0.35`
  );
}
// 1x1 transparent GIF: shown for the sentinel2 source until the Planetary Computer mosaic
// search finishes registering (usually well under a second). No {z}/{x}/{y} placeholders,
// so every tile request resolves to this same cached, harmless image.
const BLANK_TILE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";

const DEFAULT_CENTER: [number, number] = [87.32, 23.52];
const TILE_FAILURE_LIMIT = 6;

const emptyCollection = () => ({ type: "FeatureCollection", features: [] });

/**
 * Builds the square AOI (in WGS84) of `sizeM` metres per side, centred on (lng, lat).
 * Same local equirectangular approximation the wsx4_spatrad evaluation notebook uses for
 * its point-based Sentinel-2 fetch — accurate to a few metres at these sizes.
 */
export function squareFromPoint(lng: number, lat: number, sizeM: number): GeoJSONPolygon {
  const metresPerDegLat = 111_320;
  const metresPerDegLon = 111_320 * Math.cos((lat * Math.PI) / 180);
  const halfLat = sizeM / 2 / metresPerDegLat;
  const halfLon = sizeM / 2 / metresPerDegLon;
  const west = lng - halfLon, east = lng + halfLon;
  const south = lat - halfLat, north = lat + halfLat;
  return {
    type: "Polygon",
    coordinates: [
      [
        [west, south],
        [east, south],
        [east, north],
        [west, north],
        [west, south],
      ],
    ],
  };
}

function buildStyle(initial: Basemap): any {
  const vis = (b: Basemap) => ({
    visibility: initial === b ? "visible" : "none",
  });
  return {
    version: 8,
    sources: {
      s2: {
        type: "raster",
        // Placeholder until the Planetary Computer mosaic search resolves (see the
        // "register Planetary Computer mosaic" effect below, which swaps this via
        // source.setTiles()). Native resolution is ~10 m (about zoom 13-14); MapLibre
        // stretches the last real level beyond this, so raise it only if you confirm
        // tiles still resolve at that zoom.
        tiles: [BLANK_TILE],
        tileSize: 256,
        maxzoom: 13,
        attribution: PC_ATTRIBUTION,
      },
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        maxzoom: 19,
        attribution: "© OpenStreetMap contributors",
      },
      esri: {
        type: "raster",
        tiles: [
          "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        ],
        tileSize: 256,
        maxzoom: 19,
        attribution: "© Esri, Maxar, Earthstar Geographics",
      },
      footprints: { type: "geojson", data: emptyCollection() },
      aoi: { type: "geojson", data: emptyCollection() },
      draft: { type: "geojson", data: emptyCollection() },
    },
    layers: [
      {
        id: "bg",
        type: "background",
        paint: { "background-color": "#05070a" },
      },
      {
        id: "osm",
        type: "raster",
        source: "osm",
        layout: vis("dark"),
        // Desaturate + dim the raster so it reads as a dark basemap without needing a
        // vector style or an API token.
        paint: {
          "raster-opacity": 0.5,
          "raster-saturation": -0.75,
          "raster-brightness-max": 0.65,
          "raster-contrast": 0.1,
        },
      },
      { id: "esri", type: "raster", source: "esri", layout: vis("satellite") },
      { id: "s2", type: "raster", source: "s2", layout: vis("sentinel2") },

      {
        id: "fp-fill",
        type: "fill",
        source: "footprints",
        paint: {
          "fill-color": "#38d0d8",
          "fill-opacity": ["case", ["get", "active"], 0.16, 0.05],
        },
      },
      {
        id: "fp-line",
        type: "line",
        source: "footprints",
        paint: {
          "line-color": "#38d0d8",
          "line-width": ["case", ["get", "active"], 2, 1],
          "line-opacity": ["case", ["get", "active"], 0.9, 0.35],
        },
      },

      {
        id: "aoi-fill",
        type: "fill",
        source: "aoi",
        paint: { "fill-color": "#38d0d8", "fill-opacity": 0.12 },
      },
      {
        id: "aoi-line",
        type: "line",
        source: "aoi",
        paint: { "line-color": "#7fe8ee", "line-width": 2 },
      },

      {
        id: "draft-fill",
        type: "fill",
        source: "draft",
        paint: { "fill-color": "#38d0d8", "fill-opacity": 0.08 },
      },
      {
        id: "draft-line",
        type: "line",
        source: "draft",
        paint: {
          "line-color": "#7fe8ee",
          "line-width": 1.5,
          "line-dasharray": [2, 2],
        },
      },
    ],
  };
}

export function MapCanvas({
  onAoiChange,
  aoi,
  drawMode,
  onDrawComplete,
  sampleSizeM = 1280,
  footprints = [],
  center = DEFAULT_CENTER,
  zoom = 10,
  className = "",
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const lastCenterRef = useRef(center);
  const sampleSizeRef = useRef(sampleSizeM);
  sampleSizeRef.current = sampleSizeM;

  // Latest callbacks without re-binding the drawing handlers on every parent render
  // (re-binding used to throw away a half-drawn polygon).
  const onAoiChangeRef = useRef(onAoiChange);
  const onDrawCompleteRef = useRef(onDrawComplete);
  onAoiChangeRef.current = onAoiChange;
  onDrawCompleteRef.current = onDrawComplete;

  const [basemap, setBasemap] = useState<Basemap>(DEFAULT_BASEMAP);
  const [ready, setReady] = useState(false);
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [tileFailures, setTileFailures] = useState(0);
  const [pcSearchId, setPcSearchId] = useState<string | null>(null);
  const [pcError, setPcError] = useState(false);

  // --- register the Planetary Computer Sentinel-2 mosaic search, once ------
  useEffect(() => {
    let cancelled = false;
    fetch(PC_REGISTER_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        collections: [PC_COLLECTION],
        query: { "eo:cloud_cover": { lt: PC_MAX_CLOUD_COVER } },
      }),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`register failed (${r.status})`);
        return r.json();
      })
      .then((body) => {
        if (!cancelled && body?.searchid) setPcSearchId(body.searchid);
        else if (!cancelled) throw new Error("no searchid in response");
      })
      .catch(() => {
        if (!cancelled) setPcError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // --- swap the placeholder tile for the real Planetary Computer mosaic ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !pcSearchId) return;
    const src = map.getSource("s2") as maplibregl.RasterTileSource | undefined;
    src?.setTiles([pcTileUrl(pcSearchId)]);
    setTileFailures(0);
  }, [ready, pcSearchId]);

  // --- create the map once --------------------------------------------------
  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current) return;

    const map = new maplibregl.Map({
      container,
      style: buildStyle(DEFAULT_BASEMAP),
      center,
      zoom,
      attributionControl: { compact: true },
    });
    map.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      "bottom-right",
    );
    map.addControl(
      new maplibregl.ScaleControl({ unit: "metric" }),
      "bottom-left",
    );

    let disposed = false;
    // The overlay sources are part of the style, so they exist as soon as the style has
    // parsed. Waiting for the "load" event instead would hang if a single tile request
    // stalls, leaving drawing permanently disabled.
    const markReady = () => {
      if (!disposed && map.getSource("aoi")) setReady(true);
    };
    map.on("styledata", markReady);
    map.once("load", markReady);

    map.on("mousemove", (e) => setCursor([e.lngLat.lng, e.lngLat.lat]));
    map.on("mouseout", () => setCursor(null));
    // Blocked/offline tile requests surface as a bare "Failed to fetch" carrying the tile
    // and source id (no HTTP status), so key off those rather than the error's shape.
    map.on("error", (e) => {
      const ev = e as any;
      if (ev.tile && BASEMAP_SOURCES.includes(ev.sourceId))
        setTileFailures((n) => n + 1);
    });

    const ro = new ResizeObserver(() => map.resize());
    ro.observe(container);
    requestAnimationFrame(() => {
      if (!disposed) map.resize();
    });

    mapRef.current = map;
    return () => {
      disposed = true;
      ro.disconnect();
      map.remove();
      mapRef.current = null;
      setReady(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- basemap toggle (no setStyle, so overlays survive) ---------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    for (const id of Object.values(BASEMAP_LAYER)) {
      map.setLayoutProperty(
        id,
        "visibility",
        id === BASEMAP_LAYER[basemap] ? "visible" : "none",
      );
    }
    setTileFailures(0);
  }, [basemap, ready]);

  // --- fly to a new preset ---------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || center === lastCenterRef.current) return;
    lastCenterRef.current = center;
    map.flyTo({ center, zoom, essential: true });
  }, [center, zoom]);

  // --- render AOI + footprints -------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const src = map.getSource("aoi") as maplibregl.GeoJSONSource | undefined;
    src?.setData(
      aoi
        ? {
            type: "FeatureCollection",
            features: [
              { type: "Feature", geometry: aoi as any, properties: {} },
            ],
          }
        : { type: "FeatureCollection", features: [] },
    );
  }, [aoi, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const src = map.getSource("footprints") as
      | maplibregl.GeoJSONSource
      | undefined;
    src?.setData({
      type: "FeatureCollection",
      features: footprints
        .filter((f) => f.geometry)
        .map((f) => ({
          type: "Feature",
          geometry: f.geometry,
          properties: { id: f.id, active: f.active },
        })),
    } as any);
  }, [footprints, ready]);

  // --- click-to-sample ------------------------------------------------------
  // A single click (not a pan-drag — MapLibre only fires "click" for an actual click)
  // drops the AOI as a square of `sampleSizeM` metres centred on that point. No separate
  // "start drawing" mode: the hover coordinate readout below already shows where a click
  // will sample, so clicking is always live while drawMode is "point".
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;

    map.getCanvas().style.cursor = drawMode === "point" ? "crosshair" : "";
    if (drawMode !== "point") return;

    const onClick = (e: maplibregl.MapMouseEvent) => {
      const square = squareFromPoint(e.lngLat.lng, e.lngLat.lat, sampleSizeRef.current);
      onAoiChangeRef.current(square);
      onDrawCompleteRef.current?.();
    };
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") onAoiChangeRef.current(null);
    };

    map.on("click", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      map.off("click", onClick);
      window.removeEventListener("keydown", onKey);
      map.getCanvas().style.cursor = "";
    };
  }, [drawMode, ready]);

  return (
    <div
      className={`relative min-h-[320px] overflow-hidden bg-void ${className}`}
    >
      {/* Inline positioning on purpose: see the note at the top of this file. */}
      <div
        ref={containerRef}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
        }}
      />

      <div className="pointer-events-auto absolute left-3 top-3 z-10 flex overflow-hidden rounded-md border border-line bg-void/85 backdrop-blur">
        {(["sentinel2", "satellite", "dark"] as const).map((b) => (
          <button
            key={b}
            onClick={() => setBasemap(b)}
            className={`px-3 py-1.5 font-mono text-2xs uppercase tracking-wider transition-colors
                        ${basemap === b ? "bg-signal/20 text-signal" : "text-muted hover:text-bright"}`}
          >
            {BASEMAP_LABEL[b]}
          </button>
        ))}
      </div>

      {basemap === "sentinel2" && (
        <div
          className="pointer-events-none absolute left-3 top-12 z-10 max-w-[15rem] rounded border border-line
                        bg-void/85 px-2 py-1 text-2xs leading-snug text-muted backdrop-blur"
        >
          {pcError
            ? "Could not reach Microsoft Planetary Computer for this basemap."
            : "Sentinel-2 L2A · Microsoft Planetary Computer · 10 m, least-cloud mosaic"}
        </div>
      )}

      {tileFailures >= TILE_FAILURE_LIMIT && (
        <div
          className="pointer-events-none absolute left-1/2 top-14 z-10 max-w-sm -translate-x-1/2
                        rounded-md border border-warn/40 bg-void/90 px-3 py-2 text-center text-xs
                        leading-relaxed text-warn backdrop-blur"
        >
          Basemap tiles are not loading. Check your internet connection or
          ad-blocker, or try another basemap.
        </div>
      )}

      {cursor && (
        <div
          className="pointer-events-none absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded
                        border border-line bg-void/85 px-2.5 py-1 font-mono text-2xs text-dim backdrop-blur"
        >
          {cursor[1].toFixed(5)}°, {cursor[0].toFixed(5)}°
        </div>
      )}

      {drawMode === "point" && (
        <div
          className="pointer-events-none absolute left-1/2 top-3 z-10 -translate-x-1/2 rounded-md
                        border border-signal/40 bg-signal/10 px-3 py-1.5 text-xs text-signal backdrop-blur"
        >
          Click anywhere to sample a {(sampleSizeM / 1000).toFixed(2)} km square there · Esc to clear
        </div>
      )}
    </div>
  );
}
