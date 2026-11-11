# Gender and voice evaluation

Question: does the gender resolver give each character the right voice gender, and what
is left unresolved? Measured on the first Shadow Slave book, on two slices chosen from
the text.

| Slice | Book chapters | Why |
| --- | --- | --- |
| `ch01-10` | 1 to 10 | The default render. Every speaker is male, so it cannot test women. |
| `ch36-43` | 36 to 43 | Nephis and Cassie speak in every chapter (117 of 194 quoted paragraphs). |

## Files

- `gold/ch01-10.json`, `gold/ch36-43.json`: character names, aliases, gender and, in place
  of each evidence quote, the chapter plus a short SHA-256 of the quote. No book text.
- `gold/speaker-labels.json`: the speaker for 80 stratified dialogue lines, by line id
  (`chapter:ordinal:hash8`) and name only.
- `local/` (gitignored): the gold with verbatim quotes (`gold-<slice>.json`) and the
  spot-check sheet `speaker-spot-check.csv` with line context. Rebuild the committed gold
  from the local one with `build_gold.py`.

## Running

Run from the repo root with the project's virtualenv.

```bash
export PYTHONPATH=src:evals/gender          # PowerShell: $env:PYTHONPATH = "src;evals/gender"
.venv/Scripts/python.exe evals/gender/run_eval.py               # evidence from the slice only
.venv/Scripts/python.exe evals/gender/run_eval.py --scope book  # evidence from all 100 chapters
.venv/Scripts/python.exe evals/gender/build_spot_check.py sheet   # regenerate the sheet
.venv/Scripts/python.exe evals/gender/build_spot_check.py labels  # sheet -> gold/speaker-labels.json
```

The EPUB is read from `epubs/` (not in the repository).

## What it reports

For `before` (the old dialogue-tag vote and any-gender voice pool, reproduced in
`run_eval.py`) and `after` (the layered resolver and narrator fallback), per slice:

- characters scored, resolved, correct, accuracy among resolved, coverage, and the
  characters that were wrong or left unresolved;
- speakers the gold does not know, and "phantom" speakers (the gold says the character
  speaks no lines);
- dialogue lines per voice gender (male, female, narrator voice);
- lines voiced by a wrong-gender voice. The speaker used is the one the attributor chose,
  judged against the gold gender of that character. A line given to the wrong person is
  only caught through the spot-check labels, which are reported separately.

The spot check reports speaker accuracy per heuristic tier, an estimate over all lines
weighted by tier size (the sample is stratified, so its raw accuracy is not the real
accuracy), and the labelled lines voiced by a wrong-gender voice.

## Labels

`claude_label` in the sheet was filled by reading each line in context. The sheet has an
`owner_label` column; when it is filled it replaces the claude label in
`build_spot_check.py labels`. Treat the numbers as an estimate until the owner has
checked the sheet.
