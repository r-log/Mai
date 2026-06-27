from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)

from mai.config import settings

engine = create_async_engine(settings.database_url, future=True)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")    # concurrent readers + one writer
    cur.execute("PRAGMA busy_timeout=10000")  # wait up to 10s instead of erroring on a lock
    cur.close()


SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
