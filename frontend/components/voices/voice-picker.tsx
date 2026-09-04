"use client";

import { PlayIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { VoiceInfo } from "@/types/voice";

const NO_PREFERENCE = "__none__";

interface VoicePickerProps {
  voices: VoiceInfo[];
  value: string | null;
  onChange: (value: string | null) => void;
  allowNoPreference?: boolean;
  placeholder?: string;
}

/** Select wrapper for choosing a voice id from the curated `/voices` pool,
 * with an optional "No preference" option that maps to `null`. */
export function VoicePicker({
  voices,
  value,
  onChange,
  allowNoPreference = false,
  placeholder = "Select a voice",
}: VoicePickerProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [brokenSamples, setBrokenSamples] = useState<Set<string>>(new Set());

  function playSample(sampleUrl: string, voiceId: string) {
    if (!audioRef.current) {
      audioRef.current = new Audio();
    }
    audioRef.current.src = sampleUrl;
    audioRef.current.play().catch(() => {
      setBrokenSamples((prev) => new Set(prev).add(voiceId));
    });
  }

  return (
    <Select
      value={value ?? NO_PREFERENCE}
      onValueChange={(next) => onChange(next === NO_PREFERENCE ? null : next)}
    >
      <SelectTrigger className="w-full">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {allowNoPreference && <SelectItem value={NO_PREFERENCE}>No preference</SelectItem>}
        {voices.map((voice) => {
          const canPlaySample = voice.sample_url && !brokenSamples.has(voice.id);
          return (
            <SelectItem key={voice.id} value={voice.id}>
              <span className="flex flex-1 items-center justify-between gap-2">
                {voice.id}
                {canPlaySample && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`Play sample for ${voice.id}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      event.preventDefault();
                      playSample(voice.sample_url as string, voice.id);
                    }}
                    onPointerDown={(event) => event.stopPropagation()}
                  >
                    <HugeiconsIcon icon={PlayIcon} strokeWidth={2} />
                  </Button>
                )}
              </span>
            </SelectItem>
          );
        })}
      </SelectContent>
    </Select>
  );
}
