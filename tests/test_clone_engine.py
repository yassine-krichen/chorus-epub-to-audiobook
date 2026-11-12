"""The clone engine client and the routing engine, against a stub engine process.

The stub speaks the same JSON-lines protocol as ``clone_worker.py`` but needs no GPU: it
writes a sine wave. That exercises the real subprocess plumbing (start, reply parsing,
timeouts, result validation, fallback) without torch.
"""

from __future__ import annotations

import hashlib
import sys
import textwrap
import uuid

import numpy as np
import pytest
import soundfile as sf

import book2audio.tts.clone_engine as clone_engine
from book2audio.tts.clone_engine import (
    FALLBACK_REASONS,
    REASON_NOT_INSTALLED,
    REASON_OOM,
    REASON_RECORDING,
    REASON_STOPPED,
    REASON_TIMEOUT,
    REASON_VOICE_GONE,
    CloneCapability,
    CloneEngineError,
    CloneReference,
    QwenCloneEngine,
    RoutingEngine,
    prune_prompt_cache,
)
from book2audio.tts.voices import clone_voice_id, is_clone_voice, is_known_voice, parse_clone_voice

STUB = textwrap.dedent(
    '''
    import json, os, sys, time
    import numpy as np
    import soundfile as sf

    args = sys.argv[1:]
    workdir = args[args.index("--workdir") + 1]
    mode = os.environ.get("STUB_MODE", "ok")
    proto = os.fdopen(os.dup(1), "w")
    def reply(m):
        proto.write(json.dumps(m) + "\\n"); proto.flush()
    reply({"ok": True, "ready": True})
    for line in sys.stdin:
        req = json.loads(line)
        if req["op"] == "shutdown":
            reply({"ok": True}); break
        if req["op"] != "synthesize":
            reply({"ok": True}); continue
        if mode == "oom":
            reply({"ok": False, "error": "oom", "detail": "CUDA out of memory", "id": req["id"]}); continue
        if mode == "oom_long":
            secret = "C:/Users/someone/venv/torch/cuda.py token=sk-123 " + "x" * 5000
            reply({"ok": False, "error": "oom", "detail": secret, "id": req["id"]}); continue
        if mode == "bad_request":
            reply({"ok": False, "error": "bad_request", "detail": "reference clip not found", "id": req["id"]}); continue
        if mode == "crash":
            sys.exit(3)
        if mode == "hang":
            time.sleep(60)
        out = os.path.join(workdir, "out", req["out"])
        if mode == "wrong_rate":
            sf.write(out, np.zeros(2400, dtype="float32"), 16000)
        elif mode == "nan":
            sf.write(out, np.full(2400, np.nan, dtype="float32"), 24000, subtype="FLOAT")
        else:
            t = np.arange(24000) / 24000
            sf.write(out, (0.3*np.sin(2*np.pi*220*t)).astype("float32"), 24000)
        reply({"ok": True, "id": req["id"] + (1 if mode == "wrong_id" else 0), "audio_s": 1.0})
    '''
)


@pytest.fixture
def stub_engine(tmp_path, monkeypatch):
    script = tmp_path / "stub_worker.py"
    script.write_text(STUB, encoding="utf-8")
    monkeypatch.setattr(clone_engine, "_WORKER_SCRIPT", script)
    monkeypatch.setenv("CLONE_PROMPT_CACHE_DIR", str(tmp_path / "cache"))
    engines: list[QwenCloneEngine] = []

    def make() -> QwenCloneEngine:
        engine = QwenCloneEngine(sys.executable, str(tmp_path))
        engine.synth_timeout_s = 5
        engines.append(engine)
        return engine

    yield make
    for engine in engines:
        engine.close()


def _reference(tmp_path, gender="f") -> CloneReference:
    wav = tmp_path / "ref.wav"
    sf.write(str(wav), np.zeros(24000, dtype="float32"), 24000)
    digest = hashlib.sha256(wav.read_bytes()).hexdigest()
    return CloneReference(
        voice_id=clone_voice_id(uuid.uuid4()),
        wav_path=str(wav),
        ref_text="a passage",
        sha256=digest,
        gender=gender,
    )


class FakeKokoro:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray:
        self.calls.append((text, voice))
        return np.ones(100, dtype=np.float32)


def test_voice_id_helpers_accept_only_well_formed_clone_ids():
    voice_uuid = uuid.uuid4()
    voice = clone_voice_id(voice_uuid)

    assert parse_clone_voice(voice) == voice_uuid
    assert is_clone_voice(voice)
    assert is_known_voice(voice)
    assert is_known_voice("af_heart")
    for bad in ("clone:", "clone:not-a-uuid", f"{voice}/../x", f" {voice}", voice.upper(), "xx"):
        assert not is_known_voice(bad), bad


def test_the_client_returns_validated_24khz_audio(stub_engine, tmp_path):
    engine = stub_engine()

    samples = engine.synthesize_clone("Hello there.", _reference(tmp_path))

    assert samples.dtype == np.float32 and samples.ndim == 1
    assert len(samples) == 24000


