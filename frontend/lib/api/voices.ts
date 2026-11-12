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

/** `size` is the exact byte length of the WAV that will be uploaded; it is signed into the URL. */
export async function requestCloneUploadUrl(size: number) {
  return apiFetch<CloneUploadUrl>("/api/backend/voices/clones/upload-url", {
    method: "POST",
    body: JSON.stringify({ size }),
  });
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
