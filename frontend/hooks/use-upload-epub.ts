"use client";

import { useCallback, useState } from "react";

import { requestUploadUrl } from "@/lib/api/jobs";

// Matches the backend's documented (but not server-enforced, see
// book2audio.api.jobs.routes.MAX_UPLOAD_BYTES) upload cap.
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

export type UploadState = "idle" | "uploading" | "done" | "error";

interface UploadResult {
  state: UploadState;
  progress: number;
  objectKey: string | null;
  error: string | null;
  upload: (file: File) => Promise<void>;
  reset: () => void;
}

/** Puts the epub file directly to R2 against a short-lived presigned URL,
 * using XHR (not fetch) so upload progress events are available. */
export function useUploadEpub(): UploadResult {
  const [state, setState] = useState<UploadState>("idle");
  const [progress, setProgress] = useState(0);
  const [objectKey, setObjectKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reset = useCallback(() => {
    setState("idle");
    setProgress(0);
    setObjectKey(null);
    setError(null);
  }, []);

  const upload = useCallback(async (file: File) => {
    if (file.size > MAX_UPLOAD_BYTES) {
      setState("error");
      setError("File is larger than the 50MB upload limit.");
      return;
    }

    setState("uploading");
    setProgress(0);
    setError(null);

    const { data, error: urlError } = await requestUploadUrl(file.name);
    if (!data) {
      setState("error");
      setError(urlError ?? "Could not get an upload URL.");
      return;
    }

    try {
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", data.upload_url);
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            setProgress(Math.round((event.loaded / event.total) * 100));
          }
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve();
          } else {
            reject(new Error(`Upload failed (${xhr.status})`));
          }
        };
        xhr.onerror = () => reject(new Error("Upload failed — network error."));
        xhr.send(file);
      });
    } catch (uploadError) {
      setState("error");
      setError(uploadError instanceof Error ? uploadError.message : "Upload failed.");
      return;
    }

    setObjectKey(data.object_key);
    setProgress(100);
    setState("done");
  }, []);

  return { state, progress, objectKey, error, upload, reset };
}
