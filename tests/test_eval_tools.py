"""Tests for the evaluation scripts in evals/attribution/ (sheets, gold, runner).

A tiny synthetic EPUB stands in for the Gutenberg books, so nothing is
downloaded and no model is called. The scripts live outside the package, so
their directory is put on sys.path.
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys
import zipfile

import pytest

_EVAL_DIR = pathlib.Path(__file__).resolve().parents[1] / "evals" / "attribution"
sys.path.insert(0, str(_EVAL_DIR))

import build_sheet  # noqa: E402
import common  # noqa: E402
import run_eval  # noqa: E402
import scoring  # noqa: E402

_CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

_OPF = """<?xml version="1.0"?>
<package version="2.0" unique-identifier="id" xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>Tiny Book</dc:title>
    <dc:creator opf:role="aut">An Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="c1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""

_CHAPTER = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Chapter 1: The Start</h1>
<p>A frail young man sat on a rusty bench across from the old police station,
cradling a cup of coffee in his hands and thinking hard about what was to come
next, and whether any single part of it had ever truly mattered at all, in the
end, to anyone who might one day happen to remember his name.</p>
<p>"Who goes there?" asked Sunny.</p>
<p>"A friend," replied Nephis.</p>
<p>"Prove it."</p>
</body></html>"""


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point the eval scripts at a tiny EPUB and scratch directories."""
    data = tmp_path / "data"
    data.mkdir()
    epub = data / "tiny.epub"
    with zipfile.ZipFile(epub, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("OEBPS/content.opf", _OPF)
        zf.writestr("OEBPS/Text/ch1.xhtml", _CHAPTER)

    chapters = common.load_chapters(str(epub))
    chapter_index = chapters[0].index
    manifest = {
        "books": {
            "tiny": {
                "title": "Tiny Book",
                "file": "tiny.epub",
                "url": "https://example.invalid/tiny.epub",
                "sha256": scoring.sha256_file(str(epub)),
                "cast": {str(chapter_index): ["Sunny", "Nephis"]},
            }
        },
        "splits": {"test": {"tiny": [chapter_index]}, "dev": {"tiny": [chapter_index]}},
    }
    books_json = tmp_path / "books.json"
    books_json.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(common, "DATA_DIR", str(data))
    monkeypatch.setattr(common, "SHEETS_DIR", str(tmp_path / "sheets"))
    monkeypatch.setattr(common, "GOLD_DIR", str(tmp_path / "gold"))
    monkeypatch.setattr(common, "RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(common, "BOOKS_JSON", str(books_json))
    return tmp_path


def _read_sheet(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_sheet(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=build_sheet.SHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _label(workspace, speakers):
    """Build the sheet, fill the speaker column from ``speakers``, return the path."""
    assert build_sheet.main(["sheets"]) == 0
    path = workspace / "sheets" / "test.csv"
    rows = _read_sheet(path)
    for row, speaker in zip(rows, speakers):
        row["speaker"] = speaker
    _write_sheet(path, rows)
    return path


# --- sheets -----------------------------------------------------------------------


def test_sheet_has_exactly_the_labelling_columns_and_blank_speakers(workspace):
    assert build_sheet.main(["sheets"]) == 0

    with open(workspace / "sheets" / "test.csv", encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh))
    rows = _read_sheet(workspace / "sheets" / "test.csv")

    assert header == ["id", "context_before", "line", "context_after", "speaker", "note"]
    assert len(rows) == 3
    assert all(r["speaker"] == "" for r in rows)


def test_sheet_never_contains_a_prediction(workspace):
    build_sheet.main(["sheets"])
    text = (workspace / "sheets" / "test.csv").read_text(encoding="utf-8-sig").lower()

    for forbidden in ("predict", "heuristic", "llm", "tier", "alternation", "named_trailing"):
        assert forbidden not in text
    # Even though the heuristic would read "Sunny" for line 1, nothing says so.
    assert all(r["speaker"] == "" for r in _read_sheet(workspace / "sheets" / "test.csv"))


def test_sheet_shows_context_and_the_chapter_cast_on_the_first_row(workspace):
    build_sheet.main(["sheets"])
    rows = _read_sheet(workspace / "sheets" / "test.csv")

    assert rows[0]["line"] == "Who goes there?"
    assert "asked Sunny" in rows[0]["context_after"]
    assert rows[0]["note"] == "CAST: Sunny; Nephis"
    assert rows[1]["note"] == ""
    assert rows[1]["id"].count(":") == 2


# --- gold ingestion ------------------------------------------------------------------


def test_gold_is_written_from_a_fully_labelled_sheet(workspace):
    _label(workspace, ["Sunny", "Nephis", "SKIP"])

    assert build_sheet.main(["gold", "--split", "test"]) == 0

    gold = scoring.load_gold(str(workspace / "gold" / "test.json"))
    assert [g.speaker for g in gold.lines] == ["Sunny", "Nephis", "SKIP"]
    assert gold.books["tiny"]["epub_sha256"]
    assert gold.aliases is not None


def test_gold_rejects_unlabelled_rows(workspace, capsys):
    _label(workspace, ["Sunny", "", ""])

    assert build_sheet.main(["gold", "--split", "test"]) == 1
    assert "no speaker yet" in capsys.readouterr().err


def test_gold_rejects_a_speaker_missing_from_the_cast_and_aliases_and_lists_the_rows(
    workspace, capsys
):
    _label(workspace, ["Sunny", "Nephiss", "Zorblax"])

    assert build_sheet.main(["gold", "--split", "test"]) == 1

    err = capsys.readouterr().err
    assert "2 row(s)" in err
    assert "'Nephiss'" in err and "'Zorblax'" in err
    assert "cast list" in err and "alias table" in err
    assert "'Sunny'" not in err


def test_gold_accepts_special_values_cast_names_and_aliases_in_any_case(workspace):
    _label(workspace, ["narrator", "NEPHIS", "skip"])

    assert build_sheet.main(["gold", "--split", "test"]) == 0


def test_gold_accepts_a_name_from_a_hand_edited_alias_table(workspace):
    _label(workspace, ["Sunny", "Nephis", "Cassie"])
    gold_path = workspace / "gold" / "test.json"
    gold_path.parent.mkdir()
    gold_path.write_text(
        json.dumps({"split": "test", "books": {}, "aliases": {"Cassie": ["Cass"]}, "lines": []}),
        encoding="utf-8",
    )

    assert build_sheet.main(["gold", "--split", "test"]) == 0

    assert scoring.load_gold(str(gold_path)).aliases == {"Cassie": ["Cass"]}


def test_gold_rejects_duplicate_ids(workspace, capsys):
    path = _label(workspace, ["Sunny", "Nephis", "Sunny"])
    rows = _read_sheet(path)
    rows[2]["id"] = rows[0]["id"]
    _write_sheet(path, rows)

    assert build_sheet.main(["gold", "--split", "test"]) == 1
    assert "duplicate" in capsys.readouterr().err


def test_gold_rejects_ids_that_no_longer_exist_in_the_book(workspace, capsys):
    path = _label(workspace, ["Sunny", "Nephis", "Sunny"])
    rows = _read_sheet(path)
    rows[0]["id"] = "1:1:deadbeef"
    _write_sheet(path, rows)

    assert build_sheet.main(["gold", "--split", "test"]) == 1
    assert "not in the pinned books" in capsys.readouterr().err


def test_loading_a_gold_file_with_duplicate_ids_fails(tmp_path):
    path = tmp_path / "gold.json"
    line = {"id": "1:1:aaaaaaaa", "book": "b", "speaker": "A", "note": ""}
    path.write_text(
        json.dumps({"split": "t", "books": {}, "aliases": {}, "lines": [line, dict(line)]}),
        encoding="utf-8",
    )

    with pytest.raises(scoring.GoldMismatchError, match="duplicate"):
        scoring.load_gold(str(path))


# --- download ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload=b"epub-bytes", fail=False):
        self._payload, self._fail = payload, fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        if self._fail:
            raise OSError("connection reset")
        return self._payload


def test_download_writes_through_a_part_file_and_renames(workspace, monkeypatch):
    (workspace / "data" / "tiny.epub").unlink()
    manifest = common.load_books_manifest()
    manifest["books"]["tiny"]["sha256"] = ""
    common.save_books_manifest(manifest)
    monkeypatch.setattr(build_sheet.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse())

    assert build_sheet.main(["download"]) == 0

    assert (workspace / "data" / "tiny.epub").read_bytes() == b"epub-bytes"
    assert list((workspace / "data").glob("*.part")) == []


def test_an_interrupted_download_leaves_no_file_that_looks_complete(workspace, monkeypatch, capsys):
    (workspace / "data" / "tiny.epub").unlink()
    manifest = common.load_books_manifest()
    manifest["books"]["tiny"]["sha256"] = ""
    common.save_books_manifest(manifest)
    monkeypatch.setattr(
        build_sheet.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse(fail=True)
    )

    assert build_sheet.main(["download"]) == 1

    assert not (workspace / "data" / "tiny.epub").exists()


def test_a_changed_download_hash_is_refused_without_repin(workspace, monkeypatch, capsys):
    (workspace / "data" / "tiny.epub").unlink()
    monkeypatch.setattr(build_sheet.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse())

    assert build_sheet.main(["download"]) == 1
    assert "Gutenberg regenerated" in capsys.readouterr().err


# --- run_eval: model pinning and credentials ----------------------------------------------


@pytest.fixture
def llm_env(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("FREELLMAPI_API_KEY", "sk-test-SECRET-9f3a")


@pytest.mark.parametrize("model", ["auto", "AUTO", " auto ", ""])
def test_pinned_model_refuses_auto_and_empty(monkeypatch, llm_env, model):
    monkeypatch.setenv("FREELLMAPI_MODEL", model)

    with pytest.raises(ValueError, match="auto"):
        run_eval._pinned_model(None)


def test_pinned_model_refuses_an_unset_model(monkeypatch, llm_env):
    monkeypatch.delenv("FREELLMAPI_MODEL", raising=False)

    with pytest.raises(ValueError, match="auto"):
        run_eval._pinned_model(None)


def test_pinned_model_accepts_a_concrete_model_from_env_or_flag(monkeypatch, llm_env):
    monkeypatch.setenv("FREELLMAPI_MODEL", "gpt-oss-120b")
    assert run_eval._pinned_model(None) == "gpt-oss-120b"
    assert run_eval._pinned_model("other-model") == "other-model"


def test_pinned_model_requires_credentials(monkeypatch):
    monkeypatch.delenv("FREELLMAPI_BASE_URL", raising=False)
    monkeypatch.delenv("FREELLMAPI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="FREELLMAPI_BASE_URL"):
        run_eval._pinned_model("some-model")


def _gold_file(workspace, speakers=("Sunny", "Nephis", "Sunny")):
    _label(workspace, list(speakers))
    assert build_sheet.main(["gold", "--split", "test"]) == 0
    return str(workspace / "gold" / "test.json")


def test_run_eval_refuses_auto_before_running_anything(workspace, monkeypatch, llm_env, capsys):
    gold = _gold_file(workspace)
    monkeypatch.setenv("FREELLMAPI_MODEL", "auto")
    monkeypatch.setattr(
        run_eval, "_run_llm", lambda *a, **kw: pytest.fail("must not call the model")
    )

    code = run_eval.main(["--split", "test", "--gold", gold, "--modes", "llm"])

    assert code == 1
    assert "refusing" in capsys.readouterr().err


def test_check_only_needs_no_credentials_or_model(workspace, monkeypatch, capsys):
    gold = _gold_file(workspace)
    for name in ("FREELLMAPI_BASE_URL", "FREELLMAPI_API_KEY", "FREELLMAPI_MODEL"):
        monkeypatch.delenv(name, raising=False)

    code = run_eval.main(["--split", "test", "--gold", gold, "--check-only"])

    assert code == 0
    assert "matches the pinned books" in capsys.readouterr().out


def test_check_only_still_fails_on_a_changed_epub(workspace, monkeypatch, capsys):
    gold = _gold_file(workspace)
    (workspace / "data" / "tiny.epub").write_bytes(b"different edition")
    monkeypatch.delenv("FREELLMAPI_API_KEY", raising=False)

    code = run_eval.main(["--split", "test", "--gold", gold, "--check-only"])

    assert code == 1
    assert "different edition" in capsys.readouterr().err


def test_heuristic_only_run_needs_no_credentials(workspace, monkeypatch, capsys):
    gold = _gold_file(workspace)
    for name in ("FREELLMAPI_BASE_URL", "FREELLMAPI_API_KEY", "FREELLMAPI_MODEL"):
        monkeypatch.delenv(name, raising=False)

    code = run_eval.main(
        ["--split", "test", "--gold", gold, "--modes", "heuristic", "--out-dir", str(workspace / "r")]
    )

    assert code == 0
    assert list((workspace / "r").glob("*.json"))


# --- run_eval: unreliable runs -------------------------------------------------------------


def _stub_runs(monkeypatch, problem=None):
    """Replace the attribution passes with canned results; ``problem`` breaks run 2."""
    calls = {"n": 0}

    def fake_llm(books, scope, mode, model, max_calls):
        calls["n"] += 1
        result = run_eval.RunResult(lines_targeted=3, lines_in_scope=3, calls=2)
        result.predictions = {
            d.id: "Sunny" for slug, chapters in books.items() for d in common.dialogue_lines(slug, chapters)
        }
        if problem and calls["n"] == 2:
            setattr(result, problem, True if problem != "windows_failed" else 2)
        return result

    monkeypatch.setattr(run_eval, "_run_llm", fake_llm)


def _run_main(workspace, monkeypatch, llm_env, extra=()):
    gold = _gold_file(workspace)
    monkeypatch.setenv("FREELLMAPI_MODEL", "pinned-model")
    return run_eval.main(
        [
            "--split", "test", "--gold", gold, "--modes", "llm", "--runs", "3",
            "--iterations", "50", "--out-dir", str(workspace / "r"), *extra,
        ]
    )


def test_clean_runs_exit_zero_and_report_the_failure_counters(
    workspace, monkeypatch, llm_env, capsys
):
    _stub_runs(monkeypatch)

    code = _run_main(workspace, monkeypatch, llm_env)

    out = capsys.readouterr().out
    assert code == 0
    assert "failed_windows=0" in out and "breaker_tripped=False" in out
    assert "budget_exhausted=False" in out
    assert "WARNING" not in out


@pytest.mark.parametrize("problem", ["breaker_tripped", "budget_exhausted", "windows_failed"])
def test_an_unreliable_run_warns_loudly_is_excluded_and_exits_non_zero(
    workspace, monkeypatch, llm_env, capsys, problem
):
    _stub_runs(monkeypatch, problem)

    code = _run_main(workspace, monkeypatch, llm_env)

    out = capsys.readouterr().out
    assert code == run_eval.EXIT_UNRELIABLE_RUNS != 0
    assert "WARNING: 1 LLM run(s)" in out
    assert "llm run 2" in out and "UNRELIABLE" in out
    assert "mean of 2 reliable run(s)" in out
    results = json.loads(next((workspace / "r").glob("*.json")).read_text(encoding="utf-8"))
    runs = results["summary"]["llm"]["runs"]
    assert [r["reliable"] for r in runs] == [True, False, True]
    assert results["summary"]["llm"]["reliable_runs"] == 2


def test_when_no_run_is_reliable_the_headline_is_withheld(workspace, monkeypatch, llm_env, capsys):
    def fake_llm(books, scope, mode, model, max_calls):
        return run_eval.RunResult(breaker_tripped=True)

    monkeypatch.setattr(run_eval, "_run_llm", fake_llm)

    code = _run_main(workspace, monkeypatch, llm_env)

    out = capsys.readouterr().out
    assert code == run_eval.EXIT_UNRELIABLE_RUNS
    assert "none (no reliable run)" in out
