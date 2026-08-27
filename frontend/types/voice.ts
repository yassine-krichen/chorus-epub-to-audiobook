export type VoiceGender = "male" | "female";

export interface VoiceInfo {
  id: string;
  gender: VoiceGender;
  is_default_narrator: boolean;
}

export interface VoicesResponse {
  narrator_voice: string;
  voices: VoiceInfo[];
}
