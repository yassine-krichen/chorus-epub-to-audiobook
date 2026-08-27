"use client";

import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSession } from "@/hooks/use-session";
import { logout } from "@/lib/api/auth";

export default function SettingsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data: session, isLoading } = useSession();

  async function handleLogout() {
    const { error } = await logout();
    if (error) {
      toast.error(error);
      return;
    }
    queryClient.clear();
    router.push("/login");
    router.refresh();
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <Card className="max-w-md">
        <CardHeader>
          <CardTitle>Account</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {isLoading ? (
            <Skeleton className="h-5 w-48" />
          ) : (
            <p className="text-sm">
              <span className="text-muted-foreground">Email: </span>
              {session?.email}
            </p>
          )}
          <Button variant="destructive" onClick={handleLogout} className="self-start">
            Log out
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
