import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { GeoJSONPolygon } from "@/types/api";

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

export const useHealth = () =>
  useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 30_000,
             staleTime: 15_000 });

export const useModels = () =>
  useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 60_000 });

export const useSession = () =>
  useQuery({ queryKey: ["session"], queryFn: api.session });

/**
 * Poll job status until it reaches a terminal state (ADR-005).
 * Polling stops itself — this is what prevents a runaway request loop on a stuck job.
 */
export function useJobStatus(jobId: string | undefined, intervalMs = 1500) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.jobStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s && TERMINAL.has(s) ? false : intervalMs;
    },
  });
}

export function useJobResults(jobId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ["results", jobId],
    queryFn: () => api.jobResults(jobId!),
    enabled: !!jobId && enabled,
    staleTime: 30_000,
  });
}

export function useAoiPreview(geometry: GeoJSONPolygon | null, modelKey?: string) {
  return useQuery({
    queryKey: ["aoi-preview", geometry, modelKey],
    queryFn: () => api.previewAoi(geometry!, modelKey),
    enabled: !!geometry,
    retry: false,           // AOI_TOO_LARGE is a legitimate answer, not a failure
  });
}

export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createJob,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["session"] }); },
  });
}

export function useUpload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, force, onProgress }:
                 { file: File; force?: boolean; onProgress?: (p: number) => void }) =>
      api.uploadScene(file, force, onProgress),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["session"] }); },
  });
}

export function useClearSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.clearSession,
    onSuccess: () => { qc.clear(); },
  });
}

export const useChatCapabilities = () =>
  useQuery({ queryKey: ["chat-caps"], queryFn: api.chatCapabilities, staleTime: 300_000 });

export function useChat(jobId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (message: string) => api.chat(jobId, message),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["chat", jobId] }); },
  });
}

export function useRunAnalysis(jobId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ type, runId }: { type: string; runId?: string }) =>
      api.runAnalysis(jobId, type, runId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["results", jobId] }); },
  });
}
