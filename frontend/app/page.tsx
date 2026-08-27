import { redirect } from "next/navigation";

import { readAuthCookies } from "@/lib/server/auth-cookies";

export default async function RootPage() {
  const { accessToken } = await readAuthCookies();
  redirect(accessToken ? "/dashboard" : "/login");
}
