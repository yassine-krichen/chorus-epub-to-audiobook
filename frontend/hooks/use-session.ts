import { useQuery } from "@tanstack/react-query";

import { fetchSession } from "@/lib/api/auth";
import type { SessionResponse } from "@/types/auth";

export function useSession() {
  return useQuery<SessionResponse | null>({
    queryKey: ["session"],
    queryFn: async () => {
      const { data } = await fetchSession();
      return data;
    },
    staleTime: 60_000,
  });
}
