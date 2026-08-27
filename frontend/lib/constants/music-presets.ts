/**
 * Music presets are not exposed via any API — hardcoded here from
 * src/book2audio/assemble/music.py::MUSIC_PRESETS. Must be kept in sync
 * manually if that dict ever changes.
 */
export const MUSIC_PRESETS = ["ambient", "drone", "rain"] as const;

export type MusicPreset = (typeof MUSIC_PRESETS)[number];
