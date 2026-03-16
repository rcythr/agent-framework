import os
import json
import aiosqlite
from datetime import datetime, timezone
from shared.models import JobRecord, LogEvent, SessionRecord, SessionMessage, SessionContext, ActivationRecord


DB_PATH = os.getenv("DB_PATH", "gateway.db")


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._create_tables()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_tables(self) -> None:
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                project_id TEXT NOT NULL,
                project_name TEXT NOT NULL,
                status TEXT NOT NULL,
                context TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                gas_limit_input INTEGER NOT NULL DEFAULT 80000,
                gas_limit_output INTEGER NOT NULL DEFAULT 20000,
                gas_used_input INTEGER NOT NULL DEFAULT 0,
                gas_used_output INTEGER NOT NULL DEFAULT 0,
                gas_topups TEXT NOT NULL DEFAULT '[]',
                result TEXT
            )
        """)
        # Migrate existing databases that lack the result column
        cursor = await self._db.execute("PRAGMA table_info(jobs)")
        columns = {row["name"] async for row in cursor}
        if "result" not in columns:
            await self._db.execute("ALTER TABLE jobs ADD COLUMN result TEXT")
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS log_events (
                job_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (job_id, sequence)
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                project_id TEXT NOT NULL,
                project_path TEXT NOT NULL,
                branch TEXT NOT NULL,
                mr_iid INTEGER,
                status TEXT NOT NULL,
                context TEXT NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                gas_limit_input INTEGER NOT NULL DEFAULT 160000,
                gas_limit_output INTEGER NOT NULL DEFAULT 40000,
                gas_used_input INTEGER NOT NULL DEFAULT 0,
                gas_used_output INTEGER NOT NULL DEFAULT 0,
                gas_topups TEXT NOT NULL DEFAULT '[]'
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS session_messages (
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                message_type TEXT NOT NULL,
                PRIMARY KEY (session_id, sequence)
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS activations (
                project_id TEXT PRIMARY KEY,
                webhook_id TEXT NOT NULL,
                secret TEXT NOT NULL,
                activated_by TEXT NOT NULL,
                activated_at TEXT NOT NULL
            )
        """)
        await self._db.commit()

    async def create_job(self, job: JobRecord) -> None:
        await self._db.execute(
            """
            INSERT INTO jobs (
                id, task, project_id, project_name, status, context,
                started_at, finished_at,
                gas_limit_input, gas_limit_output,
                gas_used_input, gas_used_output, gas_topups, result
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.task,
                job.project_id,
                job.project_name,
                job.status,
                json.dumps(job.context),
                job.started_at.isoformat(),
                job.finished_at.isoformat() if job.finished_at else None,
                job.gas_limit_input,
                job.gas_limit_output,
                job.gas_used_input,
                job.gas_used_output,
                json.dumps(job.gas_topups),
                job.result,
            ),
        )
        await self._db.commit()

    async def update_job_status(
        self, job_id: str, status: str, finished_at: datetime | None = None,
        result: str | None = None,
    ) -> None:
        await self._db.execute(
            "UPDATE jobs SET status = ?, finished_at = ?, result = COALESCE(?, result) WHERE id = ?",
            (
                status,
                finished_at.isoformat() if finished_at else None,
                result,
                job_id,
            ),
        )
        await self._db.commit()

    async def get_job(self, job_id: str) -> JobRecord:
        cursor = await self._db.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise KeyError(f"Job not found: {job_id}")
        return _row_to_job(row)

    async def list_jobs(
        self,
        status: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[JobRecord]:
        if status:
            placeholders = ",".join("?" * len(status))
            cursor = await self._db.execute(
                f"SELECT * FROM jobs WHERE status IN ({placeholders})"
                " ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (*status, limit, offset),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM jobs ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        rows = await cursor.fetchall()
        return [_row_to_job(r) for r in rows]


    async def append_log_event(self, event: LogEvent) -> None:
        await self._db.execute(
            """
            INSERT INTO log_events (job_id, sequence, timestamp, event_type, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event.job_id,
                event.sequence,
                event.timestamp.isoformat(),
                event.event_type,
                json.dumps(event.payload),
            ),
        )
        await self._db.commit()

    async def add_gas(
        self, job_id: str, input_amount: int = 0, output_amount: int = 0
    ) -> None:
        """Increment gas limits and record the topup in gas_topups."""
        job = await self.get_job(job_id)
        topup = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input_amount": input_amount,
            "output_amount": output_amount,
        }
        new_topups = json.dumps(job.gas_topups + [topup])
        await self._db.execute(
            """UPDATE jobs
               SET gas_limit_input = gas_limit_input + ?,
                   gas_limit_output = gas_limit_output + ?,
                   gas_topups = ?
               WHERE id = ?""",
            (input_amount, output_amount, new_topups, job_id),
        )
        await self._db.commit()

    async def get_log_events(self, job_id: str) -> list[LogEvent]:
        cursor = await self._db.execute(
            "SELECT * FROM log_events WHERE job_id = ? ORDER BY sequence ASC",
            (job_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_log_event(r) for r in rows]

    # ── Session methods ───────────────────────────────────────────────────────

    async def create_session(self, session: SessionRecord) -> None:
        await self._db.execute(
            """
            INSERT INTO sessions (
                id, owner, project_id, project_path, branch, mr_iid,
                status, context, created_at, finished_at,
                gas_limit_input, gas_limit_output,
                gas_used_input, gas_used_output, gas_topups
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.owner,
                session.project_id,
                session.project_path,
                session.branch,
                session.mr_iid,
                session.status,
                json.dumps(session.context.model_dump()),
                session.created_at.isoformat(),
                session.finished_at.isoformat() if session.finished_at else None,
                session.gas_limit_input,
                session.gas_limit_output,
                session.gas_used_input,
                session.gas_used_output,
                json.dumps(session.gas_topups),
            ),
        )
        await self._db.commit()

    async def update_session_status(
        self, session_id: str, status: str, finished_at: datetime | None = None
    ) -> None:
        await self._db.execute(
            "UPDATE sessions SET status = ?, finished_at = ? WHERE id = ?",
            (
                status,
                finished_at.isoformat() if finished_at else None,
                session_id,
            ),
        )
        await self._db.commit()

    async def get_session(self, session_id: str) -> SessionRecord:
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise KeyError(f"Session not found: {session_id}")
        return _row_to_session(row)

    async def list_sessions(
        self,
        owner: str,
        status: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionRecord]:
        if status:
            placeholders = ",".join("?" * len(status))
            cursor = await self._db.execute(
                f"SELECT * FROM sessions WHERE owner = ? AND status IN ({placeholders})"
                " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (owner, *status, limit, offset),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM sessions WHERE owner = ?"
                " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (owner, limit, offset),
            )
        rows = await cursor.fetchall()
        return [_row_to_session(r) for r in rows]

    async def append_session_message(self, message: SessionMessage) -> None:
        await self._db.execute(
            """
            INSERT INTO session_messages (
                session_id, sequence, timestamp, role, content, message_type
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                message.session_id,
                message.sequence,
                message.timestamp.isoformat(),
                message.role,
                message.content,
                message.message_type,
            ),
        )
        await self._db.commit()

    async def get_session_messages(self, session_id: str) -> list[SessionMessage]:
        cursor = await self._db.execute(
            "SELECT * FROM session_messages WHERE session_id = ? ORDER BY sequence ASC",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_session_message(r) for r in rows]

    async def add_session_gas(
        self, session_id: str, input_amount: int = 0, output_amount: int = 0
    ) -> None:
        session = await self.get_session(session_id)
        topup = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input_amount": input_amount,
            "output_amount": output_amount,
        }
        new_topups = json.dumps(session.gas_topups + [topup])
        await self._db.execute(
            """UPDATE sessions
               SET gas_limit_input = gas_limit_input + ?,
                   gas_limit_output = gas_limit_output + ?,
                   gas_topups = ?
               WHERE id = ?""",
            (input_amount, output_amount, new_topups, session_id),
        )
        await self._db.commit()

    async def update_session_gas_used(
        self, session_id: str, gas_used_input: int, gas_used_output: int
    ) -> None:
        await self._db.execute(
            "UPDATE sessions SET gas_used_input = ?, gas_used_output = ? WHERE id = ?",
            (gas_used_input, gas_used_output, session_id),
        )
        await self._db.commit()


    # ── Activation methods ────────────────────────────────────────────────────

    async def activate_project(self, activation: ActivationRecord) -> None:
        await self._db.execute(
            """
            INSERT INTO activations (project_id, webhook_id, secret, activated_by, activated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                activation.project_id,
                activation.webhook_id,
                activation.secret,
                activation.activated_by,
                activation.activated_at.isoformat(),
            ),
        )
        await self._db.commit()

    async def deactivate_project(self, project_id: str) -> None:
        await self._db.execute(
            "DELETE FROM activations WHERE project_id = ?", (project_id,)
        )
        await self._db.commit()

    async def get_activation(self, project_id: str) -> ActivationRecord | None:
        cursor = await self._db.execute(
            "SELECT * FROM activations WHERE project_id = ?", (project_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_activation(row)

    async def list_activations(self) -> list[ActivationRecord]:
        cursor = await self._db.execute(
            "SELECT * FROM activations ORDER BY activated_at DESC"
        )
        rows = await cursor.fetchall()
        return [_row_to_activation(r) for r in rows]


def _row_to_job(row: aiosqlite.Row) -> JobRecord:
    return JobRecord(
        id=row["id"],
        task=row["task"],
        project_id=row["project_id"],
        project_name=row["project_name"],
        status=row["status"],
        context=json.loads(row["context"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        gas_limit_input=row["gas_limit_input"],
        gas_limit_output=row["gas_limit_output"],
        gas_used_input=row["gas_used_input"],
        gas_used_output=row["gas_used_output"],
        gas_topups=json.loads(row["gas_topups"]),
        result=row["result"],
    )


def _row_to_log_event(row: aiosqlite.Row) -> LogEvent:
    return LogEvent(
        job_id=row["job_id"],
        sequence=row["sequence"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        event_type=row["event_type"],
        payload=json.loads(row["payload"]),
    )


def _row_to_session(row: aiosqlite.Row) -> SessionRecord:
    return SessionRecord(
        id=row["id"],
        owner=row["owner"],
        project_id=row["project_id"],
        project_path=row["project_path"],
        branch=row["branch"],
        mr_iid=row["mr_iid"],
        status=row["status"],
        context=SessionContext(**json.loads(row["context"])),
        created_at=datetime.fromisoformat(row["created_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        gas_limit_input=row["gas_limit_input"],
        gas_limit_output=row["gas_limit_output"],
        gas_used_input=row["gas_used_input"],
        gas_used_output=row["gas_used_output"],
        gas_topups=json.loads(row["gas_topups"]),
    )


def _row_to_activation(row: aiosqlite.Row) -> ActivationRecord:
    return ActivationRecord(
        project_id=row["project_id"],
        webhook_id=row["webhook_id"],
        secret=row["secret"],
        activated_by=row["activated_by"],
        activated_at=datetime.fromisoformat(row["activated_at"]),
    )


def _row_to_session_message(row: aiosqlite.Row) -> SessionMessage:
    return SessionMessage(
        session_id=row["session_id"],
        sequence=row["sequence"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        role=row["role"],
        content=row["content"],
        message_type=row["message_type"],
    )
