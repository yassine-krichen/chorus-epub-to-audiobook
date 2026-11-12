"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { VoicePicker } from "@/components/voices/voice-picker";
import { useVoices } from "@/hooks/use-voices";
import { cancelJob, confirmCastReview } from "@/lib/api/jobs";
import { evidenceSummary, genderLabel, reviewCastState } from "@/lib/jobs/cast-display";
import {
  buildReviewRequest,
  canBeReplaced,
  changeCount,
  draftFor,
  formatDeadline,
  mergeTargets,
  overrideLimitProblem,
  treatAsToValue,
  updateDraft,
  valueToTreatAs,
  type DraftMap,
} from "@/lib/jobs/cast-review";
import { cloneProblemForConfig, labelForVoiceId } from "@/lib/voices/voice-display";
import type { CastCharacter, CastReport, JobDetail } from "@/types/job";
import type { VoiceInfo } from "@/types/voice";

const GENDER_ITEMS: Record<"m" | "f" | "unsure", string> = {
  m: "Male",
  f: "Female",
  unsure: "Not sure",
};

interface CharacterRowProps {
  character: CastCharacter;
  report: CastReport;
  drafts: DraftMap;
  voices: VoiceInfo[];
  disabled: boolean;
  onChange: (name: string, patch: Parameters<typeof updateDraft>[2]) => void;
}

