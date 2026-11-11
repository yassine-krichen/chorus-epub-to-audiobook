# Fix a character's gender in the wizard

Before: the only way to tell the app that Cassie is a woman was the command-line flag
`--gender Cassie=f`. The website had a per-character voice override but nothing for gender,
so an unclear character was read by the narrator and you could not change that.

Now the voice section of the new-job wizard has **Advanced: fix a character's gender or
voice**. For each character you can set a gender, a voice, or both. This page shows the flow.

## The flow

```mermaid
stateDiagram-v2
    [*] --> Wizard: you open a new job and upload the book
    state "Voice section, Advanced panel: Character name, Gender, Voice, Add" as Wizard

    state modeCheck <<choice>>
    Wizard --> modeCheck
    state "Panel hidden: the narrator reads everything, there are no characters to fix" as Hidden
    state "Panel shown" as Shown
    modeCheck --> Hidden: Narrator reads everything is on
    modeCheck --> Shown: character voices (the default)

    Shown --> Fill: you type a name and pick a gender, a voice, or both
    state "Fill in a row" as Fill
    state inputCheck <<choice>>
    Fill --> inputCheck: you press Add
    state "Row not added. A message says what is wrong: empty name, name over 80 characters, nothing picked, or already 100 characters set" as Refused
    state "Row added to the list. Adding the same name again replaces its row" as Added
    inputCheck --> Refused
    inputCheck --> Added
    Refused --> Fill
    Added --> Shown: Remove deletes a row

    Shown --> Submit: you press Create job
    state "Job created with gender_overrides and voice_overrides in its settings" as Submit
    state apiCheck <<choice>>
    Submit --> apiCheck
    state "The server refuses the job (HTTP 422): bad gender, empty or long name, more than 100" as ApiRefused
    state "Worker reads the settings" as Worker
    apiCheck --> ApiRefused: invalid
    apiCheck --> Worker: valid
    ApiRefused --> Wizard: the message is shown, nothing is rendered

    state hasVoice <<choice>>
    Worker --> hasVoice
    state "That exact voice reads the character. The gender is still saved for the cast report" as FixedVoice
    state "Gender decided by you (confidence 1.0, it beats anything in the text): the character is cast from the matching male or female voices" as GenderVoice
    state "No row for this character: the app works the gender out from the text, and the narrator reads them if it is not sure" as Automatic
    hasVoice --> FixedVoice: a voice was set
    hasVoice --> GenderVoice: only a gender was set
    hasVoice --> Automatic: no row

    state matchCheck <<choice>>
    FixedVoice --> matchCheck
    GenderVoice --> matchCheck
    state "Cast section on the job page shows the character with your choice" as CastShown
    state "No character has that name. Nothing changes for that row; the command-line run would print a warning" as NoMatch
    matchCheck --> CastShown: the name matches a detected character
    matchCheck --> NoMatch: the name matches nobody
    Automatic --> CastShown
    CastShown --> [*]
    NoMatch --> [*]
```

## What changes for you

- You can tell the app a character's gender before the job starts, on the website.
- A gender row is enough: the character gets a voice of that gender from the pool. Add a
  voice only when you want one specific voice.
- The row list shows each character with the gender and voice you chose, and a Remove button.
- The wizard checks the same limits as the server (name 1 to 80 characters, 100 rows at most),
  so a mistake shows next to the form instead of as a failed job.

## Defaults

- No overrides. The app works the gender out from the text, and when it is not sure the
  narrator voice reads the character.
- The panel is hidden when "Narrator reads everything" is on.

## How to try it

1. New job, upload a book, open the Voice Casting tab.
2. Open **Advanced: fix a character's gender or voice**.
3. Type `Cassie`, pick **Female**, leave the voice on "No preference", press **Add**.
4. Create the job and look at the **Cast** section on the job page.

## Honest limits

- Names are matched as the book spells them. A gender row matches without regard to capital
  letters; a voice row must match exactly.
- You have to know who to fix before the job starts. The review step before rendering (next
  feature) shows the detected cast so you can fix it after the first pass.
