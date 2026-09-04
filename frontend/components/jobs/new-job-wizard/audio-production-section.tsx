"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { MUSIC_PRESETS, MUSIC_PRESET_DESCRIPTIONS } from "@/lib/constants/music-presets";
import type { JobConfig } from "@/types/job";

// Client-side UX convenience only — the backend does not enforce these
// ranges (see book2audio.api.jobs.schemas.JobConfig), except for
// music_preset which is server-validated against MUSIC_PRESETS.
const BITRATE_PRESETS = ["32k", "64k", "96k", "128k", "192k"] as const;
const PREVIEW_CHAPTER_LIMIT = 3;

function firstSliderValue(value: number | readonly number[]): number {
  return Array.isArray(value) ? value[0] : (value as number);
}

interface AudioProductionSectionProps {
  config: JobConfig;
  onChange: (patch: Partial<JobConfig>) => void;
}

export function AudioProductionSection({ config, onChange }: AudioProductionSectionProps) {
  const isCustomBitrate = !BITRATE_PRESETS.includes(config.bitrate as (typeof BITRATE_PRESETS)[number]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Label>Speed ({config.speed.toFixed(2)}x)</Label>
        <p className="text-xs text-muted-foreground">1.0x is natural pace.</p>
        <Slider
          min={0.5}
          max={2.0}
          step={0.05}
          value={[config.speed]}
          onValueChange={(value) => onChange({ speed: firstSliderValue(value) })}
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label>Bitrate</Label>
        <p className="text-xs text-muted-foreground">
          64k is fine for speech-only narration. Go higher (128k+) mainly if background music is
          on and you notice compression artifacts.
        </p>
        <Select
          value={isCustomBitrate ? "custom" : config.bitrate}
          onValueChange={(value) => {
            if (value && value !== "custom") onChange({ bitrate: value });
          }}
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {BITRATE_PRESETS.map((preset) => (
              <SelectItem key={preset} value={preset}>
                {preset}
              </SelectItem>
            ))}
            <SelectItem value="custom">Custom…</SelectItem>
          </SelectContent>
        </Select>
        {isCustomBitrate && (
          <Input
            value={config.bitrate}
            onChange={(event) => onChange({ bitrate: event.target.value })}
            placeholder="e.g. 80k"
          />
        )}
      </div>

      <div className="flex items-center justify-between rounded-lg border p-3">
        <div className="flex flex-col gap-0.5">
          <Label htmlFor="preview-mode">Preview mode</Label>
          <p className="text-xs text-muted-foreground">
            Only process the first {PREVIEW_CHAPTER_LIMIT} chapters.
          </p>
        </div>
        <Switch
          id="preview-mode"
          checked={config.chapter_limit === PREVIEW_CHAPTER_LIMIT}
          onCheckedChange={(checked) => onChange({ chapter_limit: checked ? PREVIEW_CHAPTER_LIMIT : 0 })}
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="chapter-limit">Chapter limit (0 = all chapters)</Label>
        <Input
          id="chapter-limit"
          type="number"
          min={0}
          value={config.chapter_limit}
          onChange={(event) => onChange({ chapter_limit: Number(event.target.value) })}
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="batch-size">Batch size</Label>
        <p className="text-xs text-muted-foreground">
          How many chapters get bundled into each downloadable part. Lower = parts finish sooner
          but more files to manage. 0 = single file for the whole book.
        </p>
        <Input
          id="batch-size"
          type="number"
          min={1}
          value={config.batch_size}
          onChange={(event) => onChange({ batch_size: Number(event.target.value) })}
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label>Chapter gap ({config.chapter_gap.toFixed(1)}s)</Label>
        <p className="text-xs text-muted-foreground">Pause length between chapters.</p>
        <Slider
          min={0}
          max={5}
          step={0.1}
          value={[config.chapter_gap]}
          onValueChange={(value) => onChange({ chapter_gap: firstSliderValue(value) })}
        />
      </div>

      <div className="flex items-center justify-between rounded-lg border p-3">
        <Label htmlFor="music-switch">Background music</Label>
        <Switch id="music-switch" checked={config.music} onCheckedChange={(checked) => onChange({ music: checked })} />
      </div>

      {config.music && (
        <div className="flex flex-col gap-4 pl-3">
          <div className="flex flex-col gap-2">
            <Label>Music preset</Label>
            <Select
              value={config.music_preset}
              onValueChange={(value) => {
                if (value) onChange({ music_preset: value });
              }}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MUSIC_PRESETS.map((preset) => (
                  <SelectItem key={preset} value={preset}>
                    <div className="flex flex-col">
                      <span>{preset}</span>
                      <span className="text-xs text-muted-foreground">
                        {MUSIC_PRESET_DESCRIPTIONS[preset]}
                      </span>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2">
            <Label>Music volume ({Math.round(config.music_volume * 100)}%)</Label>
            <p className="text-xs text-muted-foreground">
              Loudness of the background music bed relative to narration. Default (22%) is tuned
              to sit under speech without competing.
            </p>
            <Slider
              min={0}
              max={1}
              step={0.01}
              value={[config.music_volume]}
              onValueChange={(value) => onChange({ music_volume: firstSliderValue(value) })}
            />
          </div>
        </div>
      )}

      <div className="flex items-center justify-between rounded-lg border p-3">
        <div className="flex flex-col gap-0.5">
          <Label htmlFor="expressive-switch">Expressive narration</Label>
          <p className="text-xs text-muted-foreground">Adds emotional inflection to the narration.</p>
        </div>
        <Switch
          id="expressive-switch"
          checked={config.expressive}
          onCheckedChange={(checked) => onChange({ expressive: checked })}
        />
      </div>

      <div className="flex items-center justify-between rounded-lg border p-3">
        <Label htmlFor="thumbnails-switch">Chapter thumbnails</Label>
        <Switch
          id="thumbnails-switch"
          checked={config.thumbnails}
          onCheckedChange={(checked) => onChange({ thumbnails: checked })}
        />
      </div>
    </div>
  );
}
