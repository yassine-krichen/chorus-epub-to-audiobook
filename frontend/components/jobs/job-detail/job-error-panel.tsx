export function JobErrorPanel({
  message,
  title = "Job failed",
}: {
  message: string;
  title?: string;
}) {
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4">
      <h3 className="text-sm font-medium text-destructive">{title}</h3>
      <p className="mt-1 text-sm text-destructive/90">{message}</p>
    </div>
  );
}
