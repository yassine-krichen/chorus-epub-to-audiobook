# Chapter picker: choose chapters from the book's own list

Before: to render only part of a book on the website you typed two numbers ("start at
chapter" and "chapter limit") without seeing the book's chapters. If you did not know that
the first page is a synopsis, or that the book numbers its chapters from 1, you guessed.

Now, once the book has uploaded, the wizard has a **Chapters** tab. It lists every section
of the book with its number, title, word count and a **Front matter** flag, and you pick a
range by clicking: **Start here** on the first chapter, **End here** on the last one. The
picker writes the same two settings as before (`start_chapter` and `chapter_limit`), so
nothing changes for the server or for jobs made without it. This page shows the flow.

## The flow

```mermaid
stateDiagram-v2
    [*] --> NoBook: you open the new job wizard
    state "Chapters tab says: upload a book to choose chapters. Every chapter from the story's first one is included meanwhile" as NoBook
    NoBook --> Uploading: you pick an EPUB file
    state "Upload to storage. Chapters already picked for an earlier book are cleared" as Uploading
    Uploading --> Reading: upload finished
    state "API reads the book: downloads it, checks the size, parses it with the same numbering rules a render uses" as Reading

    state readCheck <<choice>>
    Reading --> readCheck
    state "Refused with a plain message: not your upload (404), over 50 MB (413), not a readable EPUB or no chapters (422), too many requests in a short time (429)" as ReadFailed
    state "Chapter list shown: number, title, words, Front matter flag" as Listed
    readCheck --> Listed: readable
    readCheck --> ReadFailed: refused

    state "The Chapters tab explains why and offers Or type chapter numbers. Creating the job still works and renders every chapter" as Manual
    ReadFailed --> Manual
    NoBook --> Manual: you open Or type chapter numbers
    Listed --> Manual: you open Or type chapter numbers

    state "Whole book (automatic start). Summary: Chapters 1 to 100, sections, words, about N hours of audio" as WholeBook
    Listed --> WholeBook: default

    WholeBook --> PickingEnd: you choose Start here on a chapter
    state "Start chosen, running to the end of the book. Buttons now say End here" as PickingEnd
    PickingEnd --> Ranged: you choose End here on that chapter or a later one
    state "Range chosen: start to end. Summary and highlighted rows show exactly what will render" as Ranged
    PickingEnd --> PickingEnd: you choose Start here on an earlier chapter: the start moves
    Ranged --> PickingEnd: you choose Start here on another chapter
    Ranged --> WholeBook: Whole book
    PickingEnd --> WholeBook: Whole book

    state startKind <<choice>>
    PickingEnd --> startKind: how the start is saved
    state "Start left empty (automatic): the first story chapter, front matter skipped" as AutoStart
    state "Start 0: includes the synopsis and info pages before Chapter 1" as FrontStart
    state "Start N: the first section numbered N or higher" as NumberStart
    startKind --> AutoStart: you started on the first story chapter
    startKind --> FrontStart: you started on a front matter page
    startKind --> NumberStart: any other chapter

    state endKind <<choice>>
    Ranged --> endKind: how the end is saved
    state "Limit 0: through the end of the book" as ToEnd
    state "Limit = next chapter's number minus the start's number, so the server stops right after your last chapter" as Limit
    endKind --> ToEnd: you ended on the last chapter
    endKind --> Limit: you ended earlier

    Ranged --> Submitted: Create job
    WholeBook --> Submitted: Create job
    Manual --> Submitted: Create job
    state "Job created with start_chapter and chapter_limit. The worker applies them exactly as before" as Submitted
    Submitted --> [*]
```

## What changes for you

- A **Chapters** tab in the new job wizard, between Voice Casting and Audio Production.
- The list shows the book's own chapter numbers ("Chapter 7" is 7), the title, the word count,
  and marks synopsis and info pages as **Front matter** (skipped unless you start on one).
- A summary line above the list says what you picked, for example "Chapters 1 to 10 · 10
  of 612 sections · 48,210 words · about 5 h 20 min of audio". The hours use a calm pace of
  150 words a minute at your chosen speed.
- **Whole book** puts the selection back to the default in one click.
- **Preview mode** (first 3 chapters), the numeric "Start at chapter" and "Chapter limit"
  boxes moved here from Audio Production. The boxes sit under **Or type chapter numbers**
  and stay in step with the list.
- A chapter that the book splits into several sections is picked whole, at both ends.

## Defaults

- No range picked means start automatically at the story's first chapter and go to the end
  (`start_chapter` empty, `chapter_limit` 0), the same as before.
- Picking a new book clears any range picked for the old one.

## How to try it

1. New job, upload an EPUB (try the first Shadow Slave book).
2. Open the **Chapters** tab. The list appears after a moment, with SYNOPSIS marked
   **Front matter** and Chapter 1 first in the selection.
3. Choose **Start here** on Chapter 5, then **End here** on Chapter 8. The summary reads
   "Chapters 5 to 8".
4. Create the job. The job page shows four chapters.

API: `POST /jobs/inspect` with `{"epub_object_key": "uploads/<your id>/..."}` returns
`{title, author, chapters: [{index, number, title, front_matter, word_count}]}`.

## Honest limits

- The book is untrusted input read inside the API process. The download is capped at 50 MB
  while streaming and pinned to one version of the file; the archive is refused before parsing
  if it has too many entries, an entry over 16 MiB, more than 100 MB unpacked, or a forged
  size; any other parser failure is a plain 422. The parser is not run in a separate process
  with a memory limit (there is no portable way to do that on both Windows and Linux), so these
  caps and the rate limit are the protection. Only the user's own uploads can be read, and the
  same ownership check now guards job creation.
- The server reads the book once to build the list, so a large book takes a few seconds.
  Reading is limited to 20 books per user every 10 minutes (`INSPECT_RATE_LIMIT_PER_USER`,
  `INSPECT_RATE_LIMIT_WINDOW_SECONDS`).
- A kept prologue cannot be picked alone: it does not use up the limit, so ending on it also
  includes Chapter 1. The summary shows what will really render.
- Starting on a front matter page means starting at the very first page, because the server's
  "include front matter" setting is all or nothing.
- The picker's counting is a copy of the server's rule (`Config.selection_bounds`). They are
  kept in step by tests on both sides, but a change to one needs a change to the other.
- The list follows the file's own reading order and titles. A book that does not number its
  chapters is counted by position, as everywhere else.
