"use client";

import { useState } from "react";

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { VoicePicker } from "@/components/voices/voice-picker";
import { useVoices } from "@/hooks/use-voices";
import type { JobConfig } from "@/types/job";

interface VoiceCastingSectionProps {
  config: JobConfig;
  onChange: (patch: Partial<JobConfig>) => void;
}

export function VoiceCastingSection({ config, onChange }: VoiceCastingSectionProps) {
  const { data: voices, isLoading } = useVoices();
  const [overrideName, setOverrideName] = useState("");
  const [overrideVoice, setOverrideVoice] = useState<string | null>(null);

  if (isLoading || !voices) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
      </div>
    );
  }

  const maleVoices = voices.voices.filter((v) => v.gender === "male");
  const femaleVoices = voices.voices.filter((v) => v.gender === "female");
  const overrides = Object.entries(config.voice_overrides);

  function addOverride() {
    if (!overrideName.trim() || !overrideVoice) return;
    onChange({
      voice_overrides: { ...config.voice_overrides, [overrideName.trim()]: overrideVoice },
    });
    setOverrideName("");
    setOverrideVoice(null);
  }

  function removeOverride(name: string) {
    const next = { ...config.voice_overrides };
    delete next[name];
    onChange({ voice_overrides: next });
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-muted-foreground">
        Narrator voice reads all narration. Male/female pickers bias (not force) character
        casting when a character&apos;s gender is guessed from context. Defaults work fine if you
        leave these alone.
      </p>
      <div className="flex flex-col gap-2">
        <Label>Narrator voice</Label>
        <VoicePicker
          voices={voices.voices}
          value={config.narrator_voice ?? voices.narrator_voice}
          onChange={(value) => onChange({ narrator_voice: value })}
        />
      </div>
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

      <Accordion>
        <AccordionItem value="advanced">
          <AccordionTrigger>Advanced: per-character voice overrides</AccordionTrigger>
          <AccordionContent>
            <div className="flex flex-col gap-3">
              {overrides.length > 0 && (
                <ul className="flex flex-col gap-2">
                  {overrides.map(([name, voiceId]) => (
                    <li key={name} className="flex items-center justify-between gap-2 text-sm">
                      <span>
                        {name} → <span className="font-mono">{voiceId}</span>
                      </span>
                      <Button variant="ghost" size="sm" onClick={() => removeOverride(name)}>
                        Remove
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="flex items-end gap-2">
                <div className="flex flex-1 flex-col gap-1.5">
                  <Label htmlFor="override-name">Character name</Label>
                  <Input
                    id="override-name"
                    value={overrideName}
                    onChange={(event) => setOverrideName(event.target.value)}
                  />
                </div>
                <div className="flex flex-1 flex-col gap-1.5">
                  <Label>Voice</Label>
                  <VoicePicker voices={voices.voices} value={overrideVoice} onChange={setOverrideVoice} />
                </div>
                <Button type="button" variant="outline" onClick={addOverride}>
                  Add
                </Button>
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  );
}
