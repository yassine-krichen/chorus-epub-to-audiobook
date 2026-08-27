"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { JobEmptyState } from "@/components/jobs/job-empty-state";
import { JobTable } from "@/components/jobs/job-table";
import { useJobs } from "@/hooks/use-jobs";

const PAGE_SIZE = 20;

export default function DashboardPage() {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useJobs(page, PAGE_SIZE);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Jobs</h1>
        <Button render={<Link href="/jobs/new" />}>New job</Button>
      </div>

      {isLoading && (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      )}

      {!isLoading && data && data.jobs.length === 0 && <JobEmptyState />}

      {!isLoading && data && data.jobs.length > 0 && (
        <div className="flex flex-col gap-4">
          <JobTable jobs={data.jobs} />
          <div className="flex items-center justify-between">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </Button>
            <span className="text-sm text-muted-foreground">
              Page {data.page} of {Math.max(1, Math.ceil(data.total / data.page_size))}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page * data.page_size >= data.total}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
