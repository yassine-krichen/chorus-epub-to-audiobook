import { Progress } from "@/components/ui/progress";

export function JobProgressBar({ percent }: { percent: number }) {
  return (
    <div className="flex items-center gap-2">
      <Progress value={percent} className="w-24" />
      <span className="text-xs text-muted-foreground">{Math.round(percent)}%</span>
    </div>
  );
}
