"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { previewFilenameTemplate, validateFilenameTemplate } from "@/lib/validation/filename-template";

interface FilenameTemplateInputProps {
  value: string;
  onChange: (value: string) => void;
  title: string;
  batchSize: number;
}

export function FilenameTemplateInput({ value, onChange, title, batchSize }: FilenameTemplateInputProps) {
  const error = value ? validateFilenameTemplate(value, batchSize) : "Filename template is required";
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
        <code>{"{part:02d}"}</code> (zero-padded), <code>{"{chapter}"}</code> and{" "}
        <code>{"{chapter:03d}"}</code> (the book&apos;s chapter number, or the first chapter of
        a part), and <code>{"{chapter_title}"}</code> (only with one file per chapter).
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
