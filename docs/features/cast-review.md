# Cast review: check who speaks before any audio is made

Before: a hosted job went straight from "find the characters" to "make hours of audio".
If the app mixed up two names, or gave a woman a man's voice, you only found out by
listening, and fixing it meant making the whole job again.

Now a job pauses once the cast is known. The job page shows each character with the
detected gender and voice. You can fix the gender, pick a voice, say that two names are the
same person, or hand a character to the narrator. Then you press **Start rendering**. The
pause costs nothing while you think: no worker keeps running, and the model is not asked
again when the job resumes. This page shows the whole flow.

## The flow

```mermaid
stateDiagram-v2
    [*] --> Wizard: you set up a new job
    state reviewSwitch <<choice>>
    Wizard --> reviewSwitch
    state "Job runs straight through, as before" as Straight
    state "Job queued with review on (the default)" as Queued
    reviewSwitch --> Straight: Narrator reads everything is on, or you turned the review switch off
    reviewSwitch --> Queued: character voices and the review switch on

    Queued --> Detecting: worker starts
    state "Worker: parse the book, find who speaks, work out genders, cast voices, write the cast report" as Detecting

    state anyoneCheck <<choice>>
    Detecting --> anyoneCheck
    anyoneCheck --> Straight: nobody speaks, or the report cannot be read: nothing to review
    state "Worker saves the cast report, state.json and the speaker labels to storage. Only after that does the job change status" as Saving
    anyoneCheck --> Saving: at least one character

    state saveCheck <<choice>>
    Saving --> saveCheck
    state "Job awaiting_cast_review. The worker stops and frees its machine. Badge: Needs your review" as Waiting
    state "Job failed with the error shown: a job that cannot be resumed never waits" as Failed
    saveCheck --> Waiting: saved
    saveCheck --> Failed: storage error

    state "You open the job page. Each character: lines, sample line, detected gender and the clues, Gender, Voice and Who is this?" as Reviewing
    Waiting --> Reviewing

    state "Gender: Male or Female (beats the text)" as FixGender
    state "Voice: any voice, or Automatic" as FixVoice
    state "Who is this: own character, narrator reads these lines, or same person as another character" as FixWho
    Reviewing --> FixGender
    Reviewing --> FixVoice
    Reviewing --> FixWho
    FixGender --> Reviewing
    FixVoice --> Reviewing
    FixWho --> Reviewing
    note right of FixWho
        A character others were merged into stays its own character.
        A merged character takes no gender or voice of its own.
    end note

    state decision <<choice>>
    Reviewing --> decision
    decision --> Confirming: Start rendering (Apply N fixes, or Looks right)
    decision --> Canceled: you cancel the job
    decision --> Lapsed: nothing happens until the deadline

    state "Lapsed: 48 hours by default. The job is canceled with the message: the cast review was not confirmed in time, so nothing was rendered. Stored files are cleaned up after the usual grace period" as Lapsed
    Lapsed --> Canceled

    state "API checks: your job, still waiting, names are in the detected cast, no chains of merges, voices are real" as Confirming
    state confirmCheck <<choice>>
    Confirming --> confirmCheck
    state "Refused with a clear message, the job keeps waiting: unknown name, bad merge, not waiting any more (409), cast report lost" as Refused
    confirmCheck --> Refused: a check fails
    Refused --> Reviewing
    state "Fixes saved in the job's settings, review turned off, status back to queued (local worker) or running (Cloud Run)" as Resumed
    confirmCheck --> Resumed: all checks pass

    state startCheck <<choice>>
    Resumed --> startCheck
    state "Cloud Run could not start the worker: status goes back to waiting, you can press the button again (502)" as StartFailed
    startCheck --> StartFailed: worker did not start
    StartFailed --> Reviewing
    startCheck --> Resuming: started

    state "Worker restores state.json and the labels from storage, applies your merges, works genders out again with your overrides. No model call is repeated" as Resuming
    Resuming --> Straight: the review is off now, so it does not pause again
    Straight --> Rendering
    state "Render the audio, chapter by chapter" as Rendering
    Rendering --> [*]
    Canceled --> [*]
    Failed --> [*]
```

