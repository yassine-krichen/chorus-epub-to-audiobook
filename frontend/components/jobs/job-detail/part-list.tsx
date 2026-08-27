import { PartRow } from "@/components/jobs/job-detail/part-row";
import type { PartProgress } from "@/types/job";

export function PartList({ parts }: { parts: PartProgress[] }) {
  if (parts.length === 0) {
    return <p className="text-sm text-muted-foreground">No parts finished yet.</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      {parts.map((part) => (
        <PartRow key={part.part_number} part={part} />
      ))}
    </div>
  );
}
