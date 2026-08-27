"use client";

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
        {voices.map((voice) => (
          <SelectItem key={voice.id} value={voice.id}>
            {voice.id}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
