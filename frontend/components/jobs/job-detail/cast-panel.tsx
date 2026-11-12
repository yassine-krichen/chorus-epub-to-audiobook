import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  evidenceSummary,
  genderLabel,
  isReadByNarrator,
  summarizeCast,
} from "@/lib/jobs/cast-display";
import { labelForVoiceId } from "@/lib/voices/voice-display";
import type { CastCharacter, CastReport } from "@/types/job";
import type { VoiceInfo } from "@/types/voice";

/** Says so when a built-in voice stood in for the user's cloned one, and why. */
function CloneFallbackNotice({ report, voices }: { report: CastReport; voices: VoiceInfo[] }) {
  const fallbacks = report.clone_fallbacks ?? [];
  if (fallbacks.length === 0) return null;
  return (
    <Alert>
      <AlertTitle>A built-in voice read instead of your cloned voice</AlertTitle>
      <AlertDescription>
        <ul className="flex flex-col gap-1">
          {fallbacks.map((fallback) => (
            <li key={fallback.voice}>
              {labelForVoiceId(voices, fallback.voice)} was read by {fallback.fallback_voice}:{" "}
              {fallback.reason}.
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

function GenderBadge({ character }: { character: CastCharacter }) {
  return (
    <Badge variant={character.gender === null ? "outline" : "secondary"}>
      {genderLabel(character.gender)}
    </Badge>
  );
}

function VoiceCell({
  character,
  narratorVoice,
  voices,
}: {
  character: CastCharacter;
  narratorVoice: string;
  voices: VoiceInfo[];
}) {
  const narrator = isReadByNarrator(character, narratorVoice);
  return (
    <div className="flex flex-col">
      <span className="font-mono text-xs">{labelForVoiceId(voices, character.voice)}</span>
      {narrator && <span className="text-xs text-muted-foreground">Narrator reads this character</span>}
      {!narrator && character.voice_source === "override" && (
        <span className="text-xs text-muted-foreground">Your choice</span>
      )}
    </div>
  );
}

/** Who speaks in the chapters being rendered, how often, and which voice reads each of
 * them. Characters the app is not sure about come first, so a quick look is enough. */
export function CastPanel({ report, voices = [] }: { report: CastReport; voices?: VoiceInfo[] }) {
  const fallbacks = <CloneFallbackNotice report={report} voices={voices} />;
  if (report.voice_mode === "narrator_only") {
    return (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          The narrator ({labelForVoiceId(voices, report.narrator_voice)}) reads every line,
          dialogue included. No character voices were used for this job.
        </p>
        {fallbacks}
      </div>
    );
  }

  if (report.characters.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No character dialogue was found in the chapters for this job, so only the narrator
        speaks.
      </p>
    );
  }

  const summary = summarizeCast(report);

  return (
    <div className="flex flex-col gap-3">
      {fallbacks}
      <p className="text-sm text-muted-foreground">
        {summary.speakers} {summary.speakers === 1 ? "character speaks" : "characters speak"}.
        {summary.notSure > 0 && (
          <>
            {" "}
            For {summary.notSure} of them we are not sure of the gender, so the narrator (
            {report.narrator_voice}) reads their lines. We only give a character a male or female
            voice when the text makes it clear.
          </>
        )}
      </p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Character</TableHead>
            <TableHead className="text-right">Lines</TableHead>
            <TableHead>Gender</TableHead>
            <TableHead>Voice</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {report.characters.map((character) => (
            <TableRow key={character.name}>
              <TableCell className="max-w-64 whitespace-normal">
                <div className="font-medium">{character.name}</div>
                {character.sample_line && (
                  <div className="truncate text-xs text-muted-foreground">
                    &ldquo;{character.sample_line}&rdquo;
                  </div>
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums">{character.lines}</TableCell>
              <TableCell>
                <div className="flex flex-col items-start gap-1">
                  <GenderBadge character={character} />
                  <span className="text-xs text-muted-foreground">
                    {evidenceSummary(character.layer_counts)}
                  </span>
                </div>
              </TableCell>
              <TableCell>
                <VoiceCell
                  character={character}
                  narratorVoice={report.narrator_voice}
                  voices={voices}
                />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
