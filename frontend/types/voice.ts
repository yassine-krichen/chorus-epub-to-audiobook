export type VoiceGender = "male" | "female";

export type VoiceProvider = "kokoro" | "clone";

export interface VoiceInfo {
  id: string;
  gender: VoiceGender;
  is_default_narrator: boolean;
  sample_url: string | null;
  /** "clone" for the user's own recorded voices; their ids look like `clone:<uuid>`. */
  provider?: VoiceProvider;
  /** The user's name for a cloned voice; built-in voices are named by their id. */
  name?: string | null;
  /** False for a cloned voice while no local worker with the clone engine is online. */
  available?: boolean;
}

export interface VoicesResponse {
  narrator_voice: string;
  voices: VoiceInfo[];
  /** Whether a local worker that can speak cloned voices has been seen recently. */
  clone_worker_online?: boolean;
}

export interface ReadingScript {
  id: string;
  text: string;
}

export interface CloneOptions {
  scripts: ReadingScript[];
  consent_version: string;
  consent_text: string;
  max_voices: number;
  min_duration_s: number;
  max_duration_s: number;
  min_sample_rate: number;
  clone_worker_online: boolean;
}

export interface CloneUploadUrl {
  voice_id: string;
  upload_url: string;
}

export interface CreateClonedVoiceRequest {
  voice_id: string;
  name: string;
  gender: "m" | "f";
  script_id: string;
  consent: boolean;
  consent_version: string;
}

export interface ClonedVoice {
  id: string;
  name: string;
  gender: "m" | "f";
  duration_s: number;
  consent_at: string;
  created_at: string;
  recording_url: string | null;
}
