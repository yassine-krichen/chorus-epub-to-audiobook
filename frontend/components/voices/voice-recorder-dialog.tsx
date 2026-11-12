"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useVoiceRecorder } from "@/hooks/use-voice-recorder";
import { createClonedVoice, requestCloneUploadUrl } from "@/lib/api/voices";
import { encodeWav } from "@/lib/voices/recording-quality";
import type { CloneOptions } from "@/types/voice";

interface VoiceRecorderDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  options: CloneOptions;
  onSaved: () => void;
}

const GENDER_LABELS: Record<"m" | "f", string> = { m: "Male", f: "Female" };

function formatSeconds(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

/** Record, check, and save your own voice. The steps are: read the passage aloud, review the
 * recording's quality, then name the voice and confirm it is yours. */
export function VoiceRecorderDialog({
  open,
  onOpenChange,
  options,
  onSaved,
}: VoiceRecorderDialogProps) {
  const recorder = useVoiceRecorder();
  const [scriptId, setScriptId] = useState(options.scripts[0]?.id ?? "");
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [gender, setGender] = useState<"m" | "f" | null>(null);
  const [consent, setConsent] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const script = options.scripts.find((candidate) => candidate.id === scriptId);
  const { recording } = recorder;

  function close(next: boolean) {
    if (saving) return;
    if (!next) {
      recorder.reset();
      setNaming(false);
      setSaveError(null);
    }
    onOpenChange(next);
  }

  function recordAgain() {
    recorder.reset();
    setNaming(false);
    setSaveError(null);
    void recorder.start();
  }

  async function save() {
    if (!recording || !script || gender === null) return;
    setSaving(true);
    setSaveError(null);
    const { data: upload, error: urlError } = await requestCloneUploadUrl();
    if (!upload) {
      setSaving(false);
      setSaveError(urlError ?? "Could not start the upload.");
      return;
    }
    try {
      const wav = encodeWav(recording.samples, recording.sampleRate);
      const response = await fetch(upload.upload_url, {
        method: "PUT",
        body: new Blob([wav], { type: "audio/wav" }),
      });
      if (!response.ok) throw new Error(`Upload failed (${response.status})`);
    } catch {
      setSaving(false);
      setSaveError("The upload failed. Check your connection and try again.");
      return;
    }
    const { error } = await createClonedVoice({
      voice_id: upload.voice_id,
      name: name.trim(),
      gender,
      script_id: script.id,
      consent,
      consent_version: options.consent_version,
    });
    setSaving(false);
    if (error) {
      setSaveError(error);
      return;
    }
    toast.success(`Saved "${name.trim()}".`);
    recorder.reset();
    setNaming(false);
    setName("");
    setGender(null);
    setConsent(false);
    onSaved();
    onOpenChange(false);
  }

  const canSave = name.trim().length > 0 && gender !== null && consent && !saving;

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-xl">
        {recorder.state === "recorded" && recording && naming ? (
          <>
            <DialogHeader>
              <DialogTitle>Name your voice</DialogTitle>
              <DialogDescription>
                Almost done. Give the voice a name you will recognise in the voice lists.
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="voice-name">Name</Label>
                <Input
                  id="voice-name"
                  value={name}
                  maxLength={80}
                  placeholder="My narrator voice"
                  onChange={(event) => setName(event.target.value)}
                  disabled={saving}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>Which kind of voice is it?</Label>
                <Select
                  value={gender ?? ""}
                  onValueChange={(value) => setGender(value === "m" || value === "f" ? value : null)}
                  disabled={saving}
                >
                  <SelectTrigger className="w-full" aria-label="Kind of voice">
                    <SelectValue placeholder="Pick one">
                      {(value: string | null) =>
                        value === "m" || value === "f" ? GENDER_LABELS[value] : "Pick one"
                      }
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="m">{GENDER_LABELS.m}</SelectItem>
                    <SelectItem value="f">{GENDER_LABELS.f}</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  If your voice cannot be made for a job, a built-in voice of this kind reads
                  instead, so the audiobook is never left unfinished.
                </p>
              </div>
              <div className="flex items-start gap-3 rounded-lg border p-3">
                <Checkbox
                  id="voice-consent"
                  checked={consent}
                  onCheckedChange={(checked) => setConsent(checked === true)}
                  disabled={saving}
                />
                <Label htmlFor="voice-consent" className="text-sm leading-snug font-normal">
                  {options.consent_text}
                </Label>
              </div>
              {saveError && (
                <Alert variant="destructive">
                  <AlertTitle>We could not save your voice</AlertTitle>
                  <AlertDescription>{saveError}</AlertDescription>
                </Alert>
              )}
              {saving && <Progress value={null} aria-label="Saving your voice" />}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setNaming(false)} disabled={saving}>
                Back
              </Button>
              <Button onClick={save} disabled={!canSave}>
                {saving ? "Saving…" : "Save my voice"}
              </Button>
            </DialogFooter>
          </>
        ) : recorder.state === "recorded" && recording ? (
          <>
            <DialogHeader>
              <DialogTitle>
                {recording.analysis.passed ? "Sounds good. Have a listen" : "Let's try that again"}
              </DialogTitle>
              <DialogDescription>
                {recording.analysis.passed
                  ? "Play it back. If it sounds clear and natural, continue."
                  : "A few things would make the copy of your voice worse. Fix them and record again."}
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-4">
              {/* A native player: the shadcn kit has no audio component. */}
              <audio controls src={recording.objectUrl} className="w-full" />
              <ul className="flex flex-col gap-2" aria-label="Recording checks">
                {recording.analysis.checks.map((check) => (
                  <li key={check.id} className="flex flex-col text-sm">
                    <span className={check.passed ? "" : "font-medium text-destructive"}>
                      {check.passed ? "✓" : "✗"} {check.label}
                    </span>
                    {!check.passed && (
                      <span className="pl-5 text-xs text-muted-foreground">{check.advice}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={recordAgain}>
                Record again
              </Button>
              <Button onClick={() => setNaming(true)} disabled={!recording.analysis.passed}>
                Continue
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>Record your voice</DialogTitle>
              <DialogDescription>
                Record 20 to 30 seconds in a quiet room. Read the passage below out loud, in your
                natural voice and at your normal pace, as if telling it to a friend.
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-4">
              <p className="rounded-lg border bg-muted/40 p-4 text-base leading-relaxed">
                {script?.text}
              </p>
              {recorder.state === "recording" ? (
                <div className="flex flex-col gap-2" aria-live="polite">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium">Recording… keep reading</span>
                    <span className="font-mono">{formatSeconds(recorder.elapsedS)} / 0:30</span>
                  </div>
                  <Progress value={recorder.level * 100} aria-label="Microphone level" />
                  <p className="text-xs text-muted-foreground">
                    If the bar does not move when you talk, your microphone is not picking you up.
                  </p>
                </div>
              ) : recorder.state === "processing" ? (
                <p className="text-sm text-muted-foreground">Checking your recording…</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {options.scripts.length > 1 && (
                    <div className="flex flex-col gap-1.5">
                      <Label>Passage</Label>
                      <Select
                        value={scriptId}
                        onValueChange={(value) => setScriptId(value ?? scriptId)}
                        disabled={recorder.state === "requesting"}
                      >
                        <SelectTrigger className="w-full" aria-label="Passage to read">
                          <SelectValue>
                            {(value: string | null) => {
                              const index = options.scripts.findIndex((s) => s.id === value);
                              return `Passage ${index + 1}`;
                            }}
                          </SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {options.scripts.map((candidate, index) => (
                            <SelectItem key={candidate.id} value={candidate.id}>
                              Passage {index + 1}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  )}
                  <ul className="list-disc pl-5 text-xs text-muted-foreground">
                    <li>Close doors and windows, and turn off fans and music.</li>
                    <li>Sit about a hand&apos;s width from the microphone.</li>
                    <li>If you stumble, just carry on. You can record again afterwards.</li>
                  </ul>
                </div>
              )}
              {recorder.error && (
                <Alert variant="destructive">
                  <AlertTitle>We could not record</AlertTitle>
                  <AlertDescription>{recorder.error}</AlertDescription>
                </Alert>
              )}
            </div>
            <DialogFooter>
              {recorder.state === "recording" ? (
                <Button onClick={recorder.stop}>Stop recording</Button>
              ) : (
                <Button
                  onClick={() => void recorder.start()}
                  disabled={recorder.state === "requesting" || recorder.state === "processing"}
                >
                  {recorder.state === "requesting"
                    ? "Waiting for the microphone…"
                    : recorder.state === "error"
                      ? "Try again"
                      : "Start recording"}
                </Button>
              )}
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
