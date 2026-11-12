import { apiFetch } from "@/lib/api/client";
import type {
  CloneOptions,
  CloneUploadUrl,
  ClonedVoice,
  CreateClonedVoiceRequest,
  VoicesResponse,
} from "@/types/voice";

export async function fetchVoices() {
  return apiFetch<VoicesResponse>("/api/backend/voices");
}

export async function fetchCloneOptions() {
  return apiFetch<CloneOptions>("/api/backend/voices/clones/options");
}

export async function fetchClonedVoices() {
  return apiFetch<ClonedVoice[]>("/api/backend/voices/clones");
}

export async function requestCloneUploadUrl() {
  return apiFetch<CloneUploadUrl>("/api/backend/voices/clones/upload-url", { method: "POST" });
}

export async function createClonedVoice(request: CreateClonedVoiceRequest) {
  return apiFetch<ClonedVoice>("/api/backend/voices/clones", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function deleteClonedVoice(voiceId: string) {
  return apiFetch<null>(`/api/backend/voices/clones/${voiceId}`, { method: "DELETE" });
}
