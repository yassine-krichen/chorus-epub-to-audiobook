"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { previewFilenameTemplate, validateFilenameTemplate } from "@/lib/validation/filename-template";

interface FilenameTemplateInputProps {
  value: string;
  onChange: (value: string) => void;
  title: string;
}

export function FilenameTemplateInput({ value, onChange, title }: FilenameTemplateInputProps) {
  const error = value ? validateFilenameTemplate(value) : "Filename template is required";
  const preview = error ? null : previewFilenameTemplate(value, title, 1);

  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor="filename-template">Filename template</Label>
      <Input
        id="filename-template"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={Boolean(error)}
      />
      <p className="text-xs text-muted-foreground">
        Placeholders: <code>{"{title}"}</code>, <code>{"{part}"}</code>,{" "}
        <code>{"{part:02d}"}</code> (zero-padded).
      </p>
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : (
        <p className="text-xs text-muted-foreground">
          Preview: <span className="font-mono">{preview}.m4b</span>
        </p>
      )}
    </div>
  );
}
