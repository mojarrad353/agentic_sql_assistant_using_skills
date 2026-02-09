import logging
import psycopg2
from psycopg2 import pool
from contextlib import contextmanager

from .config import get_settings

logger = logging.getLogger(__name__)

class DatabasePool:
    _instance = None
    _pool = None

    @classmethod
    def initialize(cls):
        if cls._pool is None:
            try:
                settings = get_settings()
                logger.info("Initializing Database Connection Pool...")
                cls._pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=20,
                    host=settings.POSTGRES_HOST,
                    database=settings.POSTGRES_DB,
                    user=settings.POSTGRES_USER,
                    password=settings.POSTGRES_PASSWORD,
                    port=settings.POSTGRES_PORT
                )
                logger.info("Database Connection Pool Initialized.")
            except Exception as e:
                logger.critical(f"Failed to initialize database pool: {e}")
                raise

    @classmethod
    def get_pool(cls):
        if cls._pool is None:
            cls.initialize()
        return cls._pool

    @classmethod
    def close_all(cls):
        if cls._pool:
            cls._pool.closeall()
            logger.info("Database Connection Pool Closed.")

@contextmanager
def get_db_connection():
    """Context manager for getting a database connection from the pool."""
    pool = DatabasePool.get_pool()
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)
