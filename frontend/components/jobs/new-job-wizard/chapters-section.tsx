"use client";

import { useState } from "react";

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  applyPick,
  listeningTime,
  selectionBounds,
  summarizeSelection,
  WHOLE_BOOK,
  type ChapterSelection,
  type PickPhase,
} from "@/lib/jobs/chapter-selection";
import type { InspectionState } from "@/lib/jobs/inspection-state";
import { cn } from "@/lib/utils";
import type { BookInspection, JobConfig } from "@/types/job";

const PREVIEW_CHAPTER_LIMIT = 3;

interface ChaptersSectionProps {
  config: JobConfig;
  inspection: InspectionState;
  onChange: (patch: Partial<JobConfig>) => void;
}

function selectionOf(config: JobConfig): ChapterSelection {
  return { start_chapter: config.start_chapter, chapter_limit: config.chapter_limit };
}

/** Which chapters to turn into audio: click a first and a last chapter in the book's own
 * list, or type numbers. Chapter numbers are the book's ("Chapter 7" is 7). */
export function ChaptersSection({ config, inspection, onChange }: ChaptersSectionProps) {
  const [phase, setPhase] = useState<PickPhase>("pick-start");

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
        <div className="flex flex-col gap-0.5">
          <Label htmlFor="preview-mode">Preview mode</Label>
          <p className="text-xs text-muted-foreground">
            Only process the first {PREVIEW_CHAPTER_LIMIT} chapters. A quick way to hear the voices
            before committing to the whole book.
          </p>
        </div>
        <Switch
          id="preview-mode"
          checked={config.chapter_limit === PREVIEW_CHAPTER_LIMIT && config.start_chapter === null}
          onCheckedChange={(checked) => {
            setPhase("pick-start");
            onChange(
              checked
                ? { start_chapter: null, chapter_limit: PREVIEW_CHAPTER_LIMIT }
                : { start_chapter: null, chapter_limit: 0 }
            );
          }}
        />
      </div>

      {inspection.status === "no-upload" && (
        <p className="text-sm text-muted-foreground">
          Upload a book to see its chapters here and choose which ones to turn into audio. Until
          then, every chapter from the story&apos;s first one is included.
        </p>
      )}
      {inspection.status === "loading" && (
        <div className="flex flex-col gap-2" aria-busy="true">
          <p className="text-sm text-muted-foreground">Reading the chapter list…</p>
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      )}
      {inspection.status === "error" && (
        <p className="text-sm text-destructive">
          We could not list this book&apos;s chapters. {inspection.message} You can still type
          chapter numbers below, or create the job to render every chapter.
        </p>
      )}
      {inspection.status === "ready" && (
        <ChapterPicker
          book={inspection.book}
          config={config}
          phase={phase}
          onPhaseChange={setPhase}
          onChange={onChange}
        />
      )}

      <Accordion>
        <AccordionItem value="numbers">
          <AccordionTrigger>Or type chapter numbers</AccordionTrigger>
          <AccordionContent>
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="start-chapter">Start at chapter</Label>
                <p className="text-xs text-muted-foreground">
                  Leave empty and we start at the story&apos;s first chapter (synopsis and info
                  pages are skipped). Enter 0 to include them.
                </p>
                <Input
                  id="start-chapter"
                  type="number"
                  min={0}
                  placeholder="Automatic"
                  value={config.start_chapter ?? ""}
                  onChange={(event) => {
                    setPhase("pick-start");
                    onChange({
                      start_chapter: event.target.value === "" ? null : Number(event.target.value),
                    });
                  }}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="chapter-limit">Chapter limit (0 = all chapters)</Label>
                <p className="text-xs text-muted-foreground">
                  How many chapters to render from the start, counted by the book&apos;s own chapter
                  numbers.
                </p>
                <Input
                  id="chapter-limit"
                  type="number"
                  min={0}
                  value={config.chapter_limit}
                  onChange={(event) => {
                    setPhase("pick-start");
                    onChange({ chapter_limit: Number(event.target.value) });
                  }}
                />
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  );
}

interface ChapterPickerProps {
  book: BookInspection;
  config: JobConfig;
  phase: PickPhase;
  onPhaseChange: (phase: PickPhase) => void;
  onChange: (patch: Partial<JobConfig>) => void;
}

function ChapterPicker({ book, config, phase, onPhaseChange, onChange }: ChapterPickerProps) {
  const chapters = book.chapters;
  const selection = selectionOf(config);
  const summary = summarizeSelection(chapters, selection);
  const [startPos] = selectionBounds(chapters, selection);
  const isWholeBook =
    selection.start_chapter === WHOLE_BOOK.start_chapter &&
    selection.chapter_limit === WHOLE_BOOK.chapter_limit;

  function handlePick(pos: number) {
    const result = applyPick(chapters, selection, phase, pos);
    onChange(result.selection);
    onPhaseChange(result.phase);
  }

  function reset() {
    onChange(WHOLE_BOOK);
    onPhaseChange("pick-start");
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm" aria-live="polite">
          {summary.range === null ? (
            "No chapter matches these settings. Pick a start below, or reset."
          ) : (
            <>
              <span className="font-medium">
                {summary.firstNumber === summary.lastNumber
                  ? `Chapter ${summary.firstNumber}`
                  : `Chapters ${summary.firstNumber} to ${summary.lastNumber}`}
              </span>
              <span className="text-muted-foreground">
                {" "}
                · {summary.chapterCount} of {chapters.length} sections ·{" "}
                {summary.words.toLocaleString()} words · about{" "}
                {listeningTime(summary.words, config.speed)} of audio
              </span>
            </>
          )}
        </p>
        <Button type="button" variant="outline" size="sm" onClick={reset} disabled={isWholeBook}>
          Whole book
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        {phase === "pick-start"
          ? "Choose Start here on the first chapter you want. Synopsis and info pages are skipped unless you start on one."
          : "Now choose End here on the last chapter you want, or Start here on an earlier one to move the start."}
      </p>

      <div className="max-h-96 overflow-y-auto rounded-lg border">
        <Table>
          <TableHeader className="sticky top-0 bg-background">
            <TableRow>
              <TableHead className="w-20">No.</TableHead>
              <TableHead>Title</TableHead>
              <TableHead className="text-right">Words</TableHead>
              <TableHead className="w-28" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {chapters.map((chapter, pos) => {
              const selected =
                summary.range !== null && pos >= summary.range[0] && pos <= summary.range[1];
              const isStart = summary.range !== null && pos === summary.range[0];
              const isEnd = summary.range !== null && pos === summary.range[1];
              const endsHere = phase === "pick-end" && pos >= startPos;
              return (
                <TableRow key={chapter.index} className={cn(selected && "bg-primary/10")}>
                  <TableCell className="tabular-nums">
                    {chapter.front_matter ? "–" : chapter.number}
                  </TableCell>
                  <TableCell className="max-w-72 whitespace-normal">
                    <span className={cn(chapter.front_matter && "text-muted-foreground")}>
                      {chapter.title}
                    </span>
                    {chapter.front_matter && (
                      <Badge variant="outline" className="ml-2">
                        Front matter
                      </Badge>
                    )}
                    {isStart && <Badge className="ml-2">Start</Badge>}
                    {isEnd && !isStart && <Badge className="ml-2">End</Badge>}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {chapter.word_count.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      onClick={() => handlePick(pos)}
                      aria-label={`${endsHere ? "End here at" : "Start here at"} ${chapter.title}`}
                    >
                      {endsHere ? "End here" : "Start here"}
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
