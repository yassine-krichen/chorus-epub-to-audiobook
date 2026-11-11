# AI speaker detection: who is speaking

Before: the app worked out who speaks each line of dialogue with offline rules only. The
rules are right when the text names the speaker ("said Sunny"). When it does not, they
guess from pronouns, from who spoke last, or from the nearest name, and the guess is often
the wrong character. A wrong speaker means a wrong voice.

Now there is an optional "AI speaker detection" switch. When it is on, the offline rules
still run first. Then an AI model is asked only about the lines the rules had to guess.
Lines where the text names the speaker are left alone and cost nothing. If the AI is
unavailable or fails, the offline answer is kept and the book still renders. This page
shows the whole flow.

## The flow

```mermaid
stateDiagram-v2
    [*] --> WizardChoice: you set up a job on the website
    state "Voice casting choice" as WizardChoice
    state choiceFork <<choice>>
    WizardChoice --> choiceFork

    state "Narrator only: one voice reads everything. The AI switch is hidden and set back to off, because there is nobody to detect" as NarratorOnly
    state "Basic detection (default): offline rules only. Nothing is sent to an AI model" as BasicChoice
    state "AI speaker detection: attributor hybrid. The AI model is asked about the lines the rules guessed" as AiChoice
    choiceFork --> NarratorOnly: Narrator reads everything is on
    choiceFork --> BasicChoice: AI speaker detection is off
    choiceFork --> AiChoice: AI speaker detection is on

    state "Job submitted to the API" as Submitted
    NarratorOnly --> Submitted
    BasicChoice --> Submitted
    AiChoice --> Submitted

    state gate <<choice>>
    Submitted --> gate
    state "Accepted and queued" as Queued
    state "Refused with HTTP 403: AI speaker detection is not available on this server yet. No job is created. The website shows the message and you can switch AI detection off and submit again" as GateRefused
    gate --> Queued: attributor is heuristic, or narrator only
    gate --> Queued: attributor is hybrid and HOSTED_LLM_ATTRIBUTION is enabled
    gate --> GateRefused: attributor is hybrid and HOSTED_LLM_ATTRIBUTION is not enabled
    GateRefused --> [*]

    state credCheck <<choice>>
    Queued --> credCheck: worker picks up the job
    state "Job fails before any audio is made. The job page shows the error: FREELLMAPI_BASE_URL or FREELLMAPI_API_KEY is not set for LLM attribution" as CredFail
    credCheck --> CredFail: hybrid, character voices, and AI credentials missing on the worker
    CredFail --> [*]
    credCheck --> Heuristic: all other cases

    state "Offline rules label every dialogue line. Each label records how it was found: a name in the text is solid, a pronoun, alternation or fallback is a guess. Narrator only skips this step" as Heuristic
    state hybridCheck <<choice>>
    Heuristic --> hybridCheck
    hybridCheck --> Cached: attributor is heuristic or narrator only, no AI step
    hybridCheck --> LlmPass: attributor is hybrid

    state "AI pass over the guessed lines" as LlmPass {
        [*] --> Window
        state "Group the guessed lines with nearby text into windows, with at most 25 lines each. Lines whose speaker is named in the text are shown as already attributed and are not asked about" as Window
        state windowCheck <<choice>>
        Window --> windowCheck
        state "Reuse the saved answer for this window. No request is made" as WindowCached
        state "Ask the AI model who speaks each line. Each answer is checked line by line" as WindowAsk
        state "Valid answers replace the offline label. Missing or invalid answers keep the offline label" as WindowApply
        state "Window keeps its offline labels. The failure is counted" as WindowFail
        state "No more requests this book. Remaining windows keep their offline labels. A warning is recorded" as Stopped
        windowCheck --> WindowCached: this exact window was answered before
        windowCheck --> WindowAsk: not saved, calls left, AI not stopped
        windowCheck --> Stopped: 150 requests used, or 3 failures in a row
        WindowAsk --> WindowApply: reply received
        WindowAsk --> WindowFail: timeout, network error, bad reply shape
        state "Next window" as NextWindow
        WindowCached --> NextWindow
        WindowApply --> NextWindow
        WindowFail --> NextWindow
        NextWindow --> Window: more windows
        NextWindow --> [*]: no more windows
        Stopped --> [*]
    }

    state "Answers saved in the job's working folder, so asking about the same text again costs nothing. Spelling variants and nicknames are merged, for example Neph and Nephis" as Cached
    LlmPass --> Cached
    state "Gender resolver reads the whole text for evidence about each character: pronouns, titles, your overrides. Unsure means the narrator reads that character. See voice-gender.md" as Gender
    Cached --> Gender
    state "Voices assigned: male or female voice for resolved characters, narrator voice for the rest" as Voices
    Gender --> Voices
    NarratorOnly --> Voices: narrator voice for every line
    Voices --> Render
    state "Render the audio" as Render
    Render --> [*]
```

