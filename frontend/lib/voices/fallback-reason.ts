/** The reasons the worker may give for a built-in voice reading in place of a cloned one.
 * Mirrors `FALLBACK_REASONS` in `book2audio/tts/clone_engine.py`. */
export const KNOWN_FALLBACK_REASONS: readonly string[] = [
  "the voice-cloning engine ran out of memory",
  "the voice-cloning engine stopped",
  "the voice-cloning engine is not installed",
  "the voice-cloning engine timed out",
  "the voice recording could not be used",
  "the voice no longer exists",
];

const GENERIC_REASON = "the voice-cloning engine could not be used";

/** The reason as it may be shown. The server only sends the fixed sentences above, but a
 * report is data written by a worker: anything else (an older worker's raw message, a stack
 * trace) is replaced by a generic sentence rather than put on the page. */
export function fallbackReasonText(reason: string): string {
  return KNOWN_FALLBACK_REASONS.includes(reason) ? reason : GENERIC_REASON;
}

/** A built-in voice id such as `am_michael`; anything that does not look like one is shown as
 * "a built-in voice". */
export function fallbackVoiceText(voiceId: string): string {
  return /^[a-z]{2}_[a-z]{2,20}$/.test(voiceId) ? voiceId : "a built-in voice";
}
