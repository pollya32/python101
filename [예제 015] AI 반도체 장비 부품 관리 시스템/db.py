"""데이터베이스 계층 — SQLite (호스트 PC 로컬 파일 DB)

사용자 PC의 파일(smart_parts.db)을 DB로 사용한다.
WAL 모드 + busy_timeout으로 15명 내외 동시 사용을 지원한다.
"""

import os
import sqlite3
import sys

if getattr(sys, 'frozen', False):        # PyInstaller 실행 파일: exe가 있는 폴더에 DB 생성
    _BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    try:
        _BASE = os.path.dirname(os.path.abspath(__file__))
    except NameError:  # 대화형 실행 대비
        _BASE = os.getcwd()

DB_PATH = os.path.join(_BASE, 'smart_parts.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',          -- admin / engineer / viewer
    email TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS equipment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    etype TEXT,                                    -- Etcher / CVD / Litho ...
    location TEXT,
    status TEXT NOT NULL DEFAULT '가동중',          -- 가동중 / PM중 / 정지
    installed_at TEXT,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_code TEXT UNIQUE NOT NULL,                -- 예: CH-VP-001 (QR/RFID 식별자)
    name TEXT NOT NULL,
    category TEXT,
    equipment_id INTEGER REFERENCES equipment(id) ON DELETE SET NULL,
    install_date TEXT,                             -- 장착일
    expected_life_days INTEGER NOT NULL DEFAULT 180, -- TBM 기준 수명
    replacement_limit REAL NOT NULL DEFAULT 20,    -- Health Index 교체 임계치
    health_index REAL NOT NULL DEFAULT 100,        -- 0~100
    status TEXT NOT NULL DEFAULT '사용중',           -- 사용중 / 예비 / 교체예정 / 교체완료
    unit_cost INTEGER NOT NULL DEFAULT 0,
    supplier TEXT,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS sensor_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    ts TEXT DEFAULT (datetime('now','localtime')),
    pressure REAL,                                 -- Torr
    temperature REAL,                              -- ℃
    rf_power REAL,                                 -- W
    is_anomaly INTEGER NOT NULL DEFAULT 0,
    anomaly_param TEXT,                            -- pressure / temperature / rf_power
    fault_mode TEXT                                -- FDC 자동 분류 결과
);
CREATE INDEX IF NOT EXISTS idx_sensor_part_ts ON sensor_readings(part_id, ts);

CREATE TABLE IF NOT EXISTS health_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    date TEXT NOT NULL,                            -- YYYY-MM-DD
    health_index REAL NOT NULL,
    UNIQUE(part_id, date)
);

CREATE TABLE IF NOT EXISTS replacements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_code TEXT NOT NULL,
    part_name TEXT,
    category TEXT,
    equipment_id INTEGER REFERENCES equipment(id) ON DELETE SET NULL,
    replaced_at TEXT,                              -- 교체(예정)일자
    used_days INTEGER,                             -- 사용 일수
    predicted_life INTEGER,                        -- AI 예측 수명(정확도 산출용)
    failure_cause TEXT,                            -- Wear & Tear / Early Failure / Preventive ...
    status TEXT NOT NULL DEFAULT 'Completed',      -- Completed / In Use / Scheduled
    repair_hours REAL,                             -- MTTR 산출용
    cost INTEGER DEFAULT 0,
    performed_by TEXT,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    atype TEXT NOT NULL,                           -- FDC / LIFE / STOCK / RECOMMEND
    severity TEXT NOT NULL DEFAULT 'warning',      -- info / warning / critical
    part_id INTEGER REFERENCES parts(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category TEXT,
    stock_qty INTEGER NOT NULL DEFAULT 0,
    safety_stock INTEGER NOT NULL DEFAULT 2,
    unit_cost INTEGER NOT NULL DEFAULT 0,
    lead_time_days INTEGER NOT NULL DEFAULT 14,
    supplier TEXT,
    note TEXT,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
"""


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def is_empty():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return n == 0
