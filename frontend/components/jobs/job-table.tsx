"use client";

import Link from "next/link";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { JobProgressBar } from "@/components/jobs/job-progress-bar";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { ACTIVE_JOB_STATUSES, type JobSummary } from "@/types/job";
import { cancelJob } from "@/lib/api/jobs";

export function JobTable({ jobs }: { jobs: JobSummary[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Title</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className="hidden sm:table-cell">Progress</TableHead>
          <TableHead className="hidden md:table-cell">Created</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {jobs.map((job) => (
          <JobRow key={job.id} job={job} />
        ))}
      </TableBody>
    </Table>
  );
}

function JobRow({ job }: { job: JobSummary }) {
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
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
  }

  return (
    <TableRow>
      <TableCell className="max-w-48 truncate font-medium">{job.title}</TableCell>
      <TableCell>
        <JobStatusBadge status={job.status} />
      </TableCell>
      <TableCell className="hidden sm:table-cell">
        <JobProgressBar percent={job.progress_percent} />
      </TableCell>
      <TableCell className="hidden text-sm text-muted-foreground md:table-cell">
        {new Date(job.created_at).toLocaleString()}
      </TableCell>
      <TableCell className="flex justify-end gap-2 text-right">
        <Button variant="outline" size="sm" render={<Link href={`/jobs/${job.id}`} />}>
          View
        </Button>
        {isActive && (
          <AlertDialog open={open} onOpenChange={setOpen}>
            <AlertDialogTrigger render={<Button variant="destructive" size="sm" />}>
              Cancel
            </AlertDialogTrigger>
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
      </TableCell>
    </TableRow>
  );
}
