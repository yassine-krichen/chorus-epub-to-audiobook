import { PER_CHAPTER_FILENAME_TEMPLATE } from "@/lib/validation/filename-template";
import { DEFAULT_JOB_CONFIG, type JobConfig } from "@/types/job";

/**
 * The config changes behind the "One file per chapter" switch.
 *
 * On: one chapter per file, and if the user has not written their own file
 * name pattern yet, switch to the chapter-title one. Off: back to the default
 * batch size and part pattern, but a pattern the user wrote themselves is kept
 * unless it needs one file per chapter ({chapter_title}).
 */
export function oneFilePerChapterPatch(
  enabled: boolean,
  current: Pick<JobConfig, "filename_template">
): Partial<JobConfig> {
  const template = current.filename_template;
  if (enabled) {
    const keepsOwnTemplate = template !== null && template !== DEFAULT_JOB_CONFIG.filename_template;
    return {
      batch_size: 1,
      filename_template: keepsOwnTemplate ? template : PER_CHAPTER_FILENAME_TEMPLATE,
    };
  }
  const needsPerChapter =
    template === null ||
    template === PER_CHAPTER_FILENAME_TEMPLATE ||
    template.includes("{chapter_title}");
  return {
    batch_size: DEFAULT_JOB_CONFIG.batch_size,
    filename_template: needsPerChapter ? DEFAULT_JOB_CONFIG.filename_template : template,
  };
}
