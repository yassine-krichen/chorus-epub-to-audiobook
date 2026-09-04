import type { RenderStage } from "@/lib/jobs/render-stage";

function stageLabel(stage: RenderStage): string {
  switch (stage.kind) {
    case "succeeded":
      return "Finished";
    case "failed":
      return "Failed";
    case "canceled":
      return "Canceled";
    case "preparing-input":
      return "Preparing input…";
    case "casting-voices":
      return "Casting voices…";
    case "assembling-part":
      return `Assembling part ${stage.partNumber} of ${stage.totalParts}`;
    case "synthesizing-chapter":
      return `Synthesizing chapter ${stage.chapterNumber} of ${stage.totalChapters}`;
  }
}

export function JobRenderStage({ stage }: { stage: RenderStage }) {
  return <span className="text-xs text-muted-foreground">{stageLabel(stage)}</span>;
}
