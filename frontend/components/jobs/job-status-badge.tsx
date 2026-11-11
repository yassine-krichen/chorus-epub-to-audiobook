import { Badge } from "@/components/ui/badge";
import type { JobStatus } from "@/types/job";

const STATUS_STYLES: Record<JobStatus, string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  awaiting_cast_review: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  succeeded: "bg-green-500/10 text-green-600 dark:text-green-400",
  failed: "bg-destructive/10 text-destructive",
  canceled: "bg-muted text-muted-foreground",
};

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  running: "Running",
  awaiting_cast_review: "Needs your review",
  succeeded: "Succeeded",
  failed: "Failed",
  canceled: "Canceled",
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <Badge variant="secondary" className={STATUS_STYLES[status]}>
      {STATUS_LABELS[status]}
    </Badge>
  );
}
