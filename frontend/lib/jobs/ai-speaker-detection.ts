import type { Attributor, JobConfig, VoiceMode } from "@/types/job";

/** The "AI speaker detection" switch: on asks the AI model about lines the rules had to guess. */
export function attributorForSwitch(enabled: boolean): Attributor {
  return enabled ? "hybrid" : "heuristic";
}

export function isAiSpeakerDetection(attributor: Attributor): boolean {
  return attributor !== "heuristic";
}

/**
 * The config changes behind the "Narrator reads everything" switch.
 *
 * With one voice there is nobody to detect, so AI detection goes back to off.
 * The server refuses AI detection when it is not enabled there, even for a
 * narrator-only job, so a leftover "hybrid" would fail a job that never uses it.
 */
export function voiceModePatch(narratorOnly: boolean): Partial<JobConfig> {
  const voiceMode: VoiceMode = narratorOnly ? "narrator_only" : "cast";
  return narratorOnly
    ? { voice_mode: voiceMode, attributor: attributorForSwitch(false) }
    : { voice_mode: voiceMode };
}