## How the offline rules pick a speaker

These rules run first in every mode (they are the whole job when AI detection is off). Each
dialogue line goes through the checks in order and stops at the first one that answers. The
name that is the *subject* of the speech verb is the speaker: in "Nephis asked Sunny" the
second name is who was asked.

```mermaid
stateDiagram-v2
    [*] --> Trailing: a dialogue line

    state "Tag right after the quote, in the SAME paragraph: Name said, said Name, Name asked Other. A sentence ending in a colon opens the NEXT quote, and narration in the next paragraph is never this quote's tag" as Trailing
    state trailingFork <<choice>>
    Trailing --> trailingFork
    trailingFork --> NamedTrailing: the subject is a name
    trailingFork --> Leading: no name

    state "Speaker is that name (solid: named_trailing)" as NamedTrailing
    NamedTrailing --> [*]

    state "Tag right before the quote, last two sentences: Name said:, or a sentence ending in a colon whose subject is a known character" as Leading
    state leadingFork <<choice>>
    Leading --> leadingFork
    leadingFork --> NamedLeading: the subject is a name
    leadingFork --> Between: no name

    state "Speaker is that name (solid: named_leading)" as NamedLeading
    NamedLeading --> [*]

    state "Quote in the same paragraph as the previous quote, with a tag between them: speaker is the previous quote's (continuation)" as Between
    state betweenFork <<choice>>
    Between --> betweenFork
    betweenFork --> Continuation: yes
    betweenFork --> Pronoun: no
    state "Continuation" as Continuation
    Continuation --> [*]

    state "Tag with only a pronoun (he said, she asked)" as Pronoun
    state pronounFork <<choice>>
    Pronoun --> pronounFork
    state "Most recent speaker the pronoun can refer to. Gender evidence from a first pass rules out the wrong gender; a line that names the listener (Sunny, wait) is not Sunny's (pronoun)" as PronounPick
    state "No known character fits: narrator voice, left for the AI (fallback)" as PronounNobody
    pronounFork --> PronounPick: a pronoun tag, someone fits
    pronounFork --> PronounNobody: a pronoun tag, nobody fits
    pronounFork --> Unnamed: no pronoun tag
    PronounPick --> [*]
    PronounNobody --> [*]

    state "Tag whose subject is a described person (The old guard grunted): narrator voice, never the nearest named character (unnamed)" as Unnamed
    state unnamedFork <<choice>>
    Unnamed --> unnamedFork
    unnamedFork --> UnnamedDone: yes
    unnamedFork --> ScareQuote: no
    state "Narrator voice" as UnnamedDone
    UnnamedDone --> [*]

    state "A short quote with no closing punctuation in the middle of a sentence (the word 'honor' again) is not speech: narrator voice (quoted_term)" as ScareQuote
    state scareFork <<choice>>
    ScareQuote --> scareFork
    scareFork --> ScareDone: yes
    scareFork --> Beat: no
    state "Narrator voice" as ScareDone
    ScareDone --> [*]

    state "Short action by one known character just before the quote (Mara shrugged.): that character speaks (named_beat)" as Beat
    state beatFork <<choice>>
    Beat --> beatFork
    beatFork --> BeatDone: yes
    beatFork --> Guess: no
    state "Speaker is that character" as BeatDone
    BeatDone --> [*]

    state "Two speakers so far: alternate. One: continue. None: narrator voice (alternation, fallback). These are the guesses the AI is asked about" as Guess
    Guess --> [*]
```

Genders are read from the narration, not from who was assigned a line, so the rules run
twice: the first pass finds the characters and their gender evidence, the second uses it
to decide who "he" or "she" can be.

