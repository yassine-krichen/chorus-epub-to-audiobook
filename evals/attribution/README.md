# Speaker attribution evaluation

Question: does `--attributor llm` or `hybrid` label dialogue speakers more accurately than the offline heuristic, and at what cost in calls and time?

This is a measurement harness. No result is claimed until the gold labels exist and `run_eval.py` has been run.

## Books

| Book | Source | Why |
| --- | --- | --- |
| Alice's Adventures in Wonderland | Project Gutenberg #11 | The common reference text; many short exchanges among a small, distinctive cast. Its EPUB uses curly double quotes, which the segmenter reads (verified: chapters 5, 6, 7 and 9 yield 99, 108, 145 and 135 dialogue lines). |
| Penrod (Booth Tarkington, 1914) | Project Gutenberg #402 | A lesser-known novel, so a model is less likely to have memorized who says what. Third-person, dialogue-heavy, a large cast (boys, parents, neighbours) with many untagged exchanges, and it uses double quotes (verified: the chosen chapter yields 126 dialogue lines). |

Rejected: *Five Children and It* (#778) uses single quotes, so the segmenter finds almost no dialogue.

Both EPUBs are downloaded into `data/` (gitignored) and pinned by sha256 in `books.json`. Gutenberg occasionally regenerates files; a changed hash stops the download and the evaluation instead of scoring against shifted lines.

## Splits

Chapter numbers are the parser's 1-based `Chapter.index` (checked: Alice's indices 1 to 12 are chapters I to XII).

| Split | Chapters | Lines |
| --- | --- | --- |
| test | Alice 5, 7, 9 and Penrod parser chapter 5 | 505 |
| dev | Alice 6 | 108 |

Penrod's EPUB packs several book chapters into each file, so "Penrod chapter 5" is the parser's fifth document (it opens at "Retiring from the Show Business"). Use the dev split while iterating on prompts; touch the test split only for final numbers.

## Labelling protocol (owner)

1. `python evals/attribution/build_sheet.py download`, then `... sheets`. This writes `sheets/test.csv`, `sheets/dev.csv` and `sheets/*.characters.txt`.
2. Fill the `speaker` column for every row. The sheet shows two segments of context on each side (dialogue in curly quotes, `¶` between segments) and the first row of each chapter lists the chapter's cast in `note`. The sheets contain no model or heuristic output on purpose.
3. Special values:
   - `Narrator`: the quoted words are not spoken by a character (a title, a scare quote, a sign).
   - `SKIP`: you cannot decide, or the segmenter broke the line. Skipped lines are left out of every score and counted in the report.
4. Use one name per character. Spelling variants are handled by the alias table in the gold file.
5. `python evals/attribution/build_sheet.py gold --split test` writes `gold/test.json`. It refuses unlabeled rows, duplicate ids, and any speaker that is not in the chapter's cast list (`books.json`), the alias table, `Narrator` or `SKIP`, listing the offending rows so typos cannot become permanent errors. A character missing from the cast list can be added to `books.json` or to the alias table. Edit the `aliases` table in that file as needed: `{"Mad Hatter": ["Hatter", "The Hatter"]}`. Aliases are preserved if you regenerate the file.

Gold line ids are `chapter:ordinal:hash8`: the parser chapter index, the 1-based position among that chapter's dialogue lines, and the first 8 hex characters of the SHA-256 of the line text. If the parser or segmenter changes so that a labelled line moves or changes, the id no longer exists and `run_eval.py` fails instead of scoring the wrong line.

## Running

```bash
# verify gold against the pinned books (needs no credentials, model or network)
python evals/attribution/run_eval.py --split dev --modes heuristic --check-only

# tuning
python evals/attribution/run_eval.py --split dev

# final numbers
FREELLMAPI_MODEL=<one concrete model> python evals/attribution/run_eval.py --split test
```

Run from the repo root with the project's virtualenv (`.venv/Scripts/python.exe` on Windows). LLM modes need `FREELLMAPI_BASE_URL`, `FREELLMAPI_API_KEY` and a concrete `FREELLMAPI_MODEL` (or `--model`). The model `auto` is refused because the router may switch models between calls; the pinned model is recorded in the results file.

## What it reports

- Accuracy per attributor with a 95% Wilson interval, over non-SKIP lines.
- For each LLM run: the paired bootstrap difference against the heuristic and hybrid against llm, in two forms. The line-level interval resamples single lines (10,000 resamples, seeded with `--seed`, default 0). The window-clustered interval resamples blocks of 25 consecutive dialogue lines of a chapter instead. Neighbouring lines share a scene, a speaker pair and a prompt, so they are not independent and the line-level interval is overconfident; quote the clustered one for the verdict and keep the line-level one for comparison. The test set has only about 21 such blocks, so even the clustered interval is rough.
- Accuracy per heuristic confidence tier (`named_trailing`, `named_leading`, `pronoun`, `alternation`, `fallback`), averaged over runs.
- Lines the LLM fixed and broke relative to the heuristic.
- Fraction of dialogue lines sent to the model, calls, cache hits, failed windows, wall time, and whether the budget or the circuit breaker stopped a run.
- SKIP count.

Each LLM mode runs 3 times (`--runs`) with an empty in-memory cache, so repeat runs measure the model's run-to-run variation and never replay an earlier answer.

Every run prints its failed windows, whether the circuit breaker tripped and whether the budget ran out. A run with any of those is **unreliable**: it is flagged with a loud warning, left out of the headline mean and the tier tables, and the script exits with status 3 (the results file is still written). Rerun it (a timeout or a provider outage is the usual cause) instead of quoting a number computed from heuristic fallbacks. The call budget is the production default, 150 per book per run. The model sees only the gold chapters; the heuristic still runs over the whole book, as in production.

Raw per-line results (gold, heuristic, tier, and every run's prediction) go to `results/<timestamp>-<split>.json` together with the model, prompt version, seed, EPUB hashes and alias table, so any number can be recomputed.

## Reading the numbers

With about 500 test lines a Wilson interval is roughly plus or minus 4 points, so differences under a few points are not distinguishable from noise; the paired window-clustered bootstrap interval is the one to trust for "is the LLM better". Accuracy is also bounded by label quality and by the heuristic's own name extraction, so inspect the `fixed` and `broken` lines in the results file before drawing conclusions. Only two books and one cast style are covered; the numbers say nothing about other genres or languages.

## Tests

`tests/test_eval_scoring.py` covers the Wilson interval, the seeded line-level and clustered bootstrap, alias canonicalization, SKIP exclusion and the hash-mismatch failure. `tests/test_eval_tools.py` covers sheet generation (no prediction columns, blank speakers), gold ingestion (unknown speakers, duplicate ids), the `.part` download, model pinning and the unreliable-run handling, using a synthetic EPUB.
