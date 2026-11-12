"use client";

import { PlayIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  NEEDS_LOCAL_WORKER,
  builtInVoices,
  clonedVoices,
  isSelectable,
  labelForVoiceId,
  voiceLabel,
} from "@/lib/voices/voice-display";
import type { VoiceInfo } from "@/types/voice";

const NO_PREFERENCE = "__none__";

interface VoicePickerProps {
  voices: VoiceInfo[];
  value: string | null;
  onChange: (value: string | null) => void;
  allowNoPreference?: boolean;
  /** Text of the option that maps to `null`. */
  noPreferenceLabel?: string;
  placeholder?: string;
  disabled?: boolean;
}

/** Select wrapper for choosing a voice id from the curated `/voices` pool, with the user's
 * own cloned voices in a group of their own, and an optional "No preference" option that
 * maps to `null`. A cloned voice cannot be picked while no local worker can speak it. */
export function VoicePicker({
  voices,
  value,
  onChange,
  allowNoPreference = false,
  noPreferenceLabel = "No preference",
  placeholder = "Select a voice",
  disabled = false,
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

  function renderVoice(voice: VoiceInfo) {
    const canPlaySample = voice.sample_url && !brokenSamples.has(voice.id);
    const selectable = isSelectable(voice);
    return (
      <SelectItem key={voice.id} value={voice.id} disabled={!selectable}>
        <span className="flex flex-1 items-center justify-between gap-2">
          <span className="flex flex-col">
            {voiceLabel(voice)}
            {!selectable && (
              <span className="text-xs text-muted-foreground">{NEEDS_LOCAL_WORKER}</span>
            )}
          </span>
          {canPlaySample && (
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={`Play sample for ${voiceLabel(voice)}`}
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
  }

  const own = clonedVoices(voices);

  return (
    <Select
      disabled={disabled}
      value={value ?? NO_PREFERENCE}
      onValueChange={(next) => onChange(next === NO_PREFERENCE ? null : next)}
    >
      <SelectTrigger className="w-full">
        <SelectValue placeholder={placeholder}>
          {(selected: string | null) =>
            selected === null || selected === NO_PREFERENCE
              ? noPreferenceLabel
              : labelForVoiceId(voices, selected)
          }
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {allowNoPreference && <SelectItem value={NO_PREFERENCE}>{noPreferenceLabel}</SelectItem>}
        {builtInVoices(voices).map(renderVoice)}
        {own.length > 0 && (
          <SelectGroup>
            <SelectLabel>Your voices</SelectLabel>
            {own.map(renderVoice)}
          </SelectGroup>
        )}
      </SelectContent>
    </Select>
  );
}
