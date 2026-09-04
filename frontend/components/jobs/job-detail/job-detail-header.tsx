"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { JobProgressBar } from "@/components/jobs/job-progress-bar";
import { JobRenderStage } from "@/components/jobs/job-render-stage";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { cancelJob } from "@/lib/api/jobs";
import { deriveRenderStage } from "@/lib/jobs/render-stage";
import { ACTIVE_JOB_STATUSES, type JobDetail } from "@/types/job";

export function JobDetailHeader({ job }: { job: JobDetail }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const isActive = ACTIVE_JOB_STATUSES.includes(job.status);

  async function handleCancel() {
    setCanceling(true);
    const { error } = await cancelJob(job.id);
    setCanceling(false);
    setOpen(false);
    if (error) {
      toast.error(error);
      return;
    }
    toast.success("Job canceled.");
    queryClient.invalidateQueries({ queryKey: ["job", job.id] });
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold">{job.title}</h1>
          <div className="flex items-center gap-2">
            <JobStatusBadge status={job.status} />
            <span className="text-xs text-muted-foreground">
              Created {new Date(job.created_at).toLocaleString()}
            </span>
          </div>
        </div>
        {isActive && (
          <AlertDialog open={open} onOpenChange={setOpen}>
            <AlertDialogTrigger render={<Button variant="destructive" />}>Cancel job</AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Cancel this job?</AlertDialogTitle>
                <AlertDialogDescription>
                  &ldquo;{job.title}&rdquo; will stop processing. This can&apos;t be undone.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Keep job</AlertDialogCancel>
                <AlertDialogAction onClick={handleCancel} disabled={canceling}>
                  {canceling ? "Canceling…" : "Cancel job"}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </div>
      <JobProgressBar percent={job.progress_percent} />
      {isActive && <JobRenderStage stage={deriveRenderStage(job)} />}
    </div>
  );
}
