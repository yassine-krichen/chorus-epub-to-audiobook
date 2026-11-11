"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AudioProductionSection } from "@/components/jobs/new-job-wizard/audio-production-section";
import { ChaptersSection } from "@/components/jobs/new-job-wizard/chapters-section";
import { OutputNamingSection } from "@/components/jobs/new-job-wizard/output-naming-section";
import { UploadStep } from "@/components/jobs/new-job-wizard/upload-step";
import { VoiceCastingSection } from "@/components/jobs/new-job-wizard/voice-casting-section";
import { useBookInspection } from "@/hooks/use-book-inspection";
import { useUploadEpub } from "@/hooks/use-upload-epub";
import { createJob } from "@/lib/api/jobs";
import { inspectionStateOf } from "@/lib/jobs/inspection-state";
import { validateFilenameTemplate } from "@/lib/validation/filename-template";
import { DEFAULT_JOB_CONFIG, type JobConfig } from "@/types/job";

export function NewJobWizard() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [config, setConfig] = useState<JobConfig>(DEFAULT_JOB_CONFIG);
  const [submitting, setSubmitting] = useState(false);
  const upload = useUploadEpub();
  // Only a finished upload can be read; a new file replaces the book being listed.
  const inspectionQuery = useBookInspection(upload.state === "done" ? upload.objectKey : null);
  const inspection = inspectionStateOf(upload.state === "done", inspectionQuery);

  function updateConfig(patch: Partial<JobConfig>) {
    setConfig((current) => ({ ...current, ...patch }));
  }

  function handleFileSelected(file: File) {
    // The chapters picked for the previous book mean nothing for this one.
    updateConfig({ start_chapter: null, chapter_limit: 0 });
    upload.upload(file);
  }

  const filenameError = config.filename_template
    ? validateFilenameTemplate(config.filename_template, config.batch_size)
    : "Filename template is required";
  const canSubmit =
    upload.state === "done" && Boolean(upload.objectKey) && title.trim().length > 0 && !filenameError;

  async function handleSubmit() {
    if (!upload.objectKey) return;
    setSubmitting(true);
    const { data, error } = await createJob(upload.objectKey, title.trim(), config);
    setSubmitting(false);
    if (error) {
      toast.error(error);
      return;
    }
    toast.success("Job created.");
    router.push(`/jobs/${data?.job_id}`);
  }

  return (
    <div className="flex flex-col gap-6">
      <UploadStep
        title={title}
        onTitleChange={setTitle}
        uploadState={upload.state}
        uploadProgress={upload.progress}
        uploadError={upload.error}
        onFileSelected={handleFileSelected}
      />

      <Card>
        <CardContent>
          <Tabs defaultValue="voice">
            <TabsList>
              <TabsTrigger value="voice">Voice Casting</TabsTrigger>
              <TabsTrigger value="chapters">Chapters</TabsTrigger>
              <TabsTrigger value="audio">Audio Production</TabsTrigger>
              <TabsTrigger value="output">Output &amp; Naming</TabsTrigger>
            </TabsList>
            <TabsContent value="voice">
              <VoiceCastingSection config={config} onChange={updateConfig} />
            </TabsContent>
            <TabsContent value="chapters">
              <ChaptersSection config={config} inspection={inspection} onChange={updateConfig} />
            </TabsContent>
            <TabsContent value="audio">
              <AudioProductionSection config={config} onChange={updateConfig} />
            </TabsContent>
            <TabsContent value="output">
              <OutputNamingSection config={config} title={title} onChange={updateConfig} />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      <Button onClick={handleSubmit} disabled={!canSubmit || submitting} className="self-end">
        {submitting ? "Creating job…" : "Create job"}
      </Button>
    </div>
  );
}
