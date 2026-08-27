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
    staleTime: 5 * 60_000,
  });
}
