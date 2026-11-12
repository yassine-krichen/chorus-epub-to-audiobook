import { useQuery } from "@tanstack/react-query";

import { fetchCloneOptions, fetchClonedVoices } from "@/lib/api/voices";
import type { CloneOptions, ClonedVoice } from "@/types/voice";

/** The passages to read, the consent text and the rules for a recording. */
export function useCloneOptions() {
  return useQuery<CloneOptions | null>({
    queryKey: ["clone-options"],
    queryFn: async () => {
      const { data } = await fetchCloneOptions();
      return data;
    },
    // Whether a worker is online changes while the page is open.
    refetchInterval: 30_000,
  });
}

export function useClonedVoices() {
  return useQuery<ClonedVoice[]>({
    queryKey: ["cloned-voices"],
    queryFn: async () => {
      const { data } = await fetchClonedVoices();
      return data ?? [];
    },
  });
}
