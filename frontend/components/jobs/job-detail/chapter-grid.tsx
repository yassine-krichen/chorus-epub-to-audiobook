import { ChapterCard } from "@/components/jobs/job-detail/chapter-card";
import type { ChapterProgress } from "@/types/job";

interface ChapterGridProps {
  totalChapters: number | null;
  chapters: ChapterProgress[];
}

export function ChapterGrid({ totalChapters, chapters }: ChapterGridProps) {
  if (totalChapters === null) {
    return (
      <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
        Parsing book…
      </div>
    );
  }

  const chaptersByIndex = new Map(chapters.map((chapter) => [chapter.index, chapter]));

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
      {Array.from({ length: totalChapters }, (_, index) => (
        <ChapterCard key={index} index={index} chapter={chaptersByIndex.get(index)} />
      ))}
    </div>
  );
}
