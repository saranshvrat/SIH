import type {
  AoiResponse, ChatCapabilities, ChatResponse, GeoJSONPolygon, HealthResponse,
  JobResults, JobStatus, ModelInfo, SceneSummary, SessionInfo,
} from "@/types/api";

const BASE = import.meta.env.VITE_API_BASE ?? "/api/v1";

/** Carries the backend's stable error `code` so the UI can branch on it. */
export class PixelForgeError extends Error {
  constructor(
    public code: string,
    message: string,
    public details: Record<string, any> = {},
    public status = 0,
    public retryable = false,
    public requestId = "",
  ) { super(message); this.name = "PixelForgeError"; }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: "include",            // the anonymous session cookie
      headers: init.body instanceof FormData
        ? (init.headers ?? {})
        : { "Content-Type": "application/json", ...(init.headers ?? {}) },
    });
  } catch {
    throw new PixelForgeError(
      "NETWORK_ERROR",
      "Could not reach the PixelForge API. Check that the backend is running.");
  }

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  let body: any = null;
  try { body = text ? JSON.parse(text) : null; } catch { /* non-JSON */ }

  if (!res.ok) {
    const e = body?.error;
    throw new PixelForgeError(
      e?.code ?? "UNKNOWN_ERROR",
      e?.message ?? `Request failed (${res.status}).`,
      e?.details ?? {}, res.status, e?.retryable ?? false, e?.request_id ?? "");
  }
  return body as T;
}

export const api = {
  health: () => request<HealthResponse>("/health"),

  models: () => request<{ models: ModelInfo[]; default_model: string;
                          contract_version: string }>("/models"),

  previewAoi: (geometry: GeoJSONPolygon, modelKey?: string) =>
    request<AoiResponse>("/aois/preview", {
      method: "POST",
      body: JSON.stringify({ name: "preview", geometry, model_key: modelKey }),
    }),

  createAoi: (geometry: GeoJSONPolygon, name = "Area of interest", modelKey?: string) =>
    request<AoiResponse>("/aois", {
      method: "POST", body: JSON.stringify({ name, geometry, model_key: modelKey }),
    }),

  searchScenes: (body: { aoi: GeoJSONPolygon; date_from: string; date_to: string;
                         max_cloud_cover?: number; limit?: number }) =>
    request<{ scenes: SceneSummary[]; total: number; provider_query_ms: number;
              notes: string[] }>("/scenes/search",
      { method: "POST", body: JSON.stringify(body) }),

  uploadScene: (file: File, force = false, onProgress?: (pct: number) => void) =>
    new Promise<SceneSummary>((resolve, reject) => {
      // XHR rather than fetch: upload progress needs it, and these files are large.
      const form = new FormData();
      form.append("file", file);
      form.append("force", String(force));
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/scenes/upload`);
      xhr.withCredentials = true;
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => {
        let body: any = null;
        try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) return resolve(body);
        const e = body?.error;
        reject(new PixelForgeError(e?.code ?? "UPLOAD_FAILED",
          e?.message ?? "Upload failed.", e?.details ?? {}, xhr.status));
      };
      xhr.onerror = () => reject(new PixelForgeError(
        "NETWORK_ERROR", "The upload could not reach the server."));
      xhr.send(form);
    }),

  listScenes: () => request<SceneSummary[]>("/scenes"),
  getScene: (id: string) => request<SceneSummary>(`/scenes/${id}`),

  confirmBands: (id: string, bandMapping: Record<string, string>) =>
    request<SceneSummary>(`/scenes/${id}/bands`,
      { method: "PATCH", body: JSON.stringify({ band_mapping: bandMapping }) }),

  createJob: (body: {
    scene_id: string; aoi_id?: string | null; models: string[];
    auto_select_model?: boolean;
    options?: Record<string, unknown>; idempotency_key?: string;
  }) => request<{ job_id: string; status: string; poll_url: string;
                  already_existed: boolean }>("/jobs",
    { method: "POST", body: JSON.stringify(body) }),

  jobStatus: (id: string) => request<JobStatus>(`/jobs/${id}/status`),
  jobResults: (id: string) => request<JobResults>(`/jobs/${id}/results`),
  cancelJob: (id: string) => request<JobStatus>(`/jobs/${id}/cancel`, { method: "POST" }),

  session: () => request<SessionInfo>("/session"),
  clearSession: () => request<{ cleared: boolean; rows_deleted: number;
                                objects_deleted: number }>("/session",
    { method: "DELETE" }),

  availableAnalyses: () => request<{ analyses: any[] }>("/analyses/available"),
  runAnalysis: (jobId: string, type: string, runId?: string) =>
    request<any>(`/jobs/${jobId}/analyses`,
      { method: "POST", body: JSON.stringify({ type, run_id: runId }) }),

  chatCapabilities: () => request<ChatCapabilities>("/chat/capabilities"),
  chat: (jobId: string, message: string) =>
    request<ChatResponse>(`/jobs/${jobId}/chat`,
      { method: "POST", body: JSON.stringify({ message }) }),
  chatHistory: (jobId: string) =>
    request<{ messages: any[]; suggestions: string[] }>(`/jobs/${jobId}/chat`),

  createReport: (jobId: string, format: "json" | "zip" | "pdf") =>
    request<{ report_id: string; format: string; download_url: string;
              notes: string[] }>(`/jobs/${jobId}/report`,
      { method: "POST", body: JSON.stringify({ format }) }),
};
