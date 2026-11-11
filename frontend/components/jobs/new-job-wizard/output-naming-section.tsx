"use client";

import { FilenameTemplateInput } from "@/components/jobs/new-job-wizard/filename-template-input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { oneFilePerChapterPatch } from "@/lib/jobs/one-file-per-chapter";
import type { JobConfig } from "@/types/job";

interface OutputNamingSectionProps {
  config: JobConfig;
  title: string;
  onChange: (patch: Partial<JobConfig>) => void;
}

export function OutputNamingSection({ config, title, onChange }: OutputNamingSectionProps) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4 rounded-lg border p-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="one-file-per-chapter-switch">One file per chapter</Label>
          <p className="text-xs text-muted-foreground">
            Each chapter becomes its own audio file, named after the chapter, for example{" "}
            <span className="font-mono">001 - Nightmare Begins.m4b</span>. Turn it off to bundle
            chapters into larger parts.
          </p>
        </div>
        <Switch
          id="one-file-per-chapter-switch"
          checked={config.batch_size === 1}
          onCheckedChange={(checked) => onChange(oneFilePerChapterPatch(checked, config))}
        />
      </div>

      <FilenameTemplateInput
        value={config.filename_template ?? ""}
        onChange={(value) => onChange({ filename_template: value })}
        title={title}
        batchSize={config.batch_size}
      />
    </div>
  );
}
