"""Which capabilities are online: local workers record a heartbeat, the API reads it.

A worker row is a sign of life plus the list of things it can do (for example
``clone:qwen3``). A capability counts as online while some worker with it has been seen
within :data:`HEARTBEAT_TTL`; the API uses that to decide whether a cloned voice can be used
right now, and the local daemon uses it to claim only the jobs it can run.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from book2audio.db.models import WorkerHeartbeat

# A daemon beats every ~20 s; this leaves room for two missed beats.
HEARTBEAT_TTL = timedelta(seconds=90)


def record_heartbeat(session: Session, worker_id: str, capabilities: list[str]) -> None:
    now = datetime.now(timezone.utc)
    row = session.get(WorkerHeartbeat, worker_id)
    if row is None:
        session.add(WorkerHeartbeat(worker_id=worker_id, capabilities=capabilities, last_seen_at=now))
    else:
        row.capabilities = capabilities
        row.last_seen_at = now
    session.commit()


def last_seen_with_capability(session: Session, capability: str) -> datetime | None:
    """When a worker offering ``capability`` was last heard from, however long ago; else ``None``."""
    latest: datetime | None = None
    for row in session.scalars(select(WorkerHeartbeat)):
        if capability not in (str(c) for c in row.capabilities):
            continue
        seen = row.last_seen_at if row.last_seen_at.tzinfo else row.last_seen_at.replace(tzinfo=timezone.utc)
        if latest is None or seen > latest:
            latest = seen
    return latest


def online_capabilities(session: Session) -> set[str]:
    """Every capability offered by a worker seen within the last :data:`HEARTBEAT_TTL`."""
    cutoff = datetime.now(timezone.utc) - HEARTBEAT_TTL
    online: set[str] = set()
    for row in session.scalars(select(WorkerHeartbeat)):
        seen = row.last_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        if seen >= cutoff:
            online.update(str(c) for c in row.capabilities)
    return online
