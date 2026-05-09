"""
Database Connection Pool
========================
Centralized PostgreSQL connection pool for all database operations.
Reduces connection overhead from ~100ms to ~5ms per query.
"""

import os
import logging
from contextlib import contextmanager
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# Database connection parameters
# Database connection parameters
DB_HOST = os.environ.get("DB_HOST", os.environ.get("PG_HOST", "localhost"))
DB_PORT = os.environ.get("DB_PORT", os.environ.get("PG_PORT", "5432"))
DB_USER = os.environ.get("DB_USER", os.environ.get("PG_USER", "postgres"))
DB_PASSWORD = os.environ.get("DB_PWD", os.environ.get("PG_PASSWORD", ""))
DB_NAME = os.environ.get("DB_NAME", os.environ.get("PG_DB", "mibase"))

# Connection pool (lazy initialized)
_connection_pool = None
_last_failure_time = 0
_failure_count = 0
_BASE_RETRY_DELAY = 30  # seconds before first retry
_MAX_RETRY_DELAY = 300  # max 5 minutes between retries


def get_pool():
    """Get or create the connection pool (singleton pattern with backoff)."""
    global _connection_pool, _last_failure_time, _failure_count
    
    if _connection_pool is not None:
        return _connection_pool
    
    # Backoff: skip if we recently failed
    import time
    now = time.time()
    if _failure_count > 0:
        delay = min(_BASE_RETRY_DELAY * (2 ** (_failure_count - 1)), _MAX_RETRY_DELAY)
        elapsed = now - _last_failure_time
        if elapsed < delay:
            raise ConnectionError(
                f"DB connection in backoff (retry in {int(delay - elapsed)}s)"
            )
    
    try:
        _connection_pool = pool.ThreadedConnectionPool(
            minconn=2,   # Minimum connections to keep open
            maxconn=10,  # Maximum connections allowed
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )
        _failure_count = 0  # Reset on success
        logger.info(f"✅ Connection pool created: {DB_HOST}:{DB_PORT}/{DB_NAME} (2-10 connections)")
    except Exception as e:
        _failure_count += 1
        _last_failure_time = now
        delay = min(_BASE_RETRY_DELAY * (2 ** (_failure_count - 1)), _MAX_RETRY_DELAY)
        logger.error(f"❌ Failed to create connection pool (attempt {_failure_count}, next retry in {delay}s): {type(e).__name__}")
        raise
    
    return _connection_pool


@contextmanager
def get_connection():
    """
    Get a connection from the pool.
    Usage:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ...")
    """
    pool_instance = get_pool()
    conn = None
    try:
        conn = pool_instance.getconn()
        yield conn
    finally:
        if conn:
            pool_instance.putconn(conn)


@contextmanager  
def get_cursor(dict_cursor=False):
    """
    Get a cursor directly (convenience function).
    Usage:
        with get_cursor() as cur:
            cur.execute("SELECT ...")
            results = cur.fetchall()
    """
    with get_connection() as conn:
        cursor_factory = RealDictCursor if dict_cursor else None
        with conn.cursor(cursor_factory=cursor_factory) as cur:
            yield cur
        conn.commit()


def close_pool():
    """Close all connections in the pool (call on shutdown)."""
    global _connection_pool
    if _connection_pool:
        _connection_pool.closeall()
        _connection_pool = None
        logger.info("🔌 Connection pool closed")


# For backward compatibility - direct connection (will use pool internally)
def get_single_connection():
    """Get a single connection (for code that manages connections manually)."""
    return get_pool().getconn()


def return_connection(conn):
    """Return a connection to the pool."""
    get_pool().putconn(conn)
