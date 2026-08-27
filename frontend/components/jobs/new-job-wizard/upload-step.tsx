"use client";

import { useRef } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import type { UploadState } from "@/hooks/use-upload-epub";

interface UploadStepProps {
  title: string;
  onTitleChange: (title: string) => void;
  uploadState: UploadState;
  uploadProgress: number;
  uploadError: string | null;
  onFileSelected: (file: File) => void;
}

export function UploadStep({
  title,
  onTitleChange,
  uploadState,
  uploadProgress,
  uploadError,
  onFileSelected,
}: UploadStepProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  function handleFile(file: File | undefined) {
    if (!file) return;
    if (!title) {
      onTitleChange(file.name.replace(/\.epub$/i, ""));
    }
    onFileSelected(file);
  }

  return (
    <div className="flex flex-col gap-4">
      <div
        className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed p-8 text-center hover:bg-muted/50"
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          handleFile(event.dataTransfer.files[0]);
        }}
      >
        <p className="text-sm font-medium">Drop an epub here, or click to browse</p>
        <p className="text-xs text-muted-foreground">Max 50MB</p>
        <input
          ref={inputRef}
          type="file"
          accept=".epub"
          className="hidden"
          onChange={(event) => handleFile(event.target.files?.[0])}
        />
      </div>

      {uploadState === "uploading" && (
        <div className="flex items-center gap-2">
          <Progress value={uploadProgress} className="flex-1" />
          <span className="text-xs text-muted-foreground">{uploadProgress}%</span>
        </div>
      )}
      {uploadState === "done" && <p className="text-xs text-green-600">Upload complete.</p>}
      {uploadState === "error" && <p className="text-xs text-destructive">{uploadError}</p>}

      <div className="flex flex-col gap-2">
        <Label htmlFor="title">Title</Label>
        <Input id="title" value={title} onChange={(event) => onTitleChange(event.target.value)} required />
      </div>
    </div>
  );
}
