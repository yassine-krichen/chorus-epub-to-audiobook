"""Compare the heuristic, llm and hybrid speaker attributors against gold labels.

    python evals/attribution/run_eval.py --split dev --modes heuristic
    FREELLMAPI_MODEL=<pinned model> python evals/attribution/run_eval.py --split test

LLM modes need FREELLMAPI_BASE_URL, FREELLMAPI_API_KEY and a concrete
FREELLMAPI_MODEL: ``auto`` lets the router swap models between calls, so a
result could not be attributed to any one model and the run is refused.
"""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import sys
import time
from dataclasses import dataclass, field

import common
import scoring

from book2audio.attribution import (
    AttributionCache,
    HeuristicAttributor,
    LLMAttributor,
    LLMClient,
    require_llm_attribution_configured,
)
from book2audio.attribution.llm import prompt_fingerprint
from book2audio.models import SegmentKind

LLM_MODES = ("llm", "hybrid")
EXIT_UNRELIABLE_RUNS = 3


@dataclass
class RunResult:
    """One attribution pass over every gold book."""

    predictions: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0
    calls: int = 0
    cache_hits: int = 0
    windows_failed: int = 0
    lines_targeted: int = 0
    lines_in_scope: int = 0  # all dialogue lines in the chapters the LLM could see
    budget_exhausted: bool = False
    breaker_tripped: bool = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", required=True, choices=["test", "dev"])
    parser.add_argument("--gold", help="Gold JSON (default: gold/<split>.json)")
    parser.add_argument("--modes", default="heuristic,llm,hybrid")
    parser.add_argument("--runs", type=int, default=3, help="Runs per LLM mode (default 3)")
    parser.add_argument("--max-calls", type=int, default=150, help="Call budget per book per run")
    parser.add_argument("--model", help="Pinned model (default: FREELLMAPI_MODEL)")
    parser.add_argument("--seed", type=int, default=0, help="Bootstrap seed")
    parser.add_argument("--iterations", type=int, default=10_000, help="Bootstrap resamples")
    parser.add_argument("--out-dir", default=common.RESULTS_DIR)
    parser.add_argument(
        "--check-only", action="store_true", help="Only verify the gold file against the books"
    )
    args = parser.parse_args(argv)

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    unknown = [m for m in modes if m not in ("heuristic", *LLM_MODES)]
    if unknown or not modes:
        print(f"error: unknown mode(s) {unknown or modes}", file=sys.stderr)
        return 2
    if args.runs < 1:
        print("error: --runs must be at least 1", file=sys.stderr)
        return 2

    try:
        gold_path = args.gold or os.path.join(common.GOLD_DIR, f"{args.split}.json")
        gold = scoring.load_gold(gold_path)
        books = _load_verified_books(gold)
    except (scoring.GoldMismatchError, OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    scope = {
        slug: sorted({_chapter_of(g.id) for g in gold.lines if g.book == slug}) for slug in books
    }
    print(f"gold: {len(gold.lines)} line(s) in {scope}")
    if args.check_only:
        print("gold file matches the pinned books")
        return 0

    # Resolved only now: verifying a gold file must work without any credentials.
    try:
        model = _pinned_model(args.model) if any(m in LLM_MODES for m in modes) else None
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    heuristic, tiers = _run_heuristic(books)
    runs: dict[str, list[RunResult]] = {}
    for mode in modes:
        if mode == "heuristic":
            continue
        runs[mode] = []
        for n in range(1, args.runs + 1):
            print(f"running {mode} ({n}/{args.runs}) with model {model}")
            runs[mode].append(_run_llm(books, scope, mode, model, args.max_calls))

    summary = _summarize(gold, heuristic, tiers, runs, args.iterations, args.seed)
    reliable = _print_summary(gold, summary, model)
    path = _write_results(args, gold, model, modes, summary, heuristic, tiers, runs)
    print(f"raw per-line results: {path}")
    # Non-zero so a script or CI step cannot mistake a degraded run for a result.
    return 0 if reliable else EXIT_UNRELIABLE_RUNS


# --- setup ---------------------------------------------------------------------


def _pinned_model(cli_model: str | None) -> str:
    model = (cli_model or os.environ.get("FREELLMAPI_MODEL") or "").strip()
    if not model or model.lower() == "auto":
        raise ValueError(
            "refusing to evaluate with model 'auto' (or none): set FREELLMAPI_MODEL (or --model) "
            "to one concrete model so the result is attributable"
        )
    require_llm_attribution_configured()
    return model


def _chapter_of(gold_id: str) -> int:
    return int(gold_id.split(":", 1)[0])


def _load_verified_books(gold: scoring.Gold) -> dict[str, list]:
    """Parse each gold book, failing if the EPUB or any gold line no longer matches."""
    manifest = common.load_books_manifest()
    books: dict[str, list] = {}
    for slug, info in gold.books.items():
        book = manifest["books"][slug]
        path = common.epub_path(book)
        if not os.path.isfile(path):
            raise ValueError(f"{path} missing; run build_sheet.py download")
        scoring.verify_epub_hash(info["epub_sha256"], scoring.sha256_file(path), slug)
        chapters = common.load_chapters(path)
        current_ids = {d.id for d in common.dialogue_lines(slug, chapters)}
        scoring.verify_gold_matches_book([g for g in gold.lines if g.book == slug], current_ids)
        books[slug] = chapters
    return books


def _fresh(path_chapters: list) -> list:
    """Copy parsed chapters (re-parsing is slow; segments are plain dataclasses)."""
    return copy.deepcopy(path_chapters)


# --- attribution passes -----------------------------------------------------------


def _collect(slug: str, chapters: list) -> dict[str, str]:
    return {
        d.id: d.chapter.segments[d.position].speaker or ""
        for d in common.dialogue_lines(slug, chapters)
    }


def _run_heuristic(books: dict[str, list]) -> tuple[RunResult, dict[str, str]]:
    result, tiers = RunResult(), {}
    start = time.perf_counter()
    for slug, chapters in books.items():
        working = _fresh(chapters)
        attributor = HeuristicAttributor()
        attributor.attribute(working)
        result.predictions.update(_collect(slug, working))
        for d in common.dialogue_lines(slug, working):
            tiers[d.id] = attributor.tiers[(d.chapter.index, d.position)]
    result.seconds = time.perf_counter() - start
    return result, tiers


def _run_llm(
    books: dict[str, list], scope: dict[str, list[int]], mode: str, model: str, max_calls: int
) -> RunResult:
    result = RunResult()
    start = time.perf_counter()
    for slug, chapters in books.items():
        working = _fresh(chapters)
        only = frozenset(scope[slug])
        # A new in-memory cache per run: nothing can be replayed from an earlier run.
        attributor = LLMAttributor(
            mode,
            LLMClient(model=model),
            AttributionCache(None),
            max_calls,
            only_chapters=only,
        )
        attributor.attribute(working)
        report = attributor.report
        result.predictions.update(_collect(slug, working))
        result.calls += report.calls
        result.cache_hits += report.cache_hits
        result.windows_failed += report.windows_failed
        result.lines_targeted += report.lines_targeted
        result.budget_exhausted |= report.budget_exhausted
        result.breaker_tripped |= report.breaker_tripped
        result.lines_in_scope += sum(
            1
            for c in working
            if c.index in only
            for s in c.segments
            if s.kind == SegmentKind.DIALOGUE
        )
    result.seconds = time.perf_counter() - start
    return result


# --- summary -------------------------------------------------------------------------


def _ci(scored: scoring.Scored) -> dict:
    lo, hi = scored.interval()
    return {"accuracy": scored.accuracy, "n": scored.n, "wilson_lo": lo, "wilson_hi": hi}


def run_problems(result: RunResult) -> list[str]:
    """Why a run cannot be trusted as a measurement of the model, if it cannot."""
    problems = []
    if result.windows_failed:
        problems.append(f"{result.windows_failed} failed window(s)")
    if result.breaker_tripped:
        problems.append("circuit breaker tripped")
    if result.budget_exhausted:
        problems.append("call budget exhausted")
    return problems


def _diff(scored: scoring.Scored, base: scoring.Scored, iterations: int, seed: int) -> dict:
    """Difference vs the baseline: per-line bootstrap and window-clustered bootstrap."""
    out = {}
    for name, clusters in (("line", None), ("clustered", scored.clusters)):
        mean, lo, hi = scoring.paired_bootstrap_diff(
            scored.correct, base.correct, iterations, seed, clusters=clusters
        )
        out[name] = {"mean": mean, "lo": lo, "hi": hi}
    return out


def _summarize(gold, heuristic, tiers, runs, iterations, seed) -> dict:
    base = scoring.score_predictions(gold, heuristic.predictions)
    summary: dict = {
        "n_scored": base.n,
        "skipped": base.skipped,
        "heuristic": {**_ci(base), "tiers": _tier_table([base], tiers)},
    }
    scored_runs: dict[str, list[scoring.Scored]] = {}
    for mode, results in runs.items():
        scored_runs[mode] = [scoring.score_predictions(gold, r.predictions) for r in results]
        per_run = []
        for scored, result in zip(scored_runs[mode], results):
            fixed, broken = scoring.fixed_and_broken(base, scored)
            problems = run_problems(result)
            per_run.append(
                {
                    **_ci(scored),
                    "diff_vs_heuristic": _diff(scored, base, iterations, seed),
                    "fixed": fixed,
                    "broken": broken,
                    "fraction_sent": (
                        result.lines_targeted / result.lines_in_scope if result.lines_in_scope else 0
                    ),
                    "lines_targeted": result.lines_targeted,
                    "calls": result.calls,
                    "cache_hits": result.cache_hits,
                    "windows_failed": result.windows_failed,
                    "breaker_tripped": result.breaker_tripped,
                    "budget_exhausted": result.budget_exhausted,
                    "seconds": result.seconds,
                    "problems": problems,
                    "reliable": not problems,
                }
            )
        good = [sc for sc, r in zip(scored_runs[mode], per_run) if r["reliable"]]
        summary[mode] = {
            "runs": per_run,
            "reliable_runs": len(good),
            # The headline only counts runs where every window got an answer.
            "mean_accuracy": (
                sum(sc.accuracy for sc in good) / len(good) if good else None
            ),
            "tiers": _tier_table(good, tiers) if good else {},
        }
    if "llm" in scored_runs and "hybrid" in scored_runs:
        pairs = []
        for i, (h, l) in enumerate(zip(scored_runs["hybrid"], scored_runs["llm"])):
            ok = summary["hybrid"]["runs"][i]["reliable"] and summary["llm"]["runs"][i]["reliable"]
            pairs.append(
                {
                    **{
                        name: dict(zip(("mean", "lo", "hi"), scoring.paired_bootstrap_diff(
                            h.correct, l.correct, iterations, seed, clusters=cl
                        )))
                        for name, cl in (("line", None), ("clustered", h.clusters))
                    },
                    "reliable": ok,
                }
            )
        summary["hybrid_vs_llm"] = pairs
    return summary


def _tier_table(scored_runs: list[scoring.Scored], tiers: dict[str, str]) -> dict[str, dict]:
    """Per-tier accuracy, averaged over runs (a single run for the heuristic)."""
    totals: dict[str, list[float]] = {}
    for scored in scored_runs:
        for tier, (correct, total) in scored.by_tier(tiers).items():
            bucket = totals.setdefault(tier, [0.0, 0.0])
            bucket[0] += correct / len(scored_runs)
            bucket[1] = total
    return {
        tier: {"accuracy": c / t if t else 0.0, "n": int(t)}
        for tier, (c, t) in sorted(totals.items())
    }


def _pct(x: float) -> str:
    return f"{100 * x:5.1f}%"


def _pp(d: dict) -> str:
    return f"{100 * d['mean']:+.1f}pp [{100 * d['lo']:+.1f}, {100 * d['hi']:+.1f}]"


def _print_summary(gold: scoring.Gold, summary: dict, model: str | None) -> bool:
    """Print the report. Returns False if any LLM run was unreliable."""
    print(f"\nsplit={gold.split} scored={summary['n_scored']} SKIP={summary['skipped']} model={model}")
    h = summary["heuristic"]
    print(
        f"heuristic: {_pct(h['accuracy'])}  95% Wilson "
        f"[{_pct(h['wilson_lo'])}, {_pct(h['wilson_hi'])}]"
    )
    unreliable: list[str] = []
    for mode in LLM_MODES:
        if mode not in summary:
            continue
        for i, r in enumerate(summary[mode]["runs"], 1):
            d = r["diff_vs_heuristic"]
            flag = "" if r["reliable"] else "  <-- UNRELIABLE: " + ", ".join(r["problems"])
            print(
                f"{mode} run {i}: {_pct(r['accuracy'])} "
                f"[{_pct(r['wilson_lo'])}, {_pct(r['wilson_hi'])}] "
                f"vs heuristic line-bootstrap {_pp(d['line'])}, window-clustered {_pp(d['clustered'])} "
                f"fixed={r['fixed']} broken={r['broken']} sent={_pct(r['fraction_sent'])} "
                f"calls={r['calls']} failed_windows={r['windows_failed']} "
                f"breaker_tripped={r['breaker_tripped']} budget_exhausted={r['budget_exhausted']} "
                f"{r['seconds']:.0f}s{flag}"
            )
            if not r["reliable"]:
                unreliable.append(f"{mode} run {i}: {', '.join(r['problems'])}")
        mean = summary[mode]["mean_accuracy"]
        shown = "none (no reliable run)" if mean is None else _pct(mean)
        print(f"{mode} headline (mean of {summary[mode]['reliable_runs']} reliable run(s)): {shown}")

    if unreliable:
        bar = "!" * 72
        print(f"\n{bar}\nWARNING: {len(unreliable)} LLM run(s) did not get an answer for every window.")
        print("They are excluded from the headline and tier tables; do not quote them.")
        for line in unreliable:
            print(f"  - {line}")
        print(f"Exiting with status {EXIT_UNRELIABLE_RUNS}.\n{bar}")

    print("accuracy by heuristic tier (n), reliable runs only:")
    modes = ["heuristic", *[m for m in LLM_MODES if m in summary]]
    for tier in sorted(h["tiers"]):
        cells = []
        for m in modes:
            row = summary[m]["tiers"].get(tier)
            cells.append(f"{m}={_pct(row['accuracy']) if row else '  n/a '}")
        print(f"  {tier:<15} n={h['tiers'][tier]['n']:<4} {'  '.join(cells)}")
    return not unreliable


def _write_results(args, gold, model, modes, summary, heuristic, tiers, runs) -> str:
    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = []
    for g in gold.lines:
        record = {
            "id": g.id,
            "book": g.book,
            "gold": g.speaker,
            "tier": tiers.get(g.id),
            "heuristic": heuristic.predictions.get(g.id),
        }
        for mode, results in runs.items():
            record[mode] = [r.predictions.get(g.id) for r in results]
        lines.append(record)
    payload = {
        "meta": {
            "split": gold.split,
            "timestamp": stamp,
            "model": model,
            "prompt_sha256": prompt_fingerprint(),
            "modes": modes,
            "runs_per_llm_mode": args.runs,
            "max_calls_per_book": args.max_calls,
            "bootstrap_seed": args.seed,
            "bootstrap_iterations": args.iterations,
            "epub_sha256": {slug: info["epub_sha256"] for slug, info in gold.books.items()},
            "aliases": gold.aliases,
        },
        "summary": summary,
        "lines": lines,
    }
    path = os.path.join(args.out_dir, f"{stamp}-{gold.split}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    return path


if __name__ == "__main__":
    sys.exit(main())
