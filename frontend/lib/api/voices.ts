import { apiFetch } from "@/lib/api/client";
import type { VoicesResponse } from "@/types/voice";

export async function fetchVoices() {
  return apiFetch<VoicesResponse>("/api/backend/voices");
}
