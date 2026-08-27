import { NewJobWizard } from "@/components/jobs/new-job-wizard/new-job-wizard";

export default function NewJobPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">New job</h1>
      <NewJobWizard />
    </div>
  );
}
