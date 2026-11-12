"""The lifecycle script only ever adds its own rule, and does nothing unless told to."""

from __future__ import annotations

import importlib.util
import pathlib
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "storage" / "set_voice_upload_lifecycle.py"
_SPEC = importlib.util.spec_from_file_location("set_voice_upload_lifecycle", _PATH)
script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(script)

OTHER = {"ID": "keep-me", "Status": "Enabled", "Filter": {"Prefix": "tmp/"}, "Expiration": {"Days": 30}}


def test_the_rule_expires_the_staging_prefix_after_a_day():
    assert script.RULE["Filter"] == {"Prefix": "voice-uploads/"}
    assert script.RULE["Expiration"] == {"Days": 1}


def test_other_rules_are_kept_and_an_older_copy_of_ours_is_replaced():
    stale = {**script.RULE, "Expiration": {"Days": 99}}

    rules = script.merged_rules([OTHER, stale])

    assert rules == [OTHER, script.RULE]


def test_a_bucket_with_no_rules_gets_just_ours():
    client = MagicMock()
    client.get_bucket_lifecycle_configuration.side_effect = ClientError(
        {"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "GetBucketLifecycleConfiguration"
    )

    assert script.existing_rules(client, "b") == []
    assert script.merged_rules([]) == [script.RULE]


def test_other_storage_errors_are_not_swallowed():
    client = MagicMock()
    client.get_bucket_lifecycle_configuration.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "GetBucketLifecycleConfiguration"
    )

    with pytest.raises(ClientError):
        script.existing_rules(client, "b")


@pytest.mark.parametrize("flag, expect_put", [([], False), (["--apply"], True)])
def test_nothing_is_changed_without_apply(monkeypatch, flag, expect_put, capsys):
    client = MagicMock()
    client.get_bucket_lifecycle_configuration.return_value = {"Rules": [OTHER]}
    monkeypatch.setattr(script.r2, "_client", lambda: client)
    monkeypatch.setattr(script.r2, "_bucket", lambda: "bucket")
    monkeypatch.setattr("sys.argv", ["script", *flag])

    assert script.main() == 0

    assert client.put_bucket_lifecycle_configuration.called is expect_put
    if expect_put:
        sent = client.put_bucket_lifecycle_configuration.call_args.kwargs["LifecycleConfiguration"]
        assert sent == {"Rules": [OTHER, script.RULE]}
