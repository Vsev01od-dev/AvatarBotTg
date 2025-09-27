import sqlite3
from contextlib import contextmanager
from typing import Optional, List, Tuple

DB_PATH = "bot.db"

@contextmanager
def get_conn():
    con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    try:
        yield con
    finally:
        con.close()

def init_db():
    with get_conn() as con:
        cur = con.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")

        # users: avatar_key, chat_id (текущий активный чат)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                avatar_key TEXT,
                chat_id INTEGER DEFAULT 1
            );
        """)

        # messages: привязываем к chat_id, храним обе роли
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                role TEXT,         -- 'user' | 'assistant'
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # daily_usage: суточный лимит (сохраняется даже если /reset очистит чат)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id INTEGER,
                date TEXT,     -- 'YYYY-MM-DD' (локальная)
                used INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, date)
            );
        """)

        cur.execute("""
                    CREATE TABLE IF NOT EXISTS daily_usage_images (
                        user_id INTEGER,
                        date TEXT,
                        used INTEGER DEFAULT 0,
                        PRIMARY KEY (user_id, date)
                    );
                """)

        # миграции-страховки для уже существующих БД
        try: cur.execute("ALTER TABLE users ADD COLUMN chat_id INTEGER DEFAULT 1;")
        except Exception: pass
        try: cur.execute("ALTER TABLE messages ADD COLUMN chat_id INTEGER;")
        except Exception: pass

        con.commit()

# ---------- users ----------
def ensure_user(user_id: int, username: str) -> None:
    with get_conn() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO users (user_id, username)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username;
        """, (user_id, username or ""))
        con.commit()

def get_avatar(user_id: int) -> Optional[str]:
    with get_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT avatar_key FROM users WHERE user_id=?;", (user_id,))
        r = cur.fetchone()
        return r[0] if r else None

def set_avatar(user_id: int, username: str, avatar_key: str):
    with get_conn() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO users (user_id, username, avatar_key, chat_id)
            VALUES (?, ?, ?, COALESCE((SELECT chat_id FROM users WHERE user_id=?), 1))
            ON CONFLICT(user_id) DO UPDATE SET
              username=excluded.username,
              avatar_key=excluded.avatar_key;
        """, (user_id, username or "", avatar_key, user_id))
        con.commit()

def get_chat_id(user_id: int) -> int:
    with get_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT chat_id FROM users WHERE user_id=?;", (user_id,))
        r = cur.fetchone()
        return r[0] if r and r[0] else 1

def new_chat(user_id: int) -> int:
    """Начать новый чат (chat_id++)."""
    with get_conn() as con:
        cur = con.cursor()
        cur.execute("""
            UPDATE users
            SET chat_id = COALESCE(chat_id, 1) + 1
            WHERE user_id=?;
        """, (user_id,))
        con.commit()
    return get_chat_id(user_id)

# ---------- messages ----------
def save_message(user_id: int, chat_id: int, role: str, content: str):
    with get_conn() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO messages (user_id, chat_id, role, content)
            VALUES (?, ?, ?, ?);
        """, (user_id, chat_id, role, content))
        con.commit()

def load_recent_chat(user_id: int, chat_id: int, limit: int = 100) -> List[Tuple[str, str]]:
    """Возвращает [(role, content)] для активного чата, не более limit."""
    with get_conn() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT role, content FROM messages
            WHERE user_id=? AND chat_id=?
            ORDER BY id DESC
            LIMIT ?;
        """, (user_id, chat_id, limit))
        rows = cur.fetchall()
        return list(reversed(rows))

def count_chat_messages(user_id: int, chat_id: int) -> int:
    with get_conn() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM messages
            WHERE user_id=? AND chat_id=?;
        """, (user_id, chat_id))
        return cur.fetchone()[0] or 0

# ---------- daily usage ----------
def _today_local(con) -> str:
    # SQLite локально: DATE('now','localtime')
    cur = con.cursor()
    cur.execute("SELECT DATE('now','localtime');")
    return cur.fetchone()[0]

def inc_daily_usage(user_id: int) -> int:
    """Инкрементирует счётчик за сегодня, возвращает used."""
    with get_conn() as con:
        cur = con.cursor()
        today = _today_local(con)
        cur.execute("""
            INSERT INTO daily_usage (user_id, date, used)
            VALUES (?, ?, 0)
            ON CONFLICT(user_id, date) DO NOTHING;
        """, (user_id, today))
        cur.execute("""
            UPDATE daily_usage SET used = used + 1
            WHERE user_id=? AND date=?;
        """, (user_id, today))
        cur.execute("""
            SELECT used FROM daily_usage WHERE user_id=? AND date=?;
        """, (user_id, today))
        return cur.fetchone()[0] or 0

def get_daily_used(user_id: int) -> int:
    with get_conn() as con:
        cur = con.cursor()
        today = _today_local(con)
        cur.execute("SELECT used FROM daily_usage WHERE user_id=? AND date=?;", (user_id, today))
        r = cur.fetchone()
        return r[0] if r else 0

def reset_dialog(user_id: int):
    """Начать новый чат (не трогаем суточный лимит)."""
    new_chat(user_id)  # просто переключаемся на новый chat_id

def get_images_used(user_id: int) -> int:
    with get_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT DATE('now','localtime');")
        today = cur.fetchone()[0]
        cur.execute("SELECT used FROM daily_usage_images WHERE user_id=? AND date=?;", (user_id, today))
        r = cur.fetchone()
        return r[0] if r else 0

def inc_images_usage(user_id: int) -> int:
    with get_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT DATE('now','localtime');")
        today = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO daily_usage_images (user_id, date, used)
            VALUES (?, ?, 0)
            ON CONFLICT(user_id, date) DO NOTHING;
        """, (user_id, today))
        cur.execute("""
            UPDATE daily_usage_images SET used = used + 1
            WHERE user_id=? AND date=?;
        """, (user_id, today))
        cur.execute("SELECT used FROM daily_usage_images WHERE user_id=? AND date=?;", (user_id, today))
        return cur.fetchone()[0] or 0
