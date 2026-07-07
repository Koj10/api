import json
import sqlite3
from dotenv import load_dotenv
import os

load_dotenv()

# По умолчанию SQLite рядом с api/; в проде задайте DB_PATH в .env
_DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH") or os.path.join(_DB_DIR, "gamesense.db")


def SQL_request(query, params=(), fetch="one", jsonify_result=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)

            if fetch == "all":
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                result = [
                    {
                        col: json.loads(row[i])
                        if isinstance(row[i], str) and row[i].startswith("{")
                        else row[i]
                        for i, col in enumerate(columns)
                    }
                    for row in rows
                ]

            elif fetch == "one":
                row = cursor.fetchone()
                if row:
                    columns = [desc[0] for desc in cursor.description]
                    result = {
                        col: json.loads(row[i])
                        if isinstance(row[i], str) and row[i].startswith("{")
                        else row[i]
                        for i, col in enumerate(columns)
                    }
                else:
                    result = None
            else:
                conn.commit()
                result = None

        except sqlite3.Error as e:
            print(f"Ошибка SQL: {e}")
            raise

    if jsonify_result and result is not None:
        return json.dumps(result, ensure_ascii=False, indent=2)
    return result


def create_users():
    SQL_request("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        first_name VARCHAR(50) NOT NULL,
        middle_name VARCHAR(50),
        last_name VARCHAR(50) NOT NULL,
        email VARCHAR(255) NOT NULL UNIQUE,
        email_confirmed BOOLEAN DEFAULT FALSE,
        phone_number VARCHAR(20),
        password_hash VARCHAR(255) NOT NULL,
        date_of_birth DATE,
        gender VARCHAR(10) DEFAULT 'male',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_login DATETIME,
        balance INTEGER DEFAULT 0,
        passport INTEGER DEFAULT 0,
        cart JSON,
        inventory JSON,
        tg TEXT,
        vk TEXT,
        role TEXT DEFAULT 'user',
        is_active BOOLEAN DEFAULT TRUE
    )""")


def create_verification_codes():
    SQL_request("""CREATE TABLE IF NOT EXISTS verification_codes (
        id INTEGER PRIMARY KEY,
        email VARCHAR(255) NOT NULL,
        code VARCHAR(10),
        token TEXT,  -- для восстановления пароля
        type VARCHAR(20) NOT NULL,  -- 'register', 'reset_password'
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        is_used BOOLEAN DEFAULT FALSE
    )""")
    SQL_request(
        "CREATE INDEX IF NOT EXISTS idx_email_type ON verification_codes (email, type)"
    )


def create_time_packages():
    SQL_request("""CREATE TABLE IF NOT EXISTS time_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(50) NOT NULL,
    description TEXT,
    duration_minutes INTEGER NOT NULL CHECK(duration_minutes > 0),
    price DECIMAL(10,2) NOT NULL CHECK(price >= 0),
    price_vip DECIMAL(10,2) NOT NULL DEFAULT 0 CHECK(price_vip >= 0),
    time_period VARCHAR(10) CHECK(time_period IN ('дневной', 'вечерний', 'ночной', "бесконечный")),
    is_weekend BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    image BLOB
);""")
    columns = {
        row["name"]
        for row in (SQL_request("PRAGMA table_info(time_packages)", fetch="all") or [])
    }
    if "price_vip" not in columns:
        try:
            SQL_request(
                "ALTER TABLE time_packages ADD COLUMN price_vip DECIMAL(10,2) NOT NULL DEFAULT 0",
                fetch="none",
            )
            SQL_request(
                "UPDATE time_packages SET price_vip = price WHERE price_vip = 0",
                fetch="none",
            )
        except Exception:
            pass


def create_purchases():
    SQL_request("""CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    product TEXT,
    product_id INTEGER,
    quality INTEGER,
    price DECIMAL(10,2) NOT NULL CHECK(price >= 0),
    time_buy DATETIME DEFAULT CURRENT_TIMESTAMP,
    status INTEGER DEFAULT 1
);""")


def create_computers():
    SQL_request("""CREATE TABLE IF NOT EXISTS computers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number_pc INTEGER,
    token TEXT,
    time_active CURRENT_TIMESTAMP,
    session_started_at TEXT,
    session_duration_minutes INTEGER,
    user_active INTEGER REFERENCES users(id),
    zone TEXT NOT NULL DEFAULT 'regular',
    time_zone INTEGER DEFAULT 0,
    status VARCHAR(20) CHECK(status IN ('активен', 'занят', 'заблокирован', "ремонт", "админ", "выключен"))
);""")
    columns = {
        row["name"]
        for row in (SQL_request("PRAGMA table_info(computers)", fetch="all") or [])
    }
    if "session_started_at" not in columns:
        try:
            SQL_request("ALTER TABLE computers ADD COLUMN session_started_at TEXT", fetch="none")
        except Exception:
            pass
    if "session_duration_minutes" not in columns:
        try:
            SQL_request(
                "ALTER TABLE computers ADD COLUMN session_duration_minutes INTEGER",
                fetch="none",
            )
        except Exception:
            pass
    if "zone" not in columns:
        try:
            SQL_request(
                "ALTER TABLE computers ADD COLUMN zone TEXT NOT NULL DEFAULT 'regular'",
                fetch="none",
            )
        except Exception:
            pass


def create_payments():
    SQL_request("""CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    payment_id TEXT,
    value FLOAT,
    created_at TEXT,
    captured_at TEXT,
    status TEXT
);""")
    columns = {
        row["name"]
        for row in (SQL_request("PRAGMA table_info(payments)", fetch="all") or [])
    }
    if "captured_at" not in columns:
        try:
            SQL_request("ALTER TABLE payments ADD COLUMN captured_at TEXT", fetch="none")
        except Exception:
            pass


def create_revenue_transactions():
    SQL_request(
        """CREATE TABLE IF NOT EXISTS revenue_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES users(id),
        admin_id INTEGER REFERENCES users(id),
        amount INTEGER NOT NULL,
        payment_method TEXT CHECK(payment_method IN ('cash', 'card', 'online', 'none')),
        kind TEXT CHECK(kind IN ('topup', 'withdraw')) NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )"""
    )


def create_pc_coupons():
    SQL_request(
        """CREATE TABLE IF NOT EXISTS pc_coupons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        computer_id INTEGER NOT NULL,
        time_package_id INTEGER NOT NULL,
        admin_id INTEGER,
        amount INTEGER NOT NULL DEFAULT 0,
        status TEXT DEFAULT 'active',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        used_at DATETIME,
        used_by_user_id INTEGER
    )"""
    )


def create_friendships():
    SQL_request(
        """CREATE TABLE IF NOT EXISTS friendships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        requester_id INTEGER NOT NULL REFERENCES users(id),
        addressee_id INTEGER NOT NULL REFERENCES users(id),
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending', 'accepted')),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(requester_id, addressee_id)
    )"""
    )
    SQL_request(
        "CREATE INDEX IF NOT EXISTS idx_friendships_addressee ON friendships(addressee_id, status)",
        fetch="none",
    )
    SQL_request(
        "CREATE INDEX IF NOT EXISTS idx_friendships_requester ON friendships(requester_id, status)",
        fetch="none",
    )


def ensure_topup_bonus_column():
    columns = {
        row["name"]
        for row in (SQL_request("PRAGMA table_info(users)", fetch="all") or [])
    }
    if "topup_bonus_progress" not in columns:
        SQL_request(
            "ALTER TABLE users ADD COLUMN topup_bonus_progress INTEGER NOT NULL DEFAULT 0",
            fetch="none",
        )


def ensure_roulette_column():
    columns = {
        row["name"]
        for row in (SQL_request("PRAGMA table_info(users)", fetch="all") or [])
    }
    if "roulette" not in columns:
        SQL_request(
            "ALTER TABLE users ADD COLUMN roulette INTEGER NOT NULL DEFAULT 0",
            fetch="none",
        )


create_users()
create_verification_codes()
create_time_packages()
create_purchases()
create_computers()
create_payments()
create_revenue_transactions()
create_pc_coupons()
create_friendships()
ensure_topup_bonus_column()
ensure_roulette_column()
