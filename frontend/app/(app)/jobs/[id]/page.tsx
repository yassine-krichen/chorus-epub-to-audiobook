"use client";

import { use } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { ChapterGrid } from "@/components/jobs/job-detail/chapter-grid";
import { JobDetailHeader } from "@/components/jobs/job-detail/job-detail-header";
import { JobErrorPanel } from "@/components/jobs/job-detail/job-error-panel";
import { PartList } from "@/components/jobs/job-detail/part-list";
import { useJob } from "@/hooks/use-jobs";

interface JobDetailPageProps {
  params: Promise<{ id: string }>;
}

export default function JobDetailPage({ params }: JobDetailPageProps) {
  const { id } = use(params);
  const { data: job, isLoading } = useJob(id);

  if (isLoading || !job) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <JobDetailHeader job={job} />

      {job.status === "failed" && job.error_message && <JobErrorPanel message={job.error_message} />}

      <div className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Chapters</h2>
        <ChapterGrid totalChapters={job.total_chapters} chapters={job.chapters} />
      </div>

      <div className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Parts</h2>
        <PartList parts={job.parts} />
      </div>
    </div>
  );
}
