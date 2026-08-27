import { apiFetch } from "@/lib/api/client";
import type { SessionResponse } from "@/types/auth";

export async function fetchSession() {
  return apiFetch<SessionResponse>("/api/auth/session");
}

export async function register(email: string, password: string) {
  return apiFetch<{ id: string; email: string }>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function login(email: string, password: string) {
  return apiFetch<{ authenticated: boolean; email: string }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function logout() {
  return apiFetch<null>("/api/auth/logout", { method: "POST" });
}
