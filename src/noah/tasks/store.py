"""SQLite persistence for tasks, at `settings.data_dir / "noah.db"`.

The schema version is kept in `PRAGMA user_version`; `_MIGRATIONS[n]` upgrades version n to n+1.
"""

from datetime import datetime
from pathlib import Path

import aiosqlite

from noah.tasks.models import Item, ItemStatus, Task

_MIGRATIONS = [
    """
    CREATE TABLE tasks (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE items (
        task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        number INTEGER NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL,
        note TEXT,
        PRIMARY KEY (task_id, number)
    );
    CREATE INDEX tasks_updated ON tasks(updated_at);
    """,
]


class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = await aiosqlite.connect(self.path)
            await db.execute("PRAGMA foreign_keys = ON")
            await _migrate(db)
            self._db = db
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def create(self, title: str, texts: list[str], now: datetime) -> Task:
        db = await self._conn()
        stamp = now.isoformat()
        cursor = await db.execute(
            "INSERT INTO tasks (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, stamp, stamp),
        )
        task_id = cursor.lastrowid
        assert task_id is not None
        await db.executemany(
            "INSERT INTO items (task_id, number, text, status) VALUES (?, ?, ?, 'pending')",
            [(task_id, n, text) for n, text in enumerate(texts, 1)],
        )
        await db.commit()
        return await self._require(task_id)

    async def add_items(self, task_id: int, texts: list[str], now: datetime) -> Task:
        db = await self._conn()
        async with db.execute(
            "SELECT COALESCE(MAX(number), 0) FROM items WHERE task_id = ?", (task_id,)
        ) as cursor:
            row = await cursor.fetchone()
        last = row[0] if row else 0
        await db.executemany(
            "INSERT INTO items (task_id, number, text, status) VALUES (?, ?, ?, 'pending')",
            [(task_id, last + n, text) for n, text in enumerate(texts, 1)],
        )
        await self._touch(db, task_id, now)
        return await self._require(task_id)

    async def set_item(
        self, task_id: int, number: int, status: ItemStatus, note: str | None, now: datetime
    ) -> Task:
        db = await self._conn()
        await db.execute(
            "UPDATE items SET status = ?, note = ? WHERE task_id = ? AND number = ?",
            (status, note, task_id, number),
        )
        await self._touch(db, task_id, now)
        return await self._require(task_id)

    async def get(self, task_id: int) -> Task | None:
        tasks = await self._load("WHERE id = ?", (task_id,))
        return tasks[0] if tasks else None

    async def recent(self, limit: int = 20) -> list[Task]:
        """Most recently updated first."""
        return await self._load("ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,))

    async def _touch(self, db: aiosqlite.Connection, task_id: int, now: datetime) -> None:
        await db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now.isoformat(), task_id))
        await db.commit()

    async def _require(self, task_id: int) -> Task:
        task = await self.get(task_id)
        assert task is not None, f"task {task_id} vanished"
        return task

    async def _load(self, clause: str, params: tuple[object, ...]) -> list[Task]:
        db = await self._conn()
        async with db.execute(
            f"SELECT id, title, created_at, updated_at FROM tasks {clause}", params
        ) as cursor:
            rows = list(await cursor.fetchall())
        if not rows:
            return []
        ids = [row[0] for row in rows]
        marks = ",".join("?" * len(ids))
        items: dict[int, list[Item]] = {task_id: [] for task_id in ids}
        async with db.execute(
            f"SELECT task_id, number, text, status, note FROM items "
            f"WHERE task_id IN ({marks}) ORDER BY task_id, number",
            ids,
        ) as cursor:
            async for task_id, number, text, status, note in cursor:
                items[task_id].append(Item(task_id, number, text, status, note))
        return [
            Task(
                id=task_id,
                title=title,
                created_at=datetime.fromisoformat(created),
                updated_at=datetime.fromisoformat(updated),
                items=tuple(items[task_id]),
            )
            for task_id, title, created, updated in rows
        ]


async def _migrate(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA user_version") as cursor:
        row = await cursor.fetchone()
    version = row[0] if row else 0
    for target, script in enumerate(_MIGRATIONS[version:], version + 1):
        await db.executescript(script)
        await db.execute(f"PRAGMA user_version = {target}")
        await db.commit()
