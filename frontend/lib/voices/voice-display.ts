import type { JobConfig } from "@/types/job";
import type { VoiceInfo } from "@/types/voice";

/** Why a cloned voice cannot be picked right now. Shown next to the voice, never as an error. */
export const NEEDS_LOCAL_WORKER = "Needs your local worker";

const CLONE_PREFIX = "clone:";

export function isCloneVoiceId(voiceId: string): boolean {
  return voiceId.startsWith(CLONE_PREFIX);
}

export function isCloneVoice(voice: VoiceInfo): boolean {
  return voice.provider === "clone";
}

/** What to call a voice: the user's name for a cloned one, the id for a built-in one. */
export function voiceLabel(voice: VoiceInfo): string {
  return isCloneVoice(voice) ? (voice.name ?? "Your voice") : voice.id;
}

/** The label for a voice id, falling back to the id (or a neutral name for an unknown clone). */
export function labelForVoiceId(voices: VoiceInfo[], voiceId: string): string {
  const voice = voices.find((candidate) => candidate.id === voiceId);
  if (voice) return voiceLabel(voice);
  return isCloneVoiceId(voiceId) ? "A deleted voice" : voiceId;
}

export function builtInVoices(voices: VoiceInfo[]): VoiceInfo[] {
  return voices.filter((voice) => !isCloneVoice(voice));
}

export function clonedVoices(voices: VoiceInfo[]): VoiceInfo[] {
  return voices.filter(isCloneVoice);
}

/** A cloned voice can only be picked while a local worker that can speak it is online. */
export function isSelectable(voice: VoiceInfo): boolean {
  return voice.available !== false;
}

/** The cloned voice ids a job's settings name (narrator and per-character voices). */
export function cloneVoiceIdsInConfig(
  config: Pick<JobConfig, "narrator_voice" | "voice_overrides">
): string[] {
  const named = [config.narrator_voice, ...Object.values(config.voice_overrides)];
  return [...new Set(named.filter((id): id is string => id !== null && isCloneVoiceId(id)))];
}

/**
 * A sentence for the person starting a job when it names a cloned voice that cannot be
 * used right now, or null when everything it names is usable.
 */
export function cloneProblemForConfig(
  config: Pick<JobConfig, "narrator_voice" | "voice_overrides">,
  voices: VoiceInfo[]
): string | null {
  const unusable = cloneVoiceIdsInConfig(config).filter((id) => {
    const voice = voices.find((candidate) => candidate.id === id);
    return voice === undefined || !isSelectable(voice);
  });
  if (unusable.length === 0) return null;
  const known = voices.some((voice) => unusable.includes(voice.id));
  return known
    ? "Your cloned voice needs the local worker with the voice-cloning engine, and it is not online. Start it, or pick a built-in voice."
    : "One of the cloned voices you picked no longer exists. Pick another voice.";
}
