import { Skeleton } from "@/components/ui/skeleton";
import type { ChapterProgress } from "@/types/job";

export function ChapterCard({ index, chapter }: { index: number; chapter: ChapterProgress | undefined }) {
  if (!chapter) {
    return <Skeleton className="aspect-square w-full rounded-lg" title="Not rendered yet" />;
  }

  const showThumbnail = chapter.thumbnail_status === "ready" && chapter.thumbnail_url;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex aspect-square w-full items-center justify-center overflow-hidden rounded-lg border bg-muted">
        {showThumbnail ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={chapter.thumbnail_url ?? undefined} alt={chapter.title} className="h-full w-full object-cover" />
        ) : (
          <span className="text-2xl text-muted-foreground">{index + 1}</span>
        )}
      </div>
      <p className="truncate text-xs text-muted-foreground" title={chapter.title}>
        {chapter.title}
      </p>
    </div>
  );
}
