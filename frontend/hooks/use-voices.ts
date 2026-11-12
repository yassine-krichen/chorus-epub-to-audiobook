import { useQuery } from "@tanstack/react-query";

import { fetchVoices } from "@/lib/api/voices";
import type { VoicesResponse } from "@/types/voice";

export function useVoices() {
  return useQuery<VoicesResponse | null>({
    queryKey: ["voices"],
    queryFn: async () => {
      const { data } = await fetchVoices();
      return data;
    },
    // Short: whether a local worker can speak the user's cloned voices changes while the page
    // is open, and a stale "available" would let someone pick a voice nothing can read.
    staleTime: 30_000,
  });
}