def test_long_text_is_split_into_several_requests(stub_engine, tmp_path):
    engine = stub_engine()
    text = " ".join(["This is a sentence of moderate length."] * 30)

    samples = engine.synthesize_clone(text, _reference(tmp_path))

    assert len(samples) > 24000  # more than one chunk, each one second in the stub


@pytest.mark.parametrize("mode", ["oom", "crash", "wrong_rate", "nan", "wrong_id"])
def test_a_broken_engine_raises_instead_of_returning_bad_audio(
    stub_engine, tmp_path, monkeypatch, mode
):
    monkeypatch.setenv("STUB_MODE", mode)
    engine = stub_engine()

    with pytest.raises(CloneEngineError):
        engine.synthesize_clone("Hello there.", _reference(tmp_path))


def test_a_hanging_engine_times_out(stub_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "hang")
    engine = stub_engine()
    engine.synth_timeout_s = 1

    with pytest.raises(CloneEngineError, match="timed out"):
        engine.synthesize_clone("Hello there.", _reference(tmp_path))


def test_a_reference_clip_that_does_not_match_its_hash_is_refused(stub_engine, tmp_path):
    engine = stub_engine()
    reference = _reference(tmp_path)
    tampered = CloneReference(
        reference.voice_id, reference.wav_path, reference.ref_text, "0" * 64, reference.gender
    )

    with pytest.raises(CloneEngineError, match="hash"):
        engine.synthesize_clone("Hello.", tampered)


def test_close_removes_the_temp_directory_and_stops_the_process(stub_engine, tmp_path):
    engine = stub_engine()
    engine.synthesize_clone("Hello.", _reference(tmp_path))
    process = engine._process
    workdir = engine._workdir

    engine.close()

    assert process.poll() is not None
    assert not workdir.exists()


def test_routing_sends_kokoro_voices_to_kokoro_and_clone_voices_to_the_clone_engine(
    stub_engine, tmp_path
):
    kokoro = FakeKokoro()
    reference = _reference(tmp_path)
    router = RoutingEngine(kokoro, {reference.voice_id: reference}, stub_engine())

    router.synthesize("Plain.", "af_heart")
    cloned = router.synthesize("Cloned.", reference.voice_id)

    assert kokoro.calls == [("Plain.", "af_heart")]
    assert len(cloned) == 24000
    assert router.fallbacks == {} and router.fallback_count == 0


def test_routing_falls_back_to_a_kokoro_voice_of_the_same_gender_when_the_engine_fails(
    stub_engine, tmp_path, monkeypatch
):
    monkeypatch.setenv("STUB_MODE", "oom")
    kokoro = FakeKokoro()
    female = _reference(tmp_path, gender="f")
    router = RoutingEngine(kokoro, {female.voice_id: female}, stub_engine())

    audio = router.synthesize("Cloned.", female.voice_id)

    assert len(audio) == 100  # Kokoro's
    assert kokoro.calls[0][1].startswith("af_")
    assert router.fallbacks[female.voice_id] == REASON_OOM
    assert router.fallback_count == 1


def test_after_a_failure_the_clone_engine_is_not_tried_again(stub_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "oom")
    kokoro = FakeKokoro()
    reference = _reference(tmp_path)
    engine = stub_engine()
    router = RoutingEngine(kokoro, {reference.voice_id: reference}, engine)

    router.synthesize("One.", reference.voice_id)
    requests_before = engine._next_id
    router.synthesize("Two.", reference.voice_id)

    assert engine._next_id == requests_before
    assert router.fallback_count == 2
    assert router.resolve_voice(reference.voice_id).startswith("af_")


def test_routing_without_an_engine_uses_kokoro_and_says_why(tmp_path):
    kokoro = FakeKokoro()
    reference = _reference(tmp_path, gender="m")
    router = RoutingEngine(kokoro, {reference.voice_id: reference}, None)

    router.synthesize("Cloned.", reference.voice_id)

    assert kokoro.calls[0][1].startswith("am_")
    assert router.fallbacks[reference.voice_id] == REASON_NOT_INSTALLED


def test_a_voice_that_no_longer_exists_falls_back_and_says_so():
    kokoro = FakeKokoro()
    gone = clone_voice_id(uuid.uuid4())
    router = RoutingEngine(kokoro, {}, None)

    router.synthesize("Cloned.", gone)

    assert router.fallbacks[gone] == REASON_VOICE_GONE


def test_resolve_voice_reports_the_clone_voice_only_when_it_can_be_spoken(stub_engine, tmp_path):
    reference = _reference(tmp_path)
    with_engine = RoutingEngine(FakeKokoro(), {reference.voice_id: reference}, stub_engine())
    without = RoutingEngine(FakeKokoro(), {reference.voice_id: reference}, None)

    assert with_engine.resolve_voice(reference.voice_id) == reference.voice_id
    assert without.resolve_voice(reference.voice_id) != reference.voice_id
    assert without.resolve_voice("bm_lewis") == "bm_lewis"


