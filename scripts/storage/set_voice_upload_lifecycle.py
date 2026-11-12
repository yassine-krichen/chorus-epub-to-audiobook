"""Expire abandoned voice-recording uploads after one day, with a bucket lifecycle rule.

Belt and braces for the cleanup job (`python -m book2audio.worker.cleanup`), which also deletes
these: a storage rule works even when the cleanup job is not scheduled.

NOT RUN by anything in this repo. It changes bucket configuration, so an operator runs it once,
on purpose:

    python scripts/storage/set_voice_upload_lifecycle.py            # prints the rules it would set
    python scripts/storage/set_voice_upload_lifecycle.py --apply    # sets them

It reads the same environment as the app (R2_BUCKET, R2_ACCOUNT_ID or R2_ENDPOINT_URL,
R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY). A lifecycle configuration is replaced as a whole by the
S3 API, so this script reads the existing rules first and only adds or replaces its own rule
(by ID), leaving every other rule as it is.
"""

from __future__ import annotations

import argparse
import json
import sys

from botocore.exceptions import ClientError

from book2audio.storage import r2

RULE_ID = "expire-abandoned-voice-uploads"
RULE = {
    "ID": RULE_ID,
    "Status": "Enabled",
    "Filter": {"Prefix": "voice-uploads/"},
    # R2's smallest expiry is one day.
    "Expiration": {"Days": 1},
    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
}


def merged_rules(existing: list[dict]) -> list[dict]:
    """``existing`` with this script's rule added, or replaced if it is already there."""
    return [rule for rule in existing if rule.get("ID") != RULE_ID] + [RULE]


def existing_rules(client, bucket: str) -> list[dict]:
    try:
        return list(client.get_bucket_lifecycle_configuration(Bucket=bucket).get("Rules", []))
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("NoSuchLifecycleConfiguration", "404"):
            return []
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="set the rule (default: only print)")
    args = parser.parse_args()

    client, bucket = r2._client(), r2._bucket()
    rules = merged_rules(existing_rules(client, bucket))
    print(json.dumps({"Rules": rules}, indent=2))
    if not args.apply:
        print("\nDry run: nothing was changed. Run again with --apply to set these rules.")
        return 0
    client.put_bucket_lifecycle_configuration(
        Bucket=bucket, LifecycleConfiguration={"Rules": rules}
    )
    print(f"\nSet {len(rules)} lifecycle rule(s) on {bucket}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
