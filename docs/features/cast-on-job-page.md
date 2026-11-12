# Cast on the job page: see who speaks before you listen

Before: the cast report (who speaks, their gender, the voice they get) was only printed in
the command line, and a hosted worker kept `cast_report.json` in its scratch folder. On
the website you could not see it at all.

Now the worker uploads the report next to the job's other files, the API serves it to the
job's owner, and the job page shows a **Cast** section: each character, how many lines
they speak, their gender, why we think so, and the voice that reads them. This page shows
the whole flow.

## The flow

```mermaid
stateDiagram-v2
    [*] --> Queued: you create a job
    Queued --> Working: the worker starts
    state "Worker: parse the book, find who speaks, decide each character's gender and voice" as Working
    Working --> ReportWritten: cast_report.json is written (before any audio)
    state "Report written in the worker's scratch folder" as ReportWritten

    state uploadCheck <<choice>>
    ReportWritten --> uploadCheck
    state "Report uploaded to storage next to the job's other files" as Uploaded
    state "Upload failed: a warning is logged and the render carries on. The job page just has no Cast section" as UploadFailed
    uploadCheck --> Uploaded: storage reachable
    uploadCheck --> UploadFailed: storage error

    Uploaded --> Rendering
    UploadFailed --> Rendering
    state "Render the audio, chapter by chapter" as Rendering

    state "Job page asks the API for the cast every 5 seconds until it appears" as Polling
    Queued --> Polling: page open, job queued - not asked yet
    Working --> Polling: page open while the job runs
    state fetchCheck <<choice>>
    Polling --> fetchCheck
    state "No Cast section yet (the worker has not got that far, the report is missing, damaged, or the files were already cleaned up)" as NoCast
    state "Cast section shown" as ShowCast
    fetchCheck --> NoCast: API answers 404
    fetchCheck --> ShowCast: valid report
    NoCast --> Polling: job still running
    NoCast --> [*]: job over, nothing to show

    state castKind <<choice>>
    ShowCast --> castKind
    state "Narrator only: one sentence saying the narrator reads every line" as NarratorOnlyView
    state "No dialogue found: one sentence saying only the narrator speaks" as NoDialogueView
    state "Table: character, lines, gender, evidence, voice. Characters we are unsure about come first" as TableView
    castKind --> NarratorOnlyView: voice mode is narrator only
    castKind --> NoDialogueView: no character has lines
    castKind --> TableView: characters found

    state genderCheck <<choice>>
    TableView --> genderCheck
    state "Gender shown as Male or Female, with the clues behind it (for example 12 pronouns, 1 title)" as KnownGender
    state "Gender shown as Not sure. The page says the narrator voice reads this character's lines" as UnknownGender
    genderCheck --> KnownGender: confidence at or above the threshold (0.6)
    genderCheck --> UnknownGender: below it, or no clues
    KnownGender --> [*]
    UnknownGender --> [*]
```

## What changes for you

- The job page has a **Cast** section as soon as the worker has decided the cast, usually
  before the first chapter is done.
- A character shown as **Not sure** is read by the narrator voice. The page says so in plain
  words, so you know why a character sounds like the narrator.
- Only the owner of a job can fetch its cast report. Someone else's job answers "not found",
  the same as a job that does not exist.
- The API sends only these fields: name, lines, gender, confidence, voice, voice source, a
  sample line, the clue counts, and up to three clue sentences. Internal numbers stay in the
  stored file.

## Defaults

- The Cast section appears by default for every hosted job. There is nothing to switch on.
- The threshold for "sure enough" is the same 0.6 the command line uses.

## How to try it

1. Start the API and a worker (local worker or Cloud Run), and the frontend.
2. Create a job in the wizard with "Narrator reads everything" off.
3. Open the job page. After a short wait the **Cast** section appears above **Chapters**.
4. Try the narrator-only switch on another job: the section is one sentence.

API: `GET /jobs/{job_id}/cast` (bearer token). It answers 404 when there is no report to show.

## Honest limits

- The report covers the chapters the job renders, not the whole book.
- This section only shows the cast. Fixing a character comes next: the gender override in the
  wizard, and the review step before rendering.
- If the report upload fails, the section is missing for that job; the audio is not affected.
- A job whose files were cleaned up (failed or canceled jobs, after a grace period) no longer
  has a Cast section.
