import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { PartProgress } from "@/types/job";

export function PartRow({ part }: { part: PartProgress }) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-col gap-0.5">
        <span className="text-sm font-medium">Part {part.part_number}</span>
        <span className="text-xs text-muted-foreground">
          Chapters {part.first_index + 1}–{part.last_index + 1}
        </span>
      </div>
      <div className="flex items-center gap-3">
        {part.download_url ? (
          <>
            <audio controls src={part.download_url} className="h-9 max-w-64" />
            <a href={part.download_url} className="text-sm underline" download>
              Download
            </a>
          </>
        ) : (
          <Tooltip>
            <TooltipTrigger render={<span />}>
              <audio controls className="h-9 max-w-64 opacity-50" />
            </TooltipTrigger>
            <TooltipContent>
              No download link available yet. This can happen for jobs created without a
              filename template.
            </TooltipContent>
          </Tooltip>
        )}
      </div>
    </div>
  );
}
