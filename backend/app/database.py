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
