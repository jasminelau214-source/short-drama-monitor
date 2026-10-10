"""Temporary SQLite fixture for durable reservation/recovery tests only.

No production configuration, provider client, queue or Worker is imported.
Only explicit .sqlite3 paths below the OS temporary directory are accepted.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from drama_identity_v2 import IdentityReviewRequired
from research_attempt_policy_v2 import CallCheckpoint, prepare_due_retry, record_call_result
from research_spend_reservation_v2 import (
    OfflineSpendReservationLedger, ResearchTaskSnapshot, ReservationDecision,
    SpendAttemptCandidate, attempt_reservation_key, reservation_context_reason,
)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _checkpoint_json(checkpoint):
    data = asdict(checkpoint)
    data["retry_at"] = checkpoint.retry_at.isoformat() if checkpoint.retry_at else None
    return _json(data)


def _checkpoint(raw):
    data = json.loads(raw)
    data["retry_at"] = datetime.fromisoformat(data["retry_at"]) if data["retry_at"] else None
    return CallCheckpoint(**data)


def _provider_binding(provider):
    return _json([provider.provider_id, provider.review_ref, provider.approval_id,
                  provider.source_url, provider.artifact_sha256,
                  provider.checked_at.isoformat(), provider.valid_until.isoformat()])


@dataclass(frozen=True)
class StoreDecision:
    accepted: bool
    reason: str
    current_revision: int | None = None
    checkpoint: CallCheckpoint | None = None


class IsolatedReservationStore:
    """Disk-backed synthetic adapter; never authorizes a real external call."""

    def __init__(self, fixture_path, *, synthetic_offline_only):
        path = Path(fixture_path)
        if (synthetic_offline_only is not True or not path.is_absolute()
                or path.suffix != ".sqlite3"
                or not path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())):
            raise IdentityReviewRequired("explicit temporary synthetic SQLite fixture required")
        self.path = path.resolve()
        with self._transaction() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and tables != {"fixture_meta", "tasks", "attempts"}:
                raise IdentityReviewRequired("unrecognized database; refusing fixture initialization")
            if not tables:
                db.execute("CREATE TABLE fixture_meta (scope TEXT PRIMARY KEY)")
                db.execute("INSERT INTO fixture_meta VALUES ('JSM_B10_SYNTHETIC_ONLY_V1')")
                db.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision>=0), payload TEXT NOT NULL)")
                db.execute("""CREATE TABLE attempts (
                    reservation_key TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    stage TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL CHECK(attempt_number BETWEEN 1 AND 3),
                    status TEXT NOT NULL CHECK(status IN ('RESERVED','DISPATCH_STARTED','RETRY_WAIT','RETRY_READY',
                        'SUCCEEDED','DEFERRED_FREE_QUOTA','FAILED','NEEDS_GPT','REVIEW_REQUIRED')),
                    reserved_revision INTEGER NOT NULL,
                    owner_token TEXT,
                    checkpoint_json TEXT NOT NULL,
                    provider_binding TEXT NOT NULL,
                    outcome_digest TEXT,
                    UNIQUE(task_id,stage,attempt_number))""")
            marker = list(db.execute("SELECT scope FROM fixture_meta"))
            if [row[0] for row in marker] != ["JSM_B10_SYNTHETIC_ONLY_V1"]:
                raise IdentityReviewRequired("fixture scope marker mismatch")

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _read_task(db, task_id):
        row = db.execute("SELECT revision,payload FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        data = json.loads(row["payload"])
        data["reserved_attempt_keys"] = frozenset(data["reserved_attempt_keys"])
        snapshot = ResearchTaskSnapshot(**data)
        if snapshot.task_id != task_id or snapshot.revision != row["revision"]:
            raise IdentityReviewRequired("stored task revision mismatch")
        return snapshot

    @staticmethod
    def _payload(snapshot):
        data = asdict(snapshot)
        data["reserved_attempt_keys"] = sorted(snapshot.reserved_attempt_keys)
        return _json(data)

    def _update_task(self, db, current, updated):
        changed = db.execute("UPDATE tasks SET revision=?,payload=? WHERE task_id=? AND revision=?",
                             (updated.revision, self._payload(updated), current.task_id, current.revision))
        if changed.rowcount != 1:
            raise IdentityReviewRequired("task compare-and-set failed")

    def seed_synthetic_task(self, snapshot):
        if not isinstance(snapshot, ResearchTaskSnapshot) or snapshot.reserved_attempt_keys or snapshot.state != "PENDING":
            raise IdentityReviewRequired("fresh typed synthetic PENDING task required")
        with self._transaction() as db:
            db.execute("INSERT INTO tasks VALUES (?,?,?)", (snapshot.task_id, snapshot.revision, self._payload(snapshot)))

    def snapshot(self, task_id):
        with self._transaction() as db:
            return self._read_task(db, task_id)

    def reserve(self, candidate):
        if not isinstance(candidate, SpendAttemptCandidate):
            raise IdentityReviewRequired("typed spend candidate required")
        key = attempt_reservation_key(candidate.task_id, candidate.stage, candidate.attempt_number)
        with self._transaction() as db:
            current = self._read_task(db, candidate.task_id)
            if current is None:
                return ReservationDecision(False, "TASK_NOT_FOUND")
            model = OfflineSpendReservationLedger([current])
            decision = model.reserve(candidate)
            if not decision.authorized:
                return decision
            active = db.execute("SELECT 1 FROM attempts WHERE task_id=? AND status IN ('RESERVED','DISPATCH_STARTED')",
                                (current.task_id,)).fetchone()
            if active:
                return ReservationDecision(False, "ANOTHER_ATTEMPT_IN_FLIGHT", key, current.revision)
            if candidate.attempt_number > 1:
                previous = db.execute("SELECT * FROM attempts WHERE task_id=? AND stage=? AND attempt_number=?",
                                      (current.task_id, candidate.stage, candidate.attempt_number - 1)).fetchone()
                if (previous is None or previous["status"] != "RETRY_READY"
                        or previous["checkpoint_json"] != _checkpoint_json(candidate.retry_checkpoint)):
                    return ReservationDecision(False, "DURABLE_RETRY_CHECKPOINT_MISMATCH", key, current.revision)
                checkpoint = candidate.retry_checkpoint
            else:
                checkpoint = CallCheckpoint(candidate.stage, checkpoint_id="checkpoint:" + key)
            updated = model.snapshot(current.task_id)
            self._update_task(db, current, updated)
            db.execute("""INSERT INTO attempts
                (reservation_key,task_id,stage,attempt_number,status,reserved_revision,checkpoint_json,provider_binding)
                VALUES (?,?,?,?,'RESERVED',?,?,?)""",
                       (key, current.task_id, candidate.stage, candidate.attempt_number, updated.revision,
                        _checkpoint_json(checkpoint), _provider_binding(candidate.provider_approval)))
            return decision

    def supersede_authority(self, task_id, *, run_id, snapshot_sha256):
        with self._transaction() as db:
            current = self._read_task(db, task_id)
            if current is None:
                return False
            model = OfflineSpendReservationLedger([current])
            model.supersede_authority(task_id, run_id=run_id, snapshot_sha256=snapshot_sha256)
            self._update_task(db, current, model.snapshot(task_id))
            return True

    def begin_simulated_handoff(self, key, *, owner_token, expected_revision, preflight, provider, now):
        """Persist an exclusive handoff marker; this performs no API request.

        Initial reservation already moves the task to RESEARCHING. Handoff
        rechecks the supplied authority/provider context, not initial enqueue
        eligibility. A real adapter must obtain it from protected live stores.
        """
        if (not isinstance(owner_token, str) or not owner_token.strip()
                or type(expected_revision) is not int
                or not isinstance(now, datetime) or now.utcoffset() is None):
            raise IdentityReviewRequired("exact handoff owner, revision and aware time required")
        with self._transaction() as db:
            attempt = db.execute("SELECT * FROM attempts WHERE reservation_key=?", (key,)).fetchone()
            if attempt is None or attempt["status"] != "RESERVED":
                return StoreDecision(False, "HANDOFF_NOT_RESERVABLE")
            current = self._read_task(db, attempt["task_id"])
            if current.state != "RESEARCHING" or current.revision != expected_revision or current.revision != attempt["reserved_revision"]:
                return StoreDecision(False, "HANDOFF_TASK_CHANGED", current.revision)
            reason = reservation_context_reason(current, preflight, provider, now)
            if reason or _provider_binding(provider) != attempt["provider_binding"]:
                return StoreDecision(False, reason or "HANDOFF_PROVIDER_BINDING_CHANGED", current.revision)
            db.execute("UPDATE attempts SET status='DISPATCH_STARTED',owner_token=? WHERE reservation_key=?", (owner_token, key))
            updated = replace(current, revision=current.revision + 1)
            self._update_task(db, current, updated)
            return StoreDecision(True, "SIMULATED_HANDOFF_MARKED_ONLY", updated.revision)

    def record_simulated_outcome(self, key, *, owner_token, outcome, now, attempt_active_seconds=0):
        """Store a synthetic response and checkpoint atomically, once per key."""
        digest = hashlib.sha256(_json([outcome, attempt_active_seconds]).encode()).hexdigest()
        with self._transaction() as db:
            attempt = db.execute("SELECT * FROM attempts WHERE reservation_key=?", (key,)).fetchone()
            if attempt is None or attempt["owner_token"] != owner_token:
                return StoreDecision(False, "OUTCOME_OWNER_MISMATCH")
            if attempt["outcome_digest"] is not None:
                return StoreDecision(False, "OUTCOME_ALREADY_RECORDED" if attempt["outcome_digest"] == digest else "OUTCOME_CONFLICT")
            current = self._read_task(db, attempt["task_id"])
            # A newer authority or any other task mutation invalidates writeback.
            if attempt["status"] != "DISPATCH_STARTED" or current.revision != attempt["reserved_revision"] + 1:
                return StoreDecision(False, "OUTCOME_TASK_OR_ATTEMPT_CHANGED", current.revision)
            transition = record_call_result(_checkpoint(attempt["checkpoint_json"]), outcome=outcome,
                                            now=now, attempt_active_seconds=attempt_active_seconds)
            checkpoint = transition.checkpoint
            state = checkpoint.status if checkpoint.status in {"DEFERRED_FREE_QUOTA", "FAILED", "NEEDS_GPT", "REVIEW_REQUIRED"} else "RESEARCHING"
            updated = replace(current, revision=current.revision + 1, state=state)
            self._update_task(db, current, updated)
            db.execute("UPDATE attempts SET status=?,checkpoint_json=?,outcome_digest=? WHERE reservation_key=?",
                       (checkpoint.status, _checkpoint_json(checkpoint), digest, key))
            return StoreDecision(True, transition.reason, updated.revision, checkpoint)

    def prepare_retry(self, key, *, now):
        with self._transaction() as db:
            attempt = db.execute("SELECT * FROM attempts WHERE reservation_key=?", (key,)).fetchone()
            if attempt is None or attempt["status"] != "RETRY_WAIT":
                return StoreDecision(False, "RETRY_NOT_WAITING")
            current = self._read_task(db, attempt["task_id"])
            if current.state != "RESEARCHING" or current.revision != attempt["reserved_revision"] + 2:
                return StoreDecision(False, "RETRY_TASK_CHANGED", current.revision)
            transition = prepare_due_retry(_checkpoint(attempt["checkpoint_json"]), now=now)
            if transition.checkpoint.status != "READY":
                return StoreDecision(False, transition.reason, current.revision)
            updated = replace(current, revision=current.revision + 1)
            self._update_task(db, current, updated)
            db.execute("UPDATE attempts SET status='RETRY_READY',checkpoint_json=? WHERE reservation_key=?",
                       (_checkpoint_json(transition.checkpoint), key))
            return StoreDecision(True, transition.reason, updated.revision, transition.checkpoint)

    def recovery_plan(self):
        """Read-only inventory; an open store never restarts work by itself."""
        with self._transaction() as db:
            return tuple((row["reservation_key"], "RECHECK_BEFORE_HANDOFF" if row["status"] == "RESERVED" else "RECONCILE_POSSIBLE_EXTERNAL_CALL")
                         for row in db.execute("SELECT reservation_key,status FROM attempts WHERE status IN ('RESERVED','DISPATCH_STARTED') ORDER BY reservation_key"))

    def quarantine_stopped_owner(self, owner_token, *, worker_stop_confirmed):
        """Synthetic recovery after a confirmed worker stop, never a timeout."""
        if worker_stop_confirmed is not True or not isinstance(owner_token, str) or not owner_token.strip():
            raise IdentityReviewRequired("confirmed stopped fixture owner required")
        with self._transaction() as db:
            attempts = list(db.execute("SELECT * FROM attempts WHERE status='DISPATCH_STARTED' AND owner_token=?", (owner_token,)))
            for attempt in attempts:
                current = self._read_task(db, attempt["task_id"])
                self._update_task(db, current, replace(current, revision=current.revision + 1, state="REVIEW_REQUIRED"))
                db.execute("UPDATE attempts SET status='REVIEW_REQUIRED' WHERE reservation_key=?", (attempt["reservation_key"],))
            return len(attempts)
