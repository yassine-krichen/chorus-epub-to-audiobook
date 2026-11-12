"use client";

import Link from "next/link";
import { useState } from "react";

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { VoicePicker } from "@/components/voices/voice-picker";
import { useVoices } from "@/hooks/use-voices";
import {
  attributorForSwitch,
  isAiSpeakerDetection,
  voiceModePatch,
} from "@/lib/jobs/ai-speaker-detection";
import {
  listCharacterOverrides,
  removeCharacterOverride,
  setCharacterOverride,
  type OverrideGender,
} from "@/lib/jobs/character-overrides";
import {
  builtInVoices,
  cloneProblemForConfig,
  clonedVoices,
  labelForVoiceId,
} from "@/lib/voices/voice-display";
import type { JobConfig } from "@/types/job";

interface VoiceCastingSectionProps {
  config: JobConfig;
  onChange: (patch: Partial<JobConfig>) => void;
}

const GENDER_NOT_SET = "not-set";
const GENDER_CHOICES: Record<string, string> = {
  [GENDER_NOT_SET]: "Work it out from the text",
  m: "Male",
  f: "Female",
};

function genderChoice(value: string | null): OverrideGender | null {
  return value === "m" || value === "f" ? value : null;
}

export function VoiceCastingSection({ config, onChange }: VoiceCastingSectionProps) {
  const { data: voices, isLoading } = useVoices();
  const [overrideName, setOverrideName] = useState("");
  const [overrideGender, setOverrideGender] = useState<string>(GENDER_NOT_SET);
  const [overrideVoice, setOverrideVoice] = useState<string | null>(null);
  const [overrideError, setOverrideError] = useState<string | null>(null);

  if (isLoading || !voices) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
      </div>
    );
  }

  // The male/female pickers bias casting among the built-in voices only; a cloned voice is
  // chosen as the narrator or for a named character.
  const builtIn = builtInVoices(voices.voices);
  const maleVoices = builtIn.filter((v) => v.gender === "male" && !v.is_default_narrator);
  const femaleVoices = builtIn.filter((v) => v.gender === "female" && !v.is_default_narrator);
  const hasClonedVoices = clonedVoices(voices.voices).length > 0;
  const cloneProblem = cloneProblemForConfig(config, voices.voices);
  const overrides = listCharacterOverrides(config);
  const narratorOnly = config.voice_mode === "narrator_only";

  function addOverride() {
    const result = setCharacterOverride(config, overrideName, {
      gender: genderChoice(overrideGender),
      voice: overrideVoice,
    });
    if (!result.ok) {
      setOverrideError(result.error);
      return;
    }
    onChange(result.patch);
    setOverrideName("");
    setOverrideGender(GENDER_NOT_SET);
    setOverrideVoice(null);
    setOverrideError(null);
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4 rounded-lg border p-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="narrator-only-switch">Narrator reads everything (no character voices)</Label>
          <p className="text-xs text-muted-foreground">
            One voice for the whole book, dialogue included. Turn it off to give each character a
            voice that matches their gender. When we are not sure of a character&apos;s gender, the
            narrator reads that character&apos;s lines.
          </p>
        </div>
        <Switch
          id="narrator-only-switch"
          checked={narratorOnly}
          onCheckedChange={(checked) => onChange(voiceModePatch(checked))}
        />
      </div>
      {!narratorOnly && (
        <div className="flex items-start justify-between gap-4 rounded-lg border p-3">
          <div className="flex flex-col gap-1">
            <Label htmlFor="ai-speaker-detection-switch">AI speaker detection</Label>
            <p className="text-xs text-muted-foreground">
              Uses an AI model to work out who is speaking when the book doesn&apos;t say. Better
              voices for each character, but slower.
            </p>
          </div>
          <Switch
            id="ai-speaker-detection-switch"
            checked={isAiSpeakerDetection(config.attributor)}
            onCheckedChange={(checked) => onChange({ attributor: attributorForSwitch(checked) })}
          />
        </div>
      )}
      {!narratorOnly && (
        <div className="flex items-start justify-between gap-4 rounded-lg border p-3">
          <div className="flex flex-col gap-1">
            <Label htmlFor="cast-review-switch">Check the cast before rendering</Label>
            <p className="text-xs text-muted-foreground">
              After we work out who speaks, the job pauses and shows you each character with their
              gender and voice. Fix what is wrong, merge two names for the same person, or hand a
              character to the narrator, then start rendering. If you never confirm, the job is
              canceled after two days and nothing is rendered. Turn this off to render straight
              away.
            </p>
          </div>
          <Switch
            id="cast-review-switch"
            checked={config.cast_review}
            onCheckedChange={(checked) => onChange({ cast_review: checked })}
          />
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Narrator voice reads all narration. Male/female pickers bias (not force) character
        casting when a character&apos;s gender is worked out from the text. Defaults work fine if
        you leave these alone.
      </p>
      <div className="flex flex-col gap-2">
        <Label>Narrator voice</Label>
        <VoicePicker
          voices={voices.voices}
          value={config.narrator_voice ?? voices.narrator_voice}
          onChange={(value) => onChange({ narrator_voice: value })}
        />
        <p className="text-xs text-muted-foreground">
          {hasClonedVoices ? (
            <>Your own voices are listed at the end. </>
          ) : (
            <>Want to narrate with your own voice? </>
          )}
          <Link href="/voices" className="underline underline-offset-2">
            {hasClonedVoices ? "Manage your voices" : "Record your voice"}
          </Link>
          . Cloned voices are made on your local worker.
        </p>
        {cloneProblem && <p className="text-xs text-destructive">{cloneProblem}</p>}
      </div>
      {!narratorOnly && (
        <>
          <div className="flex flex-col gap-2">
            <Label>Male character voice</Label>
            <VoicePicker
              voices={maleVoices}
              value={config.male_voice}
              onChange={(value) => onChange({ male_voice: value })}
              allowNoPreference
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label>Female character voice</Label>
            <VoicePicker
              voices={femaleVoices}
              value={config.female_voice}
              onChange={(value) => onChange({ female_voice: value })}
              allowNoPreference
            />
          </div>
        </>
      )}

      {!narratorOnly && (
      <Accordion>
        <AccordionItem value="advanced">
          <AccordionTrigger>Advanced: fix a character&apos;s gender or voice</AccordionTrigger>
          <AccordionContent>
            <div className="flex flex-col gap-3">
              <p className="text-xs text-muted-foreground">
                Know a character the app might get wrong? Say their gender and we will give them a
                matching voice, or pick one voice for them. Use the name as the book spells it.
              </p>
              {overrides.length > 0 && (
                <ul className="flex flex-col gap-2">
                  {overrides.map((row) => (
                    <li key={row.name} className="flex items-center justify-between gap-2 text-sm">
                      <span>
                        {row.name}
                        {row.gender && <> · {row.gender === "f" ? "female" : "male"}</>}
                        {row.voice && (
                          <>
                            {" "}
                            → <span className="font-mono">{labelForVoiceId(voices.voices, row.voice)}</span>
                          </>
                        )}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onChange(removeCharacterOverride(config, row.name))}
                      >
                        Remove
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                <div className="flex flex-1 flex-col gap-1.5">
                  <Label htmlFor="override-name">Character name</Label>
                  <Input
                    id="override-name"
                    value={overrideName}
                    onChange={(event) => setOverrideName(event.target.value)}
                  />
                </div>
                <div className="flex flex-1 flex-col gap-1.5">
                  <Label>Gender</Label>
                  <Select
                    value={overrideGender}
                    onValueChange={(value) => setOverrideGender(value ?? GENDER_NOT_SET)}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(GENDER_CHOICES).map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex flex-1 flex-col gap-1.5">
                  <Label>Voice</Label>
                  <VoicePicker
                    voices={voices.voices}
                    value={overrideVoice}
                    onChange={setOverrideVoice}
                    allowNoPreference
                    placeholder="Pick one for me"
                  />
                </div>
                <Button type="button" variant="outline" onClick={addOverride}>
                  Add
                </Button>
              </div>
              {overrideError && <p className="text-xs text-destructive">{overrideError}</p>}
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
      )}
    </div>
  );
}
