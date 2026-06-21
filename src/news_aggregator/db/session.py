"""DB engine / session factory。"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# create_all 不會替既有資料表補欄位；列出後來才加的欄位，啟動時對既有 SQLite DB 補上。
# ponytail: 個人用 SQLite 以 idempotent ALTER 取代 alembic（此 DB 非 alembic 管理）；欄位變多時加進這張表
_ADDED_COLUMNS = {"items": {"feedback": "INTEGER"}}


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def init_db(engine: Engine) -> None:
    """建表並對既有 DB 補上後加的欄位（測試與首次執行便利用）。"""
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in _ADDED_COLUMNS.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
