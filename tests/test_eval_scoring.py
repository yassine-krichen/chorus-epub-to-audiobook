"""Unit tests for evals/attribution/scoring.py (the evaluation statistics).

The module lives outside the package (evals are not shipped), so it is loaded
by path. It is standard-library only, so no fixtures or network are needed.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

_SCORING_PATH = pathlib.Path(__file__).resolve().parents[1] / "evals" / "attribution" / "scoring.py"
_spec = importlib.util.spec_from_file_location("attribution_eval_scoring", _SCORING_PATH)
scoring = importlib.util.module_from_spec(_spec)
# Dataclasses look their module up in sys.modules while being defined.
sys.modules[_spec.name] = scoring
_spec.loader.exec_module(scoring)


def _gold(pairs, aliases=None):
    return scoring.Gold(
        split="test",
        books={"b": {"epub_sha256": "x"}},
        aliases=aliases or {},
        lines=[scoring.GoldLine(id=i, book="b", speaker=s) for i, s in pairs],
    )


# --- line ids -------------------------------------------------------------------


def test_line_id_has_chapter_ordinal_and_an_8_char_hash():
    chapter, ordinal, digest = scoring.line_id(5, 12, "Who are you?").split(":")

    assert (chapter, ordinal) == ("5", "12")
    assert len(digest) == 8 and int(digest, 16) >= 0


def test_line_id_hash_ignores_whitespace_differences_but_not_wording():
    assert scoring.line_id(1, 1, "Who  are\nyou?") == scoring.line_id(1, 1, "Who are you?")
    assert scoring.line_id(1, 1, "Who are you?") != scoring.line_id(1, 1, "Who are you!")


# --- Wilson interval -----------------------------------------------------------------


def test_wilson_interval_matches_a_known_value():
    lo, hi = scoring.wilson_interval(8, 10)

    assert lo == pytest.approx(0.4902, abs=1e-3)
    assert hi == pytest.approx(0.9433, abs=1e-3)


def test_wilson_interval_stays_inside_zero_and_one_at_the_extremes():
    lo, hi = scoring.wilson_interval(10, 10)
    assert hi == pytest.approx(1.0) and 0.0 < lo < 1.0
    lo, hi = scoring.wilson_interval(0, 10)
    assert lo == pytest.approx(0.0) and 0.0 < hi < 1.0


def test_wilson_interval_with_no_trials_is_uninformative():
    assert scoring.wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_interval_narrows_as_the_sample_grows():
    small = scoring.wilson_interval(8, 10)
    large = scoring.wilson_interval(800, 1000)

    assert (large[1] - large[0]) < (small[1] - small[0])


# --- paired bootstrap -------------------------------------------------------------------


def test_bootstrap_is_reproducible_for_a_fixed_seed():
    a = [1, 1, 0, 1, 1, 0, 1, 1, 1, 0] * 5
    b = [1, 0, 0, 1, 0, 0, 1, 1, 0, 0] * 5

    first = scoring.paired_bootstrap_diff(a, b, iterations=500, seed=7)
    second = scoring.paired_bootstrap_diff(a, b, iterations=500, seed=7)

    assert first == second


def test_bootstrap_ci_brackets_the_observed_difference():
    a = [1] * 70 + [0] * 30
    b = [1] * 55 + [0] * 45

    diff, lo, hi = scoring.paired_bootstrap_diff(a, b, iterations=2000, seed=1)

    assert diff == pytest.approx(0.15)
    assert lo <= diff <= hi


def test_bootstrap_of_identical_results_is_exactly_zero():
    flags = [1, 0, 1, 1, 0] * 10

    assert scoring.paired_bootstrap_diff(flags, flags, iterations=200, seed=3) == (0.0, 0.0, 0.0)


def test_bootstrap_ci_excludes_zero_for_a_clear_improvement():
    a = [1] * 90 + [0] * 10
    b = [1] * 50 + [0] * 50

    _diff, lo, _hi = scoring.paired_bootstrap_diff(a, b, iterations=2000, seed=0)

    assert lo > 0


def test_bootstrap_rejects_unpaired_samples():
    with pytest.raises(ValueError, match="same length"):
        scoring.paired_bootstrap_diff([1, 0], [1])


# --- alias canonicalization ---------------------------------------------------------------


def test_names_compare_case_insensitively_ignoring_articles_and_punctuation():
    alias_map = scoring.build_alias_map({})

    assert scoring.canonicalize("The Hatter.", alias_map) == scoring.canonicalize("hatter", alias_map)


def test_aliases_fold_to_their_canonical_name():
    alias_map = scoring.build_alias_map({"Mad Hatter": ["Hatter", "The Hatter"]})

    assert scoring.canonicalize("the hatter", alias_map) == "mad hatter"
    assert scoring.canonicalize("Mad Hatter", alias_map) == "mad hatter"


def test_a_prediction_using_an_alias_scores_as_correct():
    gold = _gold([("1:1:a", "Mad Hatter")], aliases={"Mad Hatter": ["Hatter"]})

    scored = scoring.score_predictions(gold, {"1:1:a": "Hatter"})

    assert scored.correct == [1]


def test_unrelated_names_are_not_merged():
    gold = _gold([("1:1:a", "Alice")])

    assert scoring.score_predictions(gold, {"1:1:a": "Alison"}).correct == [0]


# --- SKIP exclusion and scoring -----------------------------------------------------------------


def test_skip_lines_are_excluded_from_the_denominator_and_counted():
    gold = _gold([("1:1:a", "Alice"), ("1:2:b", "SKIP"), ("1:3:c", "Hatter")])

    scored = scoring.score_predictions(gold, {"1:1:a": "Alice", "1:2:b": "Alice", "1:3:c": "Dodo"})

    assert scored.n == 2
    assert scored.skipped == 1
    assert scored.ids == ["1:1:a", "1:3:c"]
    assert scored.accuracy == 0.5


def test_skip_is_recognized_case_insensitively():
    assert scoring.score_predictions(_gold([("1:1:a", "skip")]), {}).skipped == 1


def test_a_missing_prediction_counts_as_wrong():
    scored = scoring.score_predictions(_gold([("1:1:a", "Alice")]), {})

    assert scored.correct == [0]


def test_narrator_is_a_scorable_label():
    gold = _gold([("1:1:a", "Narrator")])

    assert scoring.score_predictions(gold, {"1:1:a": "narrator"}).correct == [1]


def test_accuracy_by_tier_groups_lines_and_defaults_to_unknown():
    gold = _gold([("1:1:a", "A"), ("1:2:b", "A"), ("1:3:c", "A")])
    scored = scoring.score_predictions(gold, {"1:1:a": "A", "1:2:b": "B", "1:3:c": "A"})

    table = scored.by_tier({"1:1:a": "named_trailing", "1:2:b": "alternation"})

    assert table == {"named_trailing": (1, 1), "alternation": (0, 1), "unknown": (1, 1)}


def test_fixed_and_broken_counts_the_disagreements():
    gold = _gold([("1:1:a", "A"), ("1:2:b", "A"), ("1:3:c", "A"), ("1:4:d", "A")])
    base = scoring.score_predictions(gold, {"1:1:a": "A", "1:2:b": "A", "1:3:c": "B", "1:4:d": "B"})
    other = scoring.score_predictions(gold, {"1:1:a": "A", "1:2:b": "B", "1:3:c": "A", "1:4:d": "B"})

    assert scoring.fixed_and_broken(base, other) == (1, 1)


# --- hash mismatch is a hard failure ---------------------------------------------------------------


def test_a_gold_id_missing_from_the_current_parse_raises():
    lines = [scoring.GoldLine(id="5:1:deadbeef", book="b", speaker="Alice")]

    with pytest.raises(scoring.GoldMismatchError, match="1 gold line id"):
        scoring.verify_gold_matches_book(lines, {"5:1:cafef00d"})


def test_matching_gold_ids_verify_cleanly():
    lines = [scoring.GoldLine(id="5:1:cafef00d", book="b", speaker="Alice")]

    scoring.verify_gold_matches_book(lines, {"5:1:cafef00d", "5:2:00000000"})


def test_a_different_epub_edition_raises():
    with pytest.raises(scoring.GoldMismatchError, match="different edition"):
        scoring.verify_epub_hash("a" * 64, "b" * 64, "alice")


def test_the_same_epub_hash_verifies():
    scoring.verify_epub_hash("a" * 64, "a" * 64, "alice")


def test_gold_round_trips_through_json(tmp_path):
    gold = _gold([("1:1:a", "Alice")], aliases={"Alice": ["Alyce"]})
    path = tmp_path / "gold.json"
    path.write_text(json.dumps(gold.to_json()), encoding="utf-8")

    loaded = scoring.load_gold(str(path))

    assert loaded.lines == gold.lines
    assert loaded.aliases == {"Alice": ["Alyce"]}


# --- clustered bootstrap ------------------------------------------------------------


def test_window_cluster_groups_consecutive_lines_by_block_of_25():
    assert scoring.window_cluster("alice", "5:1:aaaaaaaa") == "alice:5:0"
    assert scoring.window_cluster("alice", "5:25:aaaaaaaa") == "alice:5:0"
    assert scoring.window_cluster("alice", "5:26:aaaaaaaa") == "alice:5:1"
    assert scoring.window_cluster("penrod", "5:1:aaaaaaaa") == "penrod:5:0"


def test_clustered_bootstrap_is_wider_than_line_level_when_gains_are_clumped():
    # The improvement sits entirely in 3 of 12 windows of 10 lines each.
    a, b, clusters = [], [], []
    for window in range(12):
        gain = window < 3
        a += [1] * 10 if gain else [1, 0] * 5
        b += [0] * 10 if gain else [1, 0] * 5
        clusters += [f"w{window}"] * 10

    _, line_lo, line_hi = scoring.paired_bootstrap_diff(a, b, iterations=2000, seed=4)
    mean, lo, hi = scoring.paired_bootstrap_diff(
        a, b, iterations=2000, seed=4, clusters=clusters
    )

    assert mean == pytest.approx(0.25)
    assert (hi - lo) > (line_hi - line_lo)


def test_clustered_bootstrap_is_reproducible_for_a_fixed_seed():
    a, b = [1, 0, 1, 1] * 6, [0, 0, 1, 0] * 6
    clusters = [f"w{i // 4}" for i in range(24)]

    first = scoring.paired_bootstrap_diff(a, b, iterations=300, seed=9, clusters=clusters)

    assert first == scoring.paired_bootstrap_diff(a, b, iterations=300, seed=9, clusters=clusters)


def test_clustered_bootstrap_rejects_a_cluster_list_of_the_wrong_length():
    with pytest.raises(ValueError, match="same length"):
        scoring.paired_bootstrap_diff([1, 0], [1, 0], clusters=["a"])


def test_scored_lines_carry_their_window_cluster():
    gold = scoring.Gold(
        split="t",
        books={},
        aliases={},
        lines=[
            scoring.GoldLine(id="5:1:aaaaaaaa", book="alice", speaker="A"),
            scoring.GoldLine(id="5:30:bbbbbbbb", book="alice", speaker="A"),
        ],
    )

    scored = scoring.score_predictions(gold, {})

    assert scored.clusters == ["alice:5:0", "alice:5:1"]
