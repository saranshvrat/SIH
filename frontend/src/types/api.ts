/**
 * API types.
 *
 * Generate the authoritative version from the live backend:
 *   npx openapi-typescript http://localhost:8000/openapi.json -o src/types/schema.d.ts
 * These hand-written types mirror it and are what the app imports.
 */

export interface ApiError {
  error: { code: string; message: string; details: Record<string, unknown>;
           retryable: boolean; request_id: string };
}

export interface ModelInfo {
  key: string; display_name: string; description: string;
  status: "available" | "unavailable";
  unavailable_reason: string | null;
  is_baseline: boolean; scale_factor: number;
  input_gsd_m: number; output_gsd_m: number;
  input_bands: string[]; output_bands: string[];
  requires_gpu: boolean; supports_uncertainty: boolean; supports_sampling: boolean;
  max_input_px: number; best_for: string[];
  verified_against_repo: boolean;
  health: Record<string, unknown>;
}

export interface HealthResponse {
  status: "ok" | "degraded"; version: string; environment: string;
  database: boolean; redis: boolean; storage: { ok?: boolean; driver?: string };
  gpu_workers_online: number; models_available: string[];
  features: { catalog: boolean; llm: boolean; async_workers: boolean };
  contract_version: string;
}

export interface GeoJSONPolygon { type: "Polygon"; coordinates: number[][][] }

export interface AoiResponse {
  aoi_id: string; name: string; geometry: GeoJSONPolygon; area_km2: number;
  bbox: number[]; utm_crs: string;
  estimated_lr_px: { width: number; height: number };
  estimated_sr_px: { width: number; height: number };
  estimated_output_mb: number; estimated_runtime_s: number | null;
  warnings: string[];
}

export interface SceneSummary {
  scene_id: string | null; external_id: string | null; source: string; provider: string;
  sensed_at: string | null; cloud_cover: number | null; aoi_cloud_cover: number | null;
  tile_id: string | null; footprint: GeoJSONPolygon | null;
  crs: string | null; gsd_m: number | null; available_bands: string[];
  quicklook_url: string | null; size_bytes: number | null; status: string;
  original_filename: string | null;
  band_mapping: Record<string, string>; band_mapping_confirmed: boolean;
  validation: Record<string, any> | null;
}

export type StageStatus = "pending" | "running" | "done" | "skipped" | "failed";
export interface Stage {
  name: string; status: StageStatus; progress?: number;
  reason?: string; reason_text?: string; detail?: string; duration_s?: number;
}

export interface JobStatus {
  job_id: string; status: string; mode: string; progress: number;
  current_stage: string; stage_detail: string | null;
  stages: Stage[];
  runs: { run_id: string; model_key: string; status: string; is_baseline: boolean;
          output_gsd_m: number | null; runtime_s: number | null;
          error_code: string | null }[];
  terrain: { class: string; distribution: Record<string, number>;
             method: string; confidence: number } | null;
  cloud_fraction_aoi: number | null; partial_success: boolean;
  error: { code: string; message: string; stage: string; retryable: boolean } | null;
  created_at: string; duration_s: number | null; expires_at: string;
}

export interface MetricValue {
  value: number | null; unit: string | null;
  reference_type?: string; explanation?: string;
}

export interface RunMetrics {
  /** `null` means no ground truth exists. It is NEVER a zero or a placeholder. */
  full_reference: Record<string, MetricValue | null> | null;
  full_reference_unavailable_reason: string | null;
  no_reference: Record<string, MetricValue>;
  reference_type: string;
}

export interface ArtifactRef {
  artifact_id: string; kind: string; format: string; bytes: number;
  width: number | null; height: number | null; band_count: number | null;
  crs: string | null; bounds_wgs84: number[] | null;
  url: string; download_url: string; meta: Record<string, any>;
}

export interface RunResult {
  run_id: string; model_key: string; model_display_name: string;
  status: string; is_baseline: boolean;
  model_version: string | null; ml_commit: string | null;
  output: { gsd_m: number | null; scale_factor: number | null; bands: string[];
            crs: string | null; bounds_wgs84: number[] | null;
            width: number | null; height: number | null; dtype: string | null };
  geospatial_integrity: Record<string, any> | null;
  performance: { runtime_s: number | null; peak_vram_mb: number | null;
                 device: string | null; tiles: number | null };
  metrics: RunMetrics;
  delta_vs_baseline: Record<string, { delta: number; improved: boolean;
                                      baseline_value: number }> | null;
  warnings: string[];
  error: { code: string; message: string } | null;
  artifacts: ArtifactRef[];
}

export interface UncertaintyInfo {
  available: boolean; reason?: string; reason_text?: string;
  method?: string;
  /** Always shown next to the map. False until validated against ground truth. */
  calibrated?: boolean;
  statistic?: string; unit?: string;
  stats?: { min: number; max: number; mean: number; p50: number; p95: number };
  confidence_score?: number; confidence_definition?: string;
  source_run_ids?: string[];
  high_uncertainty_regions?: { geometry: any; area_km2: number;
                               mean_uncertainty: number }[];
}

export interface AnalysisResult {
  analysis_id: string; type: string; status: string; run_id: string;
  computed_on: string;
  sr: { stats: Record<string, number>; classes: any[]; histogram?: any;
        formula?: string; label?: string; gsd_m?: number };
  lr: { stats: Record<string, number>; classes: any[]; gsd_m?: number };
  comparison_with_lr: { mean_delta: number | null;
                        class_area_delta_km2: Record<string, number>;
                        class_area_delta_pct: Record<string, number>;
                        caveat: string };
  artifacts?: ArtifactRef[];
}

export interface JobResults {
  job_id: string; status: string; mode: string; partial_success: boolean;
  scene: { scene_id: string | null; source: string | null; sensed_at: string | null;
           cloud_cover: number | null; original_filename: string | null };
  input: { artifacts: ArtifactRef[]; cloud_fraction_aoi: number | null };
  terrain: { class: string; distribution: Record<string, number>; method: string;
             confidence: number; basis: string } | null;
  runs: RunResult[];
  uncertainty: UncertaintyInfo;
  analyses: AnalysisResult[];
  job_artifacts: ArtifactRef[];
  provenance: { contract_version: string; ml_commit: string; disclaimer: string };
  expires_at: string;
}

export interface SessionInfo {
  session_id: string; created_at: string; expires_at: string; ttl_hours: number;
  jobs: { job_id: string; status: string; progress: number; models: string[];
          terrain: string | null; created_at: string; expires_at: string }[];
  counts: { jobs: number; scenes: number; files: number };
  bytes_stored: number;
  privacy: { accounts: boolean; what_is_stored: string; where: string;
             how_long: string; how_to_delete_now: string };
}

export interface ChatCapabilities {
  enabled: boolean; provider: string | null; mode: "llm" | "template";
  notice: string | null;
  tools: { name: string; description: string }[];
  suggestions: string[]; guarantee: string;
}

export interface ChatResponse {
  chat_session_id: string; answer: string; llm_used: boolean;
  degraded_reason: string | null;
  tool_calls: { tool: string; input: any; result: any }[];
  provenance: { verified: boolean; unverified_numbers: string[]; note?: string };
}
