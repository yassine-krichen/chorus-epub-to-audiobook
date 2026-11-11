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
import type { CastCharacter, CastReport } from "@/types/job";

function GenderBadge({ character }: { character: CastCharacter }) {
  return (
    <Badge variant={character.gender === null ? "outline" : "secondary"}>
      {genderLabel(character.gender)}
    </Badge>
  );
}

function VoiceCell({ character, narratorVoice }: { character: CastCharacter; narratorVoice: string }) {
  const narrator = isReadByNarrator(character, narratorVoice);
  return (
    <div className="flex flex-col">
      <span className="font-mono text-xs">{character.voice}</span>
      {narrator && <span className="text-xs text-muted-foreground">Narrator reads this character</span>}
      {!narrator && character.voice_source === "override" && (
        <span className="text-xs text-muted-foreground">Your choice</span>
      )}
    </div>
  );
}

/** Who speaks in the chapters being rendered, how often, and which voice reads each of
 * them. Characters the app is not sure about come first, so a quick look is enough. */
export function CastPanel({ report }: { report: CastReport }) {
  if (report.voice_mode === "narrator_only") {
    return (
      <p className="text-sm text-muted-foreground">
        The narrator ({report.narrator_voice}) reads every line, dialogue included. No character
        voices were used for this job.
      </p>
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
                <VoiceCell character={character} narratorVoice={report.narrator_voice} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