## What changes for you

- A new status, **Needs your review**, and a review card at the top of the job page. Chapters
  and parts appear once rendering starts.
- On the card, for each character: how many lines, a sample line, the detected gender with
  the clues, and three controls: **Gender**, **Voice** and **Who is this?**.
  **Who is this?** is where you merge a name into another character ("Same person as
  Nephis") or send a character to the narrator.
- Characters the app is unsure about come first, as in the cast report.
- The button says **Apply N fixes and start rendering**, or **Looks right, start rendering**
  when you changed nothing.
- The card shows the deadline. If you never confirm, the job is canceled and the page says so.
- You can still cancel the job while it waits.

## Defaults

- Review is **on** for character voices and **off** for "Narrator reads everything" (there is
  no cast to check).
- A cast with nobody to review does not pause.
- The wizard has a switch, **Check the cast before rendering**, to turn it off.
- A review that is never confirmed is canceled after **48 hours**. The operator can change it
  with `CAST_REVIEW_TIMEOUT_HOURS` (1 to 720). Canceled jobs have their stored files cleaned up
  after the usual grace period (7 days).
- The command line never pauses. `--dry-run` already shows the cast.

## How to try it

1. Run the API, a worker and the frontend (see `DEPLOYMENT.md`).
2. Apply the migration: `alembic upgrade head` (adds `jobs.cast_review_requested_at`).
3. New job, upload a book, leave **Check the cast before rendering** on.
4. Wait for the badge **Needs your review**, open the job, change a character, press
   **Start rendering**.
5. To see the expiry, start the API with `CAST_REVIEW_TIMEOUT_HOURS=1` and leave a job waiting.

API: `POST /jobs/{id}/cast-review` with `{"characters": [{"name": "Rain", "merge_into": "Sunny"}]}`.
Each entry holds a `gender` (`m` or `f`) and/or a `voice`, or `merge_into`, or
`read_by_narrator: true`. An empty list confirms the cast as it is.

Rollback: `alembic downgrade 0004`. Jobs waiting for review at that moment are marked failed
first, because the older code does not know the status.

## Limits and races

- The review lists at most 100 characters (the busiest, characters we are unsure about
  first) and only names up to 80 characters. Others keep their voice but cannot be edited,
  and the card says how many are not listed. Only the copy uploaded for the website is
  trimmed; the worker's own `cast_report.json` (and the command line's) keeps every
  character. The upload, the API and the card use the same limits, so a large cast can never
  produce a card that the server refuses.
- A job can hold 100 gender fixes and 100 voice choices. If the wizard already set some, the
  card tells you before you press the button when the review would go over.
- Cancel, confirm, worker success, worker failure, the pause and the expiry are all
  conditional database updates, so none of them can overwrite another's result. A cancel
  that lands just after a confirmation also stops the worker the confirmation started.
- If the worker cannot be started after you confirm, the job goes back to waiting with its
  earlier settings, not the ones you just sent.
- The cleanup job also cancels reviews that ran out of time even if nobody opened the job
  again, so their stored files are cleaned up after the grace period.
- A job waiting for review whose cast report has gone shows an error and a Cancel button, not
  an endless placeholder.

## Honest limits

- A merge is permanent for this job. To undo it, create the job again.
- A merge only fixes lines the app gave to the wrong name. It does not find lines it gave to
  nobody.
- "Same person as" lists characters in this cast only. There is no free-text rename.
- Sending a character to the narrator removes it from the cast report of the resumed run.
- The wait can be short or long, but while it lasts the job holds no worker. What it holds is
  stored files (the report, `state.json` and the speaker labels), kept until the job finishes
  or is cleaned up.
- If the cast report is lost while a job waits (for example, files cleaned up by hand), the
  review answers with a message asking you to create the job again.
