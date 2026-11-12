"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { VoiceRecorderDialog } from "@/components/voices/voice-recorder-dialog";
import { useClonedVoices, useCloneOptions } from "@/hooks/use-clone-voices";
import { deleteClonedVoice } from "@/lib/api/voices";
import type { ClonedVoice } from "@/types/voice";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export default function VoicesPage() {
  const queryClient = useQueryClient();
  const { data: options, isLoading: optionsLoading } = useCloneOptions();
  const { data: voices, isLoading: voicesLoading } = useClonedVoices();
  const [recording, setRecording] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<ClonedVoice | null>(null);
  const [deleting, setDeleting] = useState(false);

  const workerOnline = options?.clone_worker_online ?? false;
  const atLimit = options !== null && options !== undefined && (voices?.length ?? 0) >= options.max_voices;

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ["cloned-voices"] });
    queryClient.invalidateQueries({ queryKey: ["voices"] });
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    const voiceId = pendingDelete.id.replace(/^clone:/, "");
    const { error } = await deleteClonedVoice(voiceId);
    setDeleting(false);
    if (error) {
      toast.error(error);
      return;
    }
    toast.success(`Deleted "${pendingDelete.name}" and its recording.`);
    setPendingDelete(null);
    refresh();
  }

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold">My voices</h1>
          <p className="max-w-xl text-sm text-muted-foreground">
            Record your own voice once, then pick it as the narrator or for a character in any
            audiobook. Only clone your own voice.
          </p>
        </div>
        <Button
          onClick={() => setRecording(true)}
          disabled={!options || atLimit}
          title={atLimit ? "Delete a voice to record another" : undefined}
        >
          Record a new voice
        </Button>
      </div>

      {!optionsLoading && options && !workerOnline && (
        <Alert>
          <AlertTitle>Cloned voices need your local worker</AlertTitle>
          <AlertDescription>
            Your voice is made on a computer with a graphics card running the book2audio local
            worker, and none is online right now. You can still record and save voices here;
            they become available as soon as the worker is running. The built-in voices work as
            usual.
          </AlertDescription>
        </Alert>
      )}

      {voicesLoading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : voices && voices.length > 0 ? (
        <ul className="flex flex-col gap-3">
          {voices.map((voice) => (
            <li key={voice.id}>
              <Card>
                <CardHeader>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <CardTitle>{voice.name}</CardTitle>
                    <Badge variant={workerOnline ? "secondary" : "outline"}>
                      {workerOnline ? "Ready to use" : "Needs your local worker"}
                    </Badge>
                  </div>
                  <CardDescription>
                    {voice.gender === "f" ? "Female" : "Male"} voice · recorded{" "}
                    {formatDate(voice.created_at)} · {Math.round(voice.duration_s)} seconds
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-wrap items-center justify-between gap-3">
                  {voice.recording_url ? (
                    <audio controls src={voice.recording_url} className="max-w-full" />
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      The recording is not available to play right now.
                    </span>
                  )}
                  <Button variant="outline" size="sm" onClick={() => setPendingDelete(voice)}>
                    Delete
                  </Button>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>You have no cloned voices yet</CardTitle>
            <CardDescription>
              It takes about a minute: read a short passage into your microphone, check the
              recording, and save it.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {options && (
        <VoiceRecorderDialog
          // A fresh dialog each time, so a cancelled recording never leaks into the next.
          key={String(recording)}
          open={recording}
          onOpenChange={setRecording}
          options={options}
          onSaved={refresh}
        />
      )}

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && !deleting && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this voice?</AlertDialogTitle>
            <AlertDialogDescription>
              &ldquo;{pendingDelete?.name}&rdquo; and its recording are removed for good. Jobs that
              are still waiting to use it will be read by a built-in voice instead.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Keep it</AlertDialogCancel>
            <AlertDialogAction onClick={confirmDelete} disabled={deleting}>
              {deleting ? "Deleting…" : "Delete voice"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
