import Link from "next/link";

import { Button } from "@/components/ui/button";

export function JobEmptyState() {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed py-16 text-center">
      <h2 className="text-lg font-medium">No jobs yet</h2>
      <p className="max-w-sm text-sm text-muted-foreground">
        Upload an epub to start converting it into an audiobook.
      </p>
      <Button render={<Link href="/jobs/new" />}>Create your first job</Button>
    </div>
  );
}
