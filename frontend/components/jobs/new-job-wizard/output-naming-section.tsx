"use client";

import { FilenameTemplateInput } from "@/components/jobs/new-job-wizard/filename-template-input";
import type { JobConfig } from "@/types/job";

interface OutputNamingSectionProps {
  config: JobConfig;
  title: string;
  onChange: (patch: Partial<JobConfig>) => void;
}

export function OutputNamingSection({ config, title, onChange }: OutputNamingSectionProps) {
  return (
    <div className="flex flex-col gap-4">
      <FilenameTemplateInput
        value={config.filename_template ?? ""}
        onChange={(value) => onChange({ filename_template: value })}
        title={title}
      />
    </div>
  );
}