function CharacterRow({ character, report, drafts, voices, disabled, onChange }: CharacterRowProps) {
  const draft = draftFor(drafts, character.name);
  const replaced = draft.treatAs.kind !== "own";
  const targets = mergeTargets(report, drafts, character.name);
  const allowNarrator = canBeReplaced(drafts, character.name);
  const shownGender = draft.gender ?? character.gender ?? "unsure";
  const automaticVoice =
    character.voice_source === "narrator"
      ? "narrator voice"
      : labelForVoiceId(voices, character.voice);

  return (
    <li className="flex flex-col gap-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="font-medium">{character.name}</span>
          {character.sample_line && (
            <span className="truncate text-xs text-muted-foreground">
              &ldquo;{character.sample_line}&rdquo;
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>
            {character.lines} {character.lines === 1 ? "line" : "lines"}
          </span>
          <Badge variant={character.gender === null ? "outline" : "secondary"}>
            Detected: {genderLabel(character.gender)}
          </Badge>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        {character.gender === null
          ? "We are not sure of this character's gender, so the narrator reads their lines unless you say otherwise. "
          : ""}
        Based on: {evidenceSummary(character.layer_counts)}.
      </p>

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label>Gender</Label>
          <Select
            value={shownGender}
            disabled={disabled || replaced}
            onValueChange={(value) =>
              onChange(character.name, { gender: value === "m" || value === "f" ? value : null })
            }
          >
            <SelectTrigger className="w-full" aria-label={`Gender for ${character.name}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="m">{GENDER_ITEMS.m}</SelectItem>
              <SelectItem value="f">{GENDER_ITEMS.f}</SelectItem>
              {character.gender === null && <SelectItem value="unsure">{GENDER_ITEMS.unsure}</SelectItem>}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Voice</Label>
          <VoicePicker
            disabled={disabled || replaced}
            voices={voices}
            value={draft.voice}
            onChange={(voice) => onChange(character.name, { voice })}
            allowNoPreference
            noPreferenceLabel={`Automatic (${automaticVoice})`}
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Who is this?</Label>
          <Select
            value={treatAsToValue(draft.treatAs)}
            disabled={disabled}
            onValueChange={(value) => onChange(character.name, { treatAs: valueToTreatAs(value ?? "own") })}
          >
            <SelectTrigger className="w-full" aria-label={`Who ${character.name} is`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="own">A character of their own</SelectItem>
              {allowNarrator && <SelectItem value="narrator">Narrator reads these lines</SelectItem>}
              {targets.map((target) => (
                <SelectItem key={target} value={`merge:${target}`}>
                  Same person as {target}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
    </li>
  );
}

interface CastReviewPanelProps {
  job: JobDetail;
  report: CastReport | null | undefined;
  /** True once the cast has been asked for at least once, so "no report" means missing. */
  settled: boolean;
}

/** The cast report of a job waiting for review could not be found: say so and offer the
 * only way out, instead of a placeholder that never goes away. */
function MissingCast({ job }: { job: JobDetail }) {
  const queryClient = useQueryClient();
  const [canceling, setCanceling] = useState(false);

  async function handleCancel() {
    setCanceling(true);
    const { error } = await cancelJob(job.id);
    setCanceling(false);
    if (error) {
      toast.error(error);
      return;
    }
    toast.success("Job canceled.");
    queryClient.invalidateQueries({ queryKey: ["job", job.id] });
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>We could not load the detected cast</CardTitle>
        <CardDescription>
          This job is waiting for you to check the cast, but the list of characters is not
          available any more. Cancel the job and create it again to start over.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="destructive" onClick={handleCancel} disabled={canceling}>
          {canceling ? "Canceling…" : "Cancel job"}
        </Button>
      </CardContent>
    </Card>
  );
}

/** The pause between "we found the cast" and "we make the audio": the user checks who
 * speaks, fixes what is wrong, and starts the render. */
export function CastReviewPanel({ job, report, settled }: CastReviewPanelProps) {
  const queryClient = useQueryClient();
  const { data: voices } = useVoices();
  const [drafts, setDrafts] = useState<DraftMap>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const view = reviewCastState(report, settled);
  if (view === "missing") return <MissingCast job={job} />;
  if (!report || !voices) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  const changes = changeCount(report, drafts);
  const limitProblem = overrideLimitProblem(report, drafts, job.config);
  // A cloned voice picked here has to be usable now, as it is when creating a job.
  const pickedVoices = Object.fromEntries(
    Object.entries(drafts).flatMap(([name, draft]) => (draft.voice ? [[name, draft.voice]] : []))
  );
  const cloneProblem = cloneProblemForConfig(
    { narrator_voice: null, voice_overrides: pickedVoices },
    voices.voices
  );
  const deadline = formatDeadline(job.cast_review_expires_at);
  const unsure = report.characters.filter((c) => c.gender === null).length;
  const omitted = report.omitted_characters ?? 0;

  async function handleStart() {
    if (!report) return;
    setSubmitting(true);
    setError(null);
    const { error: apiError } = await confirmCastReview(job.id, buildReviewRequest(report, drafts));
    setSubmitting(false);
    if (apiError) {
      setError(apiError);
      toast.error(apiError);
      return;
    }
    toast.success("Rendering started.");
    queryClient.invalidateQueries({ queryKey: ["job", job.id] });
    queryClient.invalidateQueries({ queryKey: ["job-cast", job.id] });
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Check the cast before we make the audio</CardTitle>
        <CardDescription>
          We found {report.characters.length}{" "}
          {report.characters.length === 1 ? "character" : "characters"} who speak.
          {unsure > 0 &&
            ` ${unsure} of them ${unsure === 1 ? "has" : "have"} no clear gender, so the narrator reads ${unsure === 1 ? "that character" : "them"} unless you say otherwise.`}{" "}
          Fix anyone we got wrong, then start rendering. Nothing is rendered until you do.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <ul className="flex flex-col gap-3">
          {report.characters.map((character) => (
            <CharacterRow
              key={character.name}
              character={character}
              report={report}
              drafts={drafts}
              voices={voices.voices}
              disabled={submitting}
              onChange={(name, patch) => setDrafts((current) => updateDraft(current, name, patch))}
            />
          ))}
        </ul>

        {omitted > 0 && (
          <p className="text-xs text-muted-foreground">
            {omitted} more {omitted === 1 ? "character is" : "characters are"} not listed (very
            quiet, or with a name too long to edit). They keep the voice we chose.
          </p>
        )}
        {limitProblem && <p className="text-sm text-destructive">{limitProblem}</p>}
        {cloneProblem && <p className="text-sm text-destructive">{cloneProblem}</p>}
        {error && <p className="text-sm text-destructive">{error}</p>}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            {deadline
              ? `Waiting for you. If you do not start rendering by ${deadline}, this job is canceled and nothing is rendered.`
              : "Waiting for you. A job that is never confirmed is canceled after a while."}
          </p>
          <Button
            onClick={handleStart}
            disabled={submitting || limitProblem !== null || cloneProblem !== null}
          >
            {submitting
              ? "Starting…"
              : changes > 0
                ? `Apply ${changes} ${changes === 1 ? "fix" : "fixes"} and start rendering`
                : "Looks right, start rendering"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
