import os
import json
import aiosqlite
from datetime import datetime
from shared.models import JobRecord, LogEvent


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
                project_id INTEGER NOT NULL,
                project_name TEXT NOT NULL,
                status TEXT NOT NULL,
                context TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                gas_limit_input INTEGER NOT NULL DEFAULT 80000,
                gas_limit_output INTEGER NOT NULL DEFAULT 20000,
                gas_used_input INTEGER NOT NULL DEFAULT 0,
                gas_used_output INTEGER NOT NULL DEFAULT 0,
                gas_topups TEXT NOT NULL DEFAULT '[]'
            )
        """)
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
        await self._db.commit()

    async def create_job(self, job: JobRecord) -> None:
        await self._db.execute(
            """
            INSERT INTO jobs (
                id, task, project_id, project_name, status, context,
                started_at, finished_at,
                gas_limit_input, gas_limit_output,
                gas_used_input, gas_used_output, gas_topups
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        await self._db.commit()

    async def update_job_status(
        self, job_id: str, status: str, finished_at: datetime | None = None
    ) -> None:
        await self._db.execute(
            "UPDATE jobs SET status = ?, finished_at = ? WHERE id = ?",
            (
                status,
                finished_at.isoformat() if finished_at else None,
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

    async def get_log_events(self, job_id: str) -> list[LogEvent]:
        cursor = await self._db.execute(
            "SELECT * FROM log_events WHERE job_id = ? ORDER BY sequence ASC",
            (job_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_log_event(r) for r in rows]


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
    )


def _row_to_log_event(row: aiosqlite.Row) -> LogEvent:
    return LogEvent(
        job_id=row["job_id"],
        sequence=row["sequence"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        event_type=row["event_type"],
        payload=json.loads(row["payload"]),
    )