def test_capability_is_off_without_configuration(monkeypatch):
    monkeypatch.delenv("CLONE_PYTHON", raising=False)
    monkeypatch.delenv("CLONE_WEIGHTS_DIR", raising=False)

    capability = clone_engine.probe_clone_capability()

    assert capability == CloneCapability(False, "CLONE_PYTHON and CLONE_WEIGHTS_DIR are not set")
    assert clone_engine.load_clone_engine() is None


def test_capability_needs_real_weights(monkeypatch, tmp_path):
    monkeypatch.setenv("CLONE_PYTHON", sys.executable)
    monkeypatch.setenv("CLONE_WEIGHTS_DIR", str(tmp_path))

    assert "no Qwen3-TTS weights" in clone_engine.probe_clone_capability().reason


def test_capability_requires_cuda_in_the_probe(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "model.safetensors").write_bytes(b"x")
    probe = tmp_path / "probe.py"
    probe.write_text('import json; print(json.dumps({"ok": True, "cuda": False}))')
    monkeypatch.setattr(clone_engine, "_WORKER_SCRIPT", probe)
    monkeypatch.setenv("CLONE_PYTHON", sys.executable)
    monkeypatch.setenv("CLONE_WEIGHTS_DIR", str(tmp_path))

    assert "no CUDA" in clone_engine.probe_clone_capability().reason

    probe.write_text('import json; print(json.dumps({"ok": True, "cuda": True, "device": "GPU X"}))')
    assert clone_engine.probe_clone_capability() == CloneCapability(True, "", "GPU X")


def test_pruning_deletes_prompts_of_voices_that_no_longer_exist(tmp_path):
    keep, drop = "a" * 64, "b" * 64
    (tmp_path / f"{keep}.safetensors").write_bytes(b"1")
    (tmp_path / f"{drop}.safetensors").write_bytes(b"2")
    (tmp_path / "notes.txt").write_text("not a prompt")

    removed = prune_prompt_cache({keep}, tmp_path)

    assert removed == 1
    assert (tmp_path / f"{keep}.safetensors").exists()
    assert not (tmp_path / f"{drop}.safetensors").exists()
    assert (tmp_path / "notes.txt").exists()


# --- what a user may be told -------------------------------------------------------------


def _reason_of(stub_engine, tmp_path, monkeypatch, mode):
    monkeypatch.setenv("STUB_MODE", mode)
    engine = stub_engine()
    with pytest.raises(CloneEngineError) as caught:
        engine.synthesize_clone("Hello there.", _reference(tmp_path))
    return caught.value


@pytest.mark.parametrize(
    "mode, reason",
    [
        ("oom", REASON_OOM),
        ("oom_long", REASON_OOM),
        ("crash", REASON_STOPPED),
        ("wrong_rate", REASON_STOPPED),
        ("nan", REASON_STOPPED),
        ("wrong_id", REASON_STOPPED),
        ("bad_request", REASON_RECORDING),
    ],
)
def test_every_engine_failure_maps_to_one_of_the_fixed_reasons(
    stub_engine, tmp_path, monkeypatch, mode, reason
):
    error = _reason_of(stub_engine, tmp_path, monkeypatch, mode)

    assert error.reason == reason
    assert error.reason in FALLBACK_REASONS


def test_a_hung_engine_is_reported_as_timed_out(stub_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "hang")
    engine = stub_engine()
    engine.synth_timeout_s = 1

    with pytest.raises(CloneEngineError) as caught:
        engine.synthesize_clone("Hello there.", _reference(tmp_path))

    assert caught.value.reason == REASON_TIMEOUT


def test_an_engine_that_cannot_start_is_reported_as_not_installed(tmp_path):
    engine = QwenCloneEngine(str(tmp_path / "no-such-python.exe"), str(tmp_path))
    try:
        with pytest.raises(CloneEngineError) as caught:
            engine.synthesize_clone("Hello there.", _reference(tmp_path))
    finally:
        engine.close()

    assert caught.value.reason == REASON_NOT_INSTALLED


def test_a_tampered_reference_clip_is_reported_as_an_unusable_recording(stub_engine, tmp_path):
    engine = stub_engine()
    reference = _reference(tmp_path)
    tampered = CloneReference(
        reference.voice_id, reference.wav_path, reference.ref_text, "0" * 64, reference.gender
    )

    with pytest.raises(CloneEngineError) as caught:
        engine.synthesize_clone("Hello.", tampered)

    assert caught.value.reason == REASON_RECORDING


def test_the_raw_engine_message_never_reaches_the_recorded_fallback(
    stub_engine, tmp_path, monkeypatch
):
    monkeypatch.setenv("STUB_MODE", "oom_long")
    reference = _reference(tmp_path)
    router = RoutingEngine(FakeKokoro(), {reference.voice_id: reference}, stub_engine())

    router.synthesize("Hello there.", reference.voice_id)
    router.synthesize("Hello again.", reference.voice_id)

    assert router.fallbacks == {reference.voice_id: REASON_OOM}
    assert "someone" not in str(router.fallbacks) and "sk-123" not in str(router.fallbacks)
    assert router.resolve_voice(reference.voice_id).startswith("af_")
