from xauby.database.db import LiteDB
from xauby.storage.interface import IDatabaseRepository
from typing import Optional

def create_database(db_path: Optional[str] = None, readonly: bool = False) -> IDatabaseRepository:
    """Factory to create a database repository instance based on configuration."""
    # Currently defaults to LiteDB (SQLite). Other repository backends (e.g. PostgresDB)
    # can be plugged in here in the future.
    return LiteDB(db_path, readonly=readonly)