## What changes for you

- The voice casting tab has a new switch, "AI speaker detection". It is off by default.
- When it is on, the guessed lines are checked by an AI model. The usual effect is that
  more lines should go to the right character, and so to the right voice (not measured yet,
  see the limits below).
- It is slower, because each window is a request to the model.
- If you turn on "Narrator reads everything", the AI switch disappears and is set back
  to off. That job never asks the AI anything.
- If the server does not allow AI detection, you see "AI speaker detection isn't available
  on this server yet. Turn off "AI speaker detection" and try again." and no job is
  created. Turn the switch off and submit again.

## Defaults

- AI speaker detection is off (`attributor` is `heuristic`). On means `hybrid`.
- At most 150 requests per book. When they run out, the remaining windows keep the offline
  labels. A window that had to try a second model still counts once.
- The AI is a list of models, not one (see [llm-model-chain.md](llm-model-chain.md)). A
  rate-limited model is skipped and the next one answers.
- 3 failed requests in a row stop the AI for the rest of the book. "Failed" means every
  model in the list failed that window, not one model hitting its limit.
- The AI only decides lines that were guesses. A name written in the text always wins.

## What happens when the AI fails

The code never fails a render because of the AI (`LLMAttributor.attribute` in
`src/book2audio/attribution/llm.py`):

- One model fails (limit reached, timeout, network error, bad reply shape): the next model
  in the list gets the same window. Only when every model fails does that window keep its
  offline labels, and the run goes on.
- A reply with some missing or invalid lines: those lines keep their offline label, the
  valid ones are used.
- 3 failures in a row: the AI is not asked again for this book. The rest keep their
  offline labels and a warning is recorded.
- Request budget used up: the same, with a different warning.
- An unexpected error inside the AI step: caught, counted as a failed window, a warning
  is recorded, the offline labels stay.

The one hard failure is missing credentials. The worker checks them before doing any work
and stops the job with an error if `FREELLMAPI_BASE_URL` or `FREELLMAPI_API_KEY` is
missing. This check is skipped for narrator only jobs.

## What you must set up

On Render (the API):

- `HOSTED_LLM_ATTRIBUTION=enabled`, exactly this value, then redeploy. Unset means every
  AI job is refused with HTTP 403. Narrator only jobs skip this check. Any other value (for example `true` or `Enabled`) makes
  the API refuse to start. Registration is open and each AI job spends the freellmapi key,
  so this switch is deliberately closed by default.

On the machine that runs the worker (the local worker's `.env`):

- `FREELLMAPI_BASE_URL`, including `/v1`.
- `FREELLMAPI_API_KEY`.
- Optional: `FREELLMAPI_MODEL_ATTRIBUTION` (or `FREELLMAPI_MODEL`), a comma-separated list
  of model names tried in order, so one model's daily limit does not stop a book. If both
  are unset the built-in list is used (`gemini-2.5-flash-lite`, `gpt-oss-120b`,
  `llama-4-scout`). The local `.env` sets `FREELLMAPI_MODEL_ATTRIBUTION` to that list. See
  [llm-model-chain.md](llm-model-chain.md).

Restart the worker after changing `.env`. Do not put these values in the frontend or in
Vercel: only the worker talks to the AI gateway.

## How to try it

1. Set the two required worker variables above and `HOSTED_LLM_ATTRIBUTION=enabled` on Render.
2. On the website, upload a book, open Voice Casting and turn on "AI speaker detection".
3. Create the job. Without step 1 on Render you get the 403 message instead.

From the command line: `uv run book2audio generate "epubs/mybook.epub" --limit 10
--attributor hybrid`. Add `--dry-run` to see how many AI requests it would make without
sending any. The command line ignores `HOSTED_LLM_ATTRIBUTION`.

## Honest limits

- On the 80-line speaker spot check (Shadow Slave book 1) the offline rules get 33 right
  and the AI mode with the default model list gets 61. That is one run of 80 lines, an
  estimate. Details in [llm-model-chain.md](llm-model-chain.md).
- The AI can be wrong too. It only replaces guesses, never a name found in the text.
- Book text for guessed lines is sent to the freellmapi gateway.
- The AI step does not decide gender. Gender still comes from the text evidence, and an
  unsure character is read by the narrator. An AI gender check is a possible next step.
