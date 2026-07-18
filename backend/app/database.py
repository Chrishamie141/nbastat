import os, sqlite3
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / '.env')

def database_path():
    url = os.getenv('DATABASE_URL', 'sqlite:///./predictions.db')
    if url.startswith('sqlite:///'):
        raw = url.replace('sqlite:///', '', 1)
        p = Path(raw)
        return p if p.is_absolute() else BASE_DIR / p
    return BASE_DIR / 'predictions.db'

def get_db_connection():
    conn = sqlite3.connect(database_path())
    conn.row_factory = sqlite3.Row
    return conn

def initialize_auth_database():
    with get_db_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT,
                is_active INTEGER NOT NULL DEFAULT 1
            )
        ''')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)')
        conn.commit()

def _column_exists(conn, table, column):
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))

def initialize_billing_database():
    initialize_auth_database()
    columns = {
        'stripe_customer_id': 'TEXT',
        'stripe_subscription_id': 'TEXT',
        'subscription_plan': "TEXT NOT NULL DEFAULT 'none'",
        'subscription_status': "TEXT NOT NULL DEFAULT 'inactive'",
        'subscription_current_period_end': 'TEXT',
        'subscription_cancel_at_period_end': 'INTEGER NOT NULL DEFAULT 0',
        'subscription_created_at': 'TEXT',
        'subscription_updated_at': 'TEXT',
        'access_source': "TEXT NOT NULL DEFAULT 'none'",
    }
    with get_db_connection() as conn:
        for name, ddl in columns.items():
            if not _column_exists(conn, 'users', name):
                conn.execute(f'ALTER TABLE users ADD COLUMN {name} {ddl}')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_users_stripe_customer ON users(stripe_customer_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_users_stripe_subscription ON users(stripe_subscription_id)')
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stripe_webhook_events (
                stripe_event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                processed_at TEXT NOT NULL
            )
        """)
        conn.commit()
