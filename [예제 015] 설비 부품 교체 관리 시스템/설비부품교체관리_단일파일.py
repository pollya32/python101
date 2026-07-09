"""
설비 부품 교체 관리 시스템 — 단일 파일 버전
=====================================================
설치: pip install flask python-pptx requests
실행: python 설비부품교체관리_단일파일.py
접속: http://localhost:5000

이 파일 하나만 다운로드하면 폴더 구조 없이 바로 실행할 수 있습니다.
(templates/static 폴더가 필요한 app.py 버전과 동일한 기능을 담은 단일 파일본)
"""

import sqlite3
import os
import csv
import io
import random
import re
import secrets
import socket
import shutil
import threading
import time
import traceback
import requests
import html as html_lib
from html.parser import HTMLParser
from urllib.parse import quote
from datetime import date, datetime, timedelta
from flask import Flask, request, jsonify, send_file, Response, session, redirect, url_for
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

try:
    _base = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _base = os.getcwd()
DB_PATH = os.path.join(_base, "equipment_data.db")
BACKUP_DIR = os.path.join(_base, "backups")


def _html_escape(s):
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )

EQUIPMENT_COUNT = 20
EQUIPMENT_PREFIX = "TEAG"
MASTER_EQUIPMENT_ID = 1  # TEAG01호기: 이 설비에 추가한 부품은 동일한 이름의 유닛을 가진 나머지 설비에도 자동 복제된다
DEFAULT_PASSWORD = "0000"
# ══ 사내 메일 API 설정 ═══════════════════════════════════════════════
# (보안) 실제 값으로 교체한 뒤에는 이 파일을 외부에 공유/업로드하지 마세요.
os.environ["no_proxy"] = "openapi.samsung.net"
MAIL_SENDER_ID = "lbr-32.lee"                    # 발신자 녹스 ID (@samsung.com 앞부분)
MAIL_AUTHORIZATION = "XXXXXXXXXXXXXXX"           # ← 실제 Authorization 값으로 교체
MAIL_SYSTEM_ID = "XXXXXXXXXXXXXX"                # ← 실제 System-ID 값으로 교체
MAIL_SUBJECT = "[부품관리] TES 설비 부품 현황"
# ════════════════════════════════════════════════════════════════════
MAX_DRAWING_DATA_LEN = 8 * 1024 * 1024  # 도면 이미지(base64 data URL) 최대 길이, 원본 파일 약 5MB에 해당


def dashboard_grid_pos(index, cols=5):
    """대시보드 캔버스 안에서 index번째 설비의 기본 격자 위치(% 좌표)를 계산한다."""
    row = index // cols
    col = index % cols
    x = 10 + col * (80 / max(cols - 1, 1))
    y = min(15 + row * 22, 92)
    return round(x, 2), round(y, 2)

# 사진 속 설비(로드포트 4개 + HMI 제어패널 + 공정모듈 + 배기/시그널타워) 구조를 본뜬 기본 유닛
# pos_x, pos_y는 설비 캔버스 안에서 유닛 중심의 위치(% 좌표)
DEFAULT_UNITS = [
    ("배기 유닛 / 시그널 타워", "🚨", "#6b7280", 50, 18),
    ("로드포트 1", "📦", "#2563eb", 10, 60),
    ("로드포트 2", "📦", "#2563eb", 26, 60),
    ("로드포트 3", "📦", "#2563eb", 42, 60),
    ("로드포트 4", "📦", "#2563eb", 58, 60),
    ("HMI 제어 패널", "🖥️", "#0f766e", 76, 60),
    ("공정 모듈 (파워유닛)", "🔧", "#7c3aed", 92, 60),
]


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


_RICH_DANGEROUS_BLOCK_RE = re.compile(
    r"<(script|style|iframe|object|embed|link|meta|form)\b[^>]*>.*?</\1\s*>"
    r"|<(script|style|iframe|object|embed|link|meta|form)\b[^>]*/?>",
    re.IGNORECASE | re.DOTALL,
)
_RICH_EVENT_ATTR_RE = re.compile(r"""\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)
_RICH_JS_URI_RE = re.compile(r"""(href|src)\s*=\s*("|')\s*(javascript|data):[^"']*\2""", re.IGNORECASE)


def sanitize_rich_html(raw):
    """메모/노트에 저장되는 HTML에 대한 서버측 2차 방어선(주 방어는 클라이언트의 화이트리스트
    기반 sanitizeRichHtml). script/style/iframe 등 위험 태그와 on*= 이벤트 속성, javascript:/data:
    URI를 제거한다."""
    if not raw:
        return raw
    cleaned = _RICH_DANGEROUS_BLOCK_RE.sub("", raw)
    cleaned = _RICH_EVENT_ATTR_RE.sub("", cleaned)
    cleaned = _RICH_JS_URI_RE.sub(lambda m: f'{m.group(1)}="#"', cleaned)
    return cleaned


def legacy_text_to_rich_html(text):
    """리치 메모 기능 도입 이전에 평문으로 저장되어 있던 메모/노트를, 기존과 동일하게 보이는
    안전한 HTML로 1회 변환한다 (이스케이프 + 줄바꿈을 <br>로 + URL 자동 링크화)."""
    if not text:
        return text
    escaped = html_lib.escape(text, quote=False)
    escaped = re.sub(
        r"(https?://[^\s<]+)",
        r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>',
        escaped,
    )
    return escaped.replace("\n", "<br>")


def get_lan_ip():
    """같은 네트워크의 다른 사람이 접속할 수 있는 이 컴퓨터의 IP 주소를 알아낸다."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def get_config(conn, key):
    row = conn.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_config(conn, key, value):
    conn.execute(
        "INSERT INTO app_config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def log_activity(conn, action, target_type, target_id, target_name, detail=""):
    """설비/유닛/부품 등에 대한 주요 변경 작업을 활동 이력에 기록한다.
    현재 세션에 저장된 사용자 이름을 행위자로 남긴다."""
    actor = session.get("user_name") or "익명"
    conn.execute(
        "INSERT INTO activity_log (actor_name, action, target_type, target_id, target_name, detail) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (actor, action, target_type, target_id, target_name, detail),
    )


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT)")
    if not c.execute("SELECT 1 FROM app_config WHERE key = 'password_hash'").fetchone():
        c.execute(
            "INSERT INTO app_config (key, value) VALUES ('password_hash', ?)",
            (generate_password_hash(DEFAULT_PASSWORD),),
        )
    if not c.execute("SELECT 1 FROM app_config WHERE key = 'secret_key'").fetchone():
        c.execute(
            "INSERT INTO app_config (key, value) VALUES ('secret_key', ?)",
            (secrets.token_hex(32),),
        )
    conn.commit()

    c.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_name TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            target_name TEXT,
            detail TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()

    c.execute("""
        CREATE TABLE IF NOT EXISTS equipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            icon TEXT DEFAULT '🏭',
            pos_x REAL DEFAULT 50,
            pos_y REAL DEFAULT 50,
            location TEXT,
            setup_date TEXT,
            deleted_at TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    existing_eq_cols = {r["name"] for r in c.execute("PRAGMA table_info(equipments)").fetchall()}
    if "icon" not in existing_eq_cols:
        c.execute("ALTER TABLE equipments ADD COLUMN icon TEXT DEFAULT '🏭'")
        c.execute("UPDATE equipments SET icon = '🏭' WHERE icon IS NULL")
    if "pos_x" not in existing_eq_cols:
        c.execute("ALTER TABLE equipments ADD COLUMN pos_x REAL")
        c.execute("ALTER TABLE equipments ADD COLUMN pos_y REAL")
    if "location" not in existing_eq_cols:
        c.execute("ALTER TABLE equipments ADD COLUMN location TEXT")
    if "setup_date" not in existing_eq_cols:
        c.execute("ALTER TABLE equipments ADD COLUMN setup_date TEXT")
    if "deleted_at" not in existing_eq_cols:
        c.execute("ALTER TABLE equipments ADD COLUMN deleted_at TEXT")
    eq_count = c.execute("SELECT COUNT(*) AS n FROM equipments").fetchone()["n"]
    if eq_count == 0:
        for i in range(1, EQUIPMENT_COUNT + 1):
            x, y = dashboard_grid_pos(i - 1)
            c.execute(
                "INSERT INTO equipments (id, name, pos_x, pos_y) VALUES (?, ?, ?, ?)",
                (i, f"{EQUIPMENT_PREFIX}{i:02d}호기", x, y),
            )
    unplaced_eq = c.execute(
        "SELECT id FROM equipments WHERE pos_x IS NULL OR pos_y IS NULL ORDER BY id"
    ).fetchall()
    for i, row in enumerate(unplaced_eq):
        x, y = dashboard_grid_pos(i)
        c.execute("UPDATE equipments SET pos_x = ?, pos_y = ? WHERE id = ?", (x, y, row["id"]))

    c.execute("""
        CREATE TABLE IF NOT EXISTS units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equipment_id INTEGER NOT NULL DEFAULT 1,
            name TEXT NOT NULL,
            icon TEXT DEFAULT '⚙️',
            color TEXT DEFAULT '#1a3a5c',
            pos_x REAL DEFAULT 50,
            pos_y REAL DEFAULT 50,
            width REAL DEFAULT 140,
            height REAL DEFAULT 110,
            deleted_at TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (equipment_id) REFERENCES equipments(id) ON DELETE CASCADE
        )
    """)
    existing_cols = {r["name"] for r in c.execute("PRAGMA table_info(units)").fetchall()}
    if "equipment_id" not in existing_cols:
        c.execute("ALTER TABLE units ADD COLUMN equipment_id INTEGER")
        c.execute("UPDATE units SET equipment_id = 1 WHERE equipment_id IS NULL")
    if "pos_x" not in existing_cols:
        c.execute("ALTER TABLE units ADD COLUMN pos_x REAL")
        c.execute("ALTER TABLE units ADD COLUMN pos_y REAL")
    if "width" not in existing_cols:
        c.execute("ALTER TABLE units ADD COLUMN width REAL")
        c.execute("ALTER TABLE units ADD COLUMN height REAL")
    if "deleted_at" not in existing_cols:
        c.execute("ALTER TABLE units ADD COLUMN deleted_at TEXT")
    unplaced = c.execute(
        "SELECT id FROM units WHERE pos_x IS NULL OR pos_y IS NULL ORDER BY id"
    ).fetchall()
    for i, row in enumerate(unplaced):
        x = 10 + (i * 84 / max(len(unplaced) - 1, 1)) if len(unplaced) > 1 else 50
        c.execute("UPDATE units SET pos_x = ?, pos_y = ? WHERE id = ?", (x, 50, row["id"]))
    c.execute("UPDATE units SET width = 140 WHERE width IS NULL")
    c.execute("UPDATE units SET height = 110 WHERE height IS NULL")

    c.execute("""
        CREATE TABLE IF NOT EXISTS parts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unit_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            spec TEXT,
            cycle_days INTEGER NOT NULL DEFAULT 90,
            cycle_unit TEXT DEFAULT '일',
            cost REAL DEFAULT 0,
            last_replaced_date TEXT,
            note TEXT,
            memo TEXT,
            drawing_data TEXT,
            icon TEXT DEFAULT '🔩',
            pos_x REAL DEFAULT 50,
            pos_y REAL DEFAULT 50,
            width REAL DEFAULT 130,
            height REAL DEFAULT 110,
            stock_qty INTEGER DEFAULT 0,
            supplier TEXT,
            supplier_contact TEXT,
            lead_time_days INTEGER,
            deleted_at TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE
        )
    """)
    existing_part_cols = {r["name"] for r in c.execute("PRAGMA table_info(parts)").fetchall()}
    if "icon" not in existing_part_cols:
        c.execute("ALTER TABLE parts ADD COLUMN icon TEXT DEFAULT '🔩'")
    if "pos_x" not in existing_part_cols:
        c.execute("ALTER TABLE parts ADD COLUMN pos_x REAL")
        c.execute("ALTER TABLE parts ADD COLUMN pos_y REAL")
        c.execute("ALTER TABLE parts ADD COLUMN width REAL")
        c.execute("ALTER TABLE parts ADD COLUMN height REAL")
    if "cost" not in existing_part_cols:
        c.execute("ALTER TABLE parts ADD COLUMN cost REAL DEFAULT 0")
        c.execute("UPDATE parts SET cost = 0 WHERE cost IS NULL")
    if "cycle_unit" not in existing_part_cols:
        c.execute("ALTER TABLE parts ADD COLUMN cycle_unit TEXT DEFAULT '일'")
        c.execute("UPDATE parts SET cycle_unit = '일' WHERE cycle_unit IS NULL")
    if "memo" not in existing_part_cols:
        c.execute("ALTER TABLE parts ADD COLUMN memo TEXT")
    if "drawing_data" not in existing_part_cols:
        c.execute("ALTER TABLE parts ADD COLUMN drawing_data TEXT")
    if "stock_qty" not in existing_part_cols:
        c.execute("ALTER TABLE parts ADD COLUMN stock_qty INTEGER DEFAULT 0")
        c.execute("ALTER TABLE parts ADD COLUMN supplier TEXT")
        c.execute("ALTER TABLE parts ADD COLUMN supplier_contact TEXT")
        c.execute("ALTER TABLE parts ADD COLUMN lead_time_days INTEGER")
        c.execute("UPDATE parts SET stock_qty = 0 WHERE stock_qty IS NULL")
    if "deleted_at" not in existing_part_cols:
        c.execute("ALTER TABLE parts ADD COLUMN deleted_at TEXT")
    for unit_row in c.execute("SELECT DISTINCT unit_id FROM parts").fetchall():
        unplaced_parts = c.execute(
            "SELECT id FROM parts WHERE unit_id = ? AND (pos_x IS NULL OR pos_y IS NULL) ORDER BY id",
            (unit_row["unit_id"],),
        ).fetchall()
        for i, prow in enumerate(unplaced_parts):
            x = 15 + (i * 70 / max(len(unplaced_parts) - 1, 1)) if len(unplaced_parts) > 1 else 50
            c.execute("UPDATE parts SET pos_x = ?, pos_y = ? WHERE id = ?", (x, 50, prow["id"]))
    c.execute("UPDATE parts SET width = 130 WHERE width IS NULL")
    c.execute("UPDATE parts SET height = 110 WHERE height IS NULL")
    c.execute("UPDATE parts SET icon = '🔩' WHERE icon IS NULL")

    c.execute("""
        CREATE TABLE IF NOT EXISTS replacement_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_id INTEGER NOT NULL,
            replaced_date TEXT NOT NULL,
            cost REAL DEFAULT 0,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (part_id) REFERENCES parts(id) ON DELETE CASCADE
        )
    """)
    existing_history_cols = {r["name"] for r in c.execute("PRAGMA table_info(replacement_history)").fetchall()}
    if "cost" not in existing_history_cols:
        c.execute("ALTER TABLE replacement_history ADD COLUMN cost REAL DEFAULT 0")
        c.execute("UPDATE replacement_history SET cost = 0 WHERE cost IS NULL")

    c.execute("""
        CREATE TABLE IF NOT EXISTS equipment_notes (
            equipment_id INTEGER PRIMARY KEY,
            content TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (equipment_id) REFERENCES equipments(id) ON DELETE CASCADE
        )
    """)
    old_notes_cols = {r["name"] for r in c.execute("PRAGMA table_info(equipment_notes)").fetchall()}
    if "equipment_id" not in old_notes_cols:
        c.execute("ALTER TABLE equipment_notes RENAME TO equipment_notes_old")
        c.execute("""
            CREATE TABLE equipment_notes (
                equipment_id INTEGER PRIMARY KEY,
                content TEXT DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (equipment_id) REFERENCES equipments(id) ON DELETE CASCADE
            )
        """)
        old_row = c.execute("SELECT content, updated_at FROM equipment_notes_old WHERE id = 1").fetchone()
        if old_row:
            c.execute(
                "INSERT INTO equipment_notes (equipment_id, content, updated_at) VALUES (1, ?, ?)",
                (old_row["content"], old_row["updated_at"]),
            )
        c.execute("DROP TABLE equipment_notes_old")
    c.execute(
        "INSERT OR IGNORE INTO equipment_notes (equipment_id, content) "
        "SELECT id, '' FROM equipments"
    )

    c.execute("""
        CREATE TABLE IF NOT EXISTS unit_notes (
            unit_id INTEGER PRIMARY KEY,
            content TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE
        )
    """)

    # 횡전개 현황판: 엑셀에서 붙여넣은 호기/Chamber별 진행 날짜 표(data_html)를 항목별로 저장
    c.execute("""
        CREATE TABLE IF NOT EXISTS rollout_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            data_html TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 메일 수신자 목록 (녹스 ID만 저장, @samsung.com 은 발송 시 자동으로 붙임)
    c.execute("""
        CREATE TABLE IF NOT EXISTS mail_recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS bulk_part_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_unit_text TEXT,
            unit_id INTEGER,
            part_name TEXT NOT NULL,
            q_code TEXT,
            note TEXT,
            cost REAL DEFAULT 0,
            cycle_days INTEGER DEFAULT 90,
            cycle_unit TEXT DEFAULT '일',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE SET NULL
        )
    """)
    existing_bulk_cols = {r["name"] for r in c.execute("PRAGMA table_info(bulk_part_entries)").fetchall()}
    if "cost" not in existing_bulk_cols:
        c.execute("ALTER TABLE bulk_part_entries ADD COLUMN cost REAL DEFAULT 0")
        c.execute("UPDATE bulk_part_entries SET cost = 0 WHERE cost IS NULL")
    if "cycle_days" not in existing_bulk_cols:
        c.execute("ALTER TABLE bulk_part_entries ADD COLUMN cycle_days INTEGER DEFAULT 90")
        c.execute("ALTER TABLE bulk_part_entries ADD COLUMN cycle_unit TEXT DEFAULT '일'")
        c.execute("UPDATE bulk_part_entries SET cycle_days = 90, cycle_unit = '일' WHERE cycle_days IS NULL")

    c.execute("""
        CREATE TABLE IF NOT EXISTS bulk_part_entry_units (
            entry_id INTEGER NOT NULL,
            unit_id INTEGER NOT NULL,
            PRIMARY KEY (entry_id, unit_id),
            FOREIGN KEY (entry_id) REFERENCES bulk_part_entries(id) ON DELETE CASCADE,
            FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE
        )
    """)
    if "unit_id" in existing_bulk_cols:
        for legacy in c.execute(
            "SELECT id, unit_id FROM bulk_part_entries WHERE unit_id IS NOT NULL"
        ).fetchall():
            c.execute(
                "INSERT OR IGNORE INTO bulk_part_entry_units (entry_id, unit_id) VALUES (?, ?)",
                (legacy["id"], legacy["unit_id"]),
            )

    # 모든 설비에 공통으로 반영되는 기본 유닛 구성 템플릿
    c.execute("""
        CREATE TABLE IF NOT EXISTS unit_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            icon TEXT DEFAULT '⚙️',
            color TEXT DEFAULT '#1a3a5c',
            pos_x REAL DEFAULT 50,
            pos_y REAL DEFAULT 50,
            width REAL DEFAULT 140,
            height REAL DEFAULT 110,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    template_count = c.execute("SELECT COUNT(*) AS n FROM unit_templates").fetchone()["n"]
    if template_count == 0:
        for name, icon, color, pos_x, pos_y in DEFAULT_UNITS:
            c.execute(
                "INSERT INTO unit_templates (name, icon, color, pos_x, pos_y) VALUES (?, ?, ?, ?, ?)",
                (name, icon, color, pos_x, pos_y),
            )

    # 각 설비에 유닛이 하나도 없으면 기본 유닛 구성 템플릿을 자동으로 반영
    templates = c.execute("SELECT * FROM unit_templates ORDER BY id").fetchall()
    for eq in c.execute("SELECT id FROM equipments").fetchall():
        unit_count = c.execute(
            "SELECT COUNT(*) AS n FROM units WHERE equipment_id = ?", (eq["id"],)
        ).fetchone()["n"]
        if unit_count == 0:
            for t in templates:
                c.execute(
                    "INSERT INTO units (equipment_id, name, icon, color, pos_x, pos_y, width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (eq["id"], t["name"], t["icon"], t["color"], t["pos_x"], t["pos_y"], t["width"], t["height"]),
                )

    # 메모/노트에 리치 텍스트(엑셀 표 붙여넣기 등) 편집 기능이 도입되기 전에 평문으로 저장된
    # 기존 값을, 화면에 보이던 모습 그대로 안전한 HTML로 1회 변환한다 (재실행돼도 한 번만 수행).
    if not c.execute("SELECT 1 FROM app_config WHERE key = 'memo_rich_migrated_v1'").fetchone():
        for row in c.execute("SELECT id, memo FROM parts WHERE memo IS NOT NULL AND memo != ''").fetchall():
            c.execute(
                "UPDATE parts SET memo = ? WHERE id = ?",
                (legacy_text_to_rich_html(row["memo"]), row["id"]),
            )
        for row in c.execute(
            "SELECT equipment_id, content FROM equipment_notes WHERE content IS NOT NULL AND content != ''"
        ).fetchall():
            c.execute(
                "UPDATE equipment_notes SET content = ? WHERE equipment_id = ?",
                (legacy_text_to_rich_html(row["content"]), row["equipment_id"]),
            )
        for row in c.execute(
            "SELECT unit_id, content FROM unit_notes WHERE content IS NOT NULL AND content != ''"
        ).fetchall():
            c.execute(
                "UPDATE unit_notes SET content = ? WHERE unit_id = ?",
                (legacy_text_to_rich_html(row["content"]), row["unit_id"]),
            )
        c.execute("INSERT INTO app_config (key, value) VALUES ('memo_rich_migrated_v1', '1')")

    # 설비/유닛/부품이 많아져도 목록 조회가 느려지지 않도록, 자주 필터링되는 외래키 컬럼에
    # 인덱스를 추가한다 (이미 있으면 아무 일도 하지 않음).
    c.execute("CREATE INDEX IF NOT EXISTS idx_units_equipment_id ON units(equipment_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_parts_unit_id ON parts(unit_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_replacement_history_part_id ON replacement_history(part_id)")

    conn.commit()
    conn.close()


def part_status(cycle_days, last_replaced_date):
    """부품 교체 주기 대비 경과 상태 계산"""
    if not last_replaced_date:
        return {"status": "unknown", "label": "미기록", "days_left": None, "next_due": None}
    last = datetime.strptime(last_replaced_date, "%Y-%m-%d").date()
    next_due = last + timedelta(days=cycle_days)
    days_left = (next_due - date.today()).days
    if days_left < 0:
        status, label = "overdue", "교체 필요"
    elif cycle_days > 0 and days_left <= max(cycle_days * 0.2, 3):
        status, label = "soon", "교체 임박"
    else:
        status, label = "ok", "정상"
    return {
        "status": status,
        "label": label,
        "days_left": days_left,
        "next_due": next_due.isoformat(),
    }


def serialize_part(row):
    info = part_status(row["cycle_days"], row["last_replaced_date"])
    d = dict(row)
    d.update(info)
    return d


def insert_part(conn, unit_id, name, spec="", cycle_days=90, cycle_unit="일", cost=0,
                 last_replaced_date=None, note="", memo="", drawing_data=None, icon="🔩",
                 pos_x=None, pos_y=None, width=130, height=110,
                 stock_qty=0, supplier="", supplier_contact="", lead_time_days=None):
    if pos_x is None or pos_y is None:
        pos_x, pos_y = random.uniform(15, 85), random.uniform(20, 80)
    cur = conn.execute(
        """INSERT INTO parts (unit_id, name, spec, cycle_days, cycle_unit, cost, last_replaced_date, note, memo,
           drawing_data, icon, pos_x, pos_y, width, height, stock_qty, supplier, supplier_contact, lead_time_days)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (unit_id, name, spec, cycle_days, cycle_unit, cost, last_replaced_date, note, memo, drawing_data, icon,
         pos_x, pos_y, width, height, stock_qty, supplier, supplier_contact, lead_time_days),
    )
    part_id = cur.lastrowid
    if last_replaced_date:
        conn.execute(
            "INSERT INTO replacement_history (part_id, replaced_date, note) VALUES (?, ?, ?)",
            (part_id, last_replaced_date, "최초 등록"),
        )
    return part_id


def apply_unit_parts_to_other_equipment(conn, unit_id):
    """기준 설비(TEAG01호기)의 특정 유닛에 등록된 부품 구성 전체를, 동일한 이름의 유닛을 가진
    나머지 설비에 일괄 동기화한다. 이름이 같은 부품은 규격/교체주기/비고/메모/도면/재고수량/구매처/
    리드타임/아이콘/위치/크기가 TEAG01호기 기준으로 갱신되고(재고는 전 설비가 동일하게 관리됨),
    새 부품은 추가되며, 여기 없는 이름의 부품은 삭제된다(교체 이력도 함께 삭제)."""
    master_unit = conn.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
    master_parts = conn.execute(
        "SELECT * FROM parts WHERE unit_id = ? AND deleted_at IS NULL ORDER BY id", (unit_id,)
    ).fetchall()
    master_names = {p["name"] for p in master_parts}

    target_units = conn.execute(
        "SELECT id FROM units WHERE name = ? AND equipment_id != ? AND deleted_at IS NULL",
        (master_unit["name"], MASTER_EQUIPMENT_ID),
    ).fetchall()

    for t in target_units:
        existing = {
            p["name"]: p
            for p in conn.execute("SELECT * FROM parts WHERE unit_id = ? AND deleted_at IS NULL", (t["id"],)).fetchall()
        }
        for mp in master_parts:
            if mp["name"] in existing:
                ep = existing[mp["name"]]
                conn.execute(
                    """UPDATE parts SET spec = ?, cycle_days = ?, cycle_unit = ?, cost = ?, note = ?, memo = ?,
                       drawing_data = ?, icon = ?, pos_x = ?, pos_y = ?, width = ?, height = ?,
                       stock_qty = ?, supplier = ?, supplier_contact = ?, lead_time_days = ? WHERE id = ?""",
                    (
                        mp["spec"], mp["cycle_days"], mp["cycle_unit"], mp["cost"], mp["note"], mp["memo"],
                        mp["drawing_data"], mp["icon"], mp["pos_x"], mp["pos_y"], mp["width"], mp["height"],
                        mp["stock_qty"], mp["supplier"], mp["supplier_contact"], mp["lead_time_days"], ep["id"],
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO parts (unit_id, name, spec, cycle_days, cycle_unit, cost, note, memo, drawing_data,
                       icon, pos_x, pos_y, width, height, stock_qty, supplier, supplier_contact, lead_time_days)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        t["id"], mp["name"], mp["spec"], mp["cycle_days"], mp["cycle_unit"], mp["cost"],
                        mp["note"], mp["memo"], mp["drawing_data"], mp["icon"], mp["pos_x"], mp["pos_y"], mp["width"], mp["height"],
                        mp["stock_qty"], mp["supplier"], mp["supplier_contact"], mp["lead_time_days"],
                    ),
                )
        for name, ep in existing.items():
            if name not in master_names:
                conn.execute("DELETE FROM parts WHERE id = ?", (ep["id"],))

    return len(target_units), len(master_parts)


def units_with_status_bulk(conn, units):
    """유닛 여러 개의 상태/부품수를 부품 테이블 조회 1번으로 한꺼번에 계산한다
    (유닛마다 따로 쿼리를 날리는 N+1 패턴을 피하기 위함)."""
    if not units:
        return []
    unit_ids = [u["id"] for u in units]
    placeholders = ",".join("?" for _ in unit_ids)
    all_parts = conn.execute(
        f"SELECT * FROM parts WHERE unit_id IN ({placeholders}) AND deleted_at IS NULL", unit_ids
    ).fetchall()
    parts_by_unit = {}
    for p in all_parts:
        parts_by_unit.setdefault(p["unit_id"], []).append(p)

    result = []
    for u in units:
        parts = parts_by_unit.get(u["id"], [])
        statuses = [part_status(p["cycle_days"], p["last_replaced_date"])["status"] for p in parts]
        if "overdue" in statuses:
            overall = "overdue"
        elif "soon" in statuses:
            overall = "soon"
        elif "unknown" in statuses:
            overall = "unknown" if not any(s == "ok" for s in statuses) else "ok"
        elif statuses:
            overall = "ok"
        else:
            overall = "empty"
        d = dict(u)
        d["part_count"] = len(parts)
        d["overall_status"] = overall
        d["overdue_count"] = statuses.count("overdue")
        d["soon_count"] = statuses.count("soon")
        result.append(d)
    return result


def unit_with_status(conn, u):
    return units_with_status_bulk(conn, [u])[0]


STATUS_PRIORITY = ["overdue", "soon", "unknown", "ok", "empty"]


def calc_setup_runtime(setup_date):
    """SETUP 일자로부터 오늘까지 경과한 기간을 "N년 M개월" 형식으로 계산"""
    if not setup_date:
        return None
    setup = datetime.strptime(setup_date, "%Y-%m-%d").date()
    today = date.today()
    if setup > today:
        return None
    years = today.year - setup.year
    months = today.month - setup.month
    if today.day < setup.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    return f"{years}년 {months}개월"


def equipments_with_status_bulk(conn, equipments):
    """설비 여러 개의 상태/유닛수를 유닛+부품 조회 2번으로 한꺼번에 계산한다
    (설비마다, 유닛마다 따로 쿼리를 날리는 N+1 패턴을 피하기 위함)."""
    if not equipments:
        return []
    eq_ids = [e["id"] for e in equipments]
    placeholders = ",".join("?" for _ in eq_ids)
    all_units = conn.execute(
        f"SELECT * FROM units WHERE equipment_id IN ({placeholders}) AND deleted_at IS NULL", eq_ids
    ).fetchall()
    unit_statuses_all = units_with_status_bulk(conn, all_units)
    units_by_equipment = {}
    for us in unit_statuses_all:
        units_by_equipment.setdefault(us["equipment_id"], []).append(us)

    result = []
    for e in equipments:
        unit_statuses = units_by_equipment.get(e["id"], [])
        statuses = [us["overall_status"] for us in unit_statuses]
        overall = next((s for s in STATUS_PRIORITY if s in statuses), "empty")
        d = dict(e)
        d["unit_count"] = len(unit_statuses)
        d["overall_status"] = overall
        d["overdue_count"] = sum(us["overdue_count"] for us in unit_statuses)
        d["soon_count"] = sum(us["soon_count"] for us in unit_statuses)
        d["runtime_display"] = calc_setup_runtime(e["setup_date"])
        result.append(d)
    return result


def equipment_with_status(conn, e):
    return equipments_with_status_bulk(conn, [e])[0]


def seed_default_units_for_equipment(conn, equipment_id):
    templates = conn.execute("SELECT * FROM unit_templates ORDER BY id").fetchall()
    for t in templates:
        conn.execute(
            "INSERT INTO units (equipment_id, name, icon, color, pos_x, pos_y, width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (equipment_id, t["name"], t["icon"], t["color"], t["pos_x"], t["pos_y"], t["width"], t["height"]),
        )


class _TableGridParser(HTMLParser):
    """rollout_items.data_html 의 첫 번째 표를 병합 셀(rowspan/colspan)까지
    반영한 2차원 격자로 펼친다 (화면의 게이지 집계 로직과 동일한 규칙)."""
    def __init__(self):
        super().__init__()
        self.grid = []
        self.row = -1
        self.col = 0
        self.in_cell = False
        self.cell_text = ""
        self.cell_span = (1, 1)
        self.cell_pos = (0, 0)
        self.table_depth = 0
        self.done_first_table = False

    def handle_starttag(self, tag, attrs):
        if self.done_first_table:
            return
        if tag == "table":
            self.table_depth += 1
            return
        if self.table_depth == 0:
            return
        if tag == "tr":
            self.row += 1
            self.col = 0
            while len(self.grid) <= self.row:
                self.grid.append({})
        elif tag in ("td", "th"):
            attrs = dict(attrs)
            colspan = int(attrs.get("colspan") or 1)
            rowspan = int(attrs.get("rowspan") or 1)
            while self.col in self.grid[self.row]:
                self.col += 1
            self.in_cell = True
            self.cell_text = ""
            self.cell_span = (rowspan, colspan)
            self.cell_pos = (self.row, self.col)

    def handle_endtag(self, tag):
        if self.done_first_table:
            return
        if tag == "table" and self.table_depth:
            self.table_depth -= 1
            if self.table_depth == 0:
                self.done_first_table = True
        elif tag in ("td", "th") and self.in_cell:
            r0, c0 = self.cell_pos
            rowspan, colspan = self.cell_span
            text = self.cell_text.strip()
            for dr in range(rowspan):
                while len(self.grid) <= r0 + dr:
                    self.grid.append({})
                for dc in range(colspan):
                    self.grid[r0 + dr][c0 + dc] = text
            self.col = c0 + colspan
            self.in_cell = False

    def handle_data(self, data):
        if self.in_cell:
            self.cell_text += data


def rollout_progress_from_html(data_html):
    """CH 이름(2열)이 있는 행만 대상으로 진행 날짜(3열) 기입 여부를 센다."""
    parser = _TableGridParser()
    parser.feed(data_html or "")
    done = pending = 0
    for r in range(1, len(parser.grid)):
        row = parser.grid[r]
        if not (row.get(1) or "").strip():
            continue
        if (row.get(2) or "").strip():
            done += 1
        else:
            pending += 1
    return done, pending


def build_mail_report_html():
    """메일 본문 HTML을 만든다.
    1번째: 사이트 접속 URL
    2번째: 횡전개 현황 (항목별 완료/미진행/전체/진행률)
    3번째: 이벤트 알림 - 재고 1개 이하 발생 리스트, 전날(발송 기준) 교체 기록 리스트"""
    conn = get_db()
    items = conn.execute("SELECT * FROM rollout_items ORDER BY id").fetchall()
    low_stock = [p for p in get_inventory_rows(conn) if (p["stock_qty"] or 0) <= 1]
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    yesterday_history = conn.execute("""
        SELECT h.cost, p.name AS part_name, p.spec,
               u.name AS unit_name, e.name AS equipment_name
        FROM replacement_history h
        JOIN parts p ON h.part_id = p.id
        JOIN units u ON p.unit_id = u.id
        JOIN equipments e ON u.equipment_id = e.id
        WHERE h.replaced_date = ?
        ORDER BY e.id, u.id, p.id
    """, (yesterday,)).fetchall()
    conn.close()

    td = "border:1px solid #ccc;padding:6px 12px"
    site_url = f"http://{get_lan_ip()}:5000"

    rollout_rows = ""
    for it in items:
        done, pending = rollout_progress_from_html(it["data_html"])
        total = done + pending
        pct = round(done / total * 100) if total else 0
        rollout_rows += (
            f"<tr><td style='{td}'>{html_lib.escape(it['title'])}</td>"
            f"<td style='{td};text-align:center'>{done}</td>"
            f"<td style='{td};text-align:center'>{pending}</td>"
            f"<td style='{td};text-align:center'>{total}</td>"
            f"<td style='{td};text-align:center;font-weight:bold'>{pct}%</td></tr>"
        )
    if not rollout_rows:
        rollout_rows = f"<tr><td colspan='5' style='{td}'>등록된 횡전개 항목이 없습니다.</td></tr>"

    low_stock_rows = ""
    for p in low_stock:
        low_stock_rows += (
            f"<tr><td style='{td}'>{html_lib.escape(p['name'])}</td>"
            f"<td style='{td}'>{html_lib.escape(p['spec'] or '')}</td>"
            f"<td style='{td}'>{html_lib.escape(p['unit_name'])}</td>"
            f"<td style='{td};text-align:center;color:#c0392b;font-weight:bold'>{p['stock_qty'] or 0}</td></tr>"
        )
    if not low_stock_rows:
        low_stock_rows = f"<tr><td colspan='4' style='{td}'>재고 1개 이하 부품이 없습니다.</td></tr>"

    history_rows = ""
    for h in yesterday_history:
        history_rows += (
            f"<tr><td style='{td}'>{html_lib.escape(h['equipment_name'])}</td>"
            f"<td style='{td}'>{html_lib.escape(h['unit_name'])}</td>"
            f"<td style='{td}'>{html_lib.escape(h['part_name'])}</td>"
            f"<td style='{td}'>{html_lib.escape(h['spec'] or '')}</td>"
            f"<td style='{td};text-align:right'>{h['cost'] or 0:,.0f}원</td></tr>"
        )
    if not history_rows:
        history_rows = f"<tr><td colspan='5' style='{td}'>{yesterday} 교체 기록이 없습니다.</td></tr>"

    return (
        f"<p style='font-size:14px'>사이트 접속: <a href='{site_url}'>{site_url}</a></p>"
        f"<h3>횡전개 현황</h3>"
        f"<table style='border-collapse:collapse;font-size:14px;margin-bottom:20px'>"
        f"<tr style='background:#f3f4f6'>"
        f"<th style='{td}'>횡전개 항목</th><th style='{td}'>완료</th>"
        f"<th style='{td}'>미진행</th><th style='{td}'>전체</th><th style='{td}'>진행률</th></tr>"
        f"{rollout_rows}</table>"
        f"<h3>이벤트 알림</h3>"
        f"<p style='font-size:14px;margin-bottom:4px'><b>재고 1개 이하 발생</b></p>"
        f"<table style='border-collapse:collapse;font-size:14px;margin-bottom:16px'>"
        f"<tr style='background:#f3f4f6'>"
        f"<th style='{td}'>부품명</th><th style='{td}'>규격</th><th style='{td}'>소속 유닛</th><th style='{td}'>재고</th></tr>"
        f"{low_stock_rows}</table>"
        f"<p style='font-size:14px;margin-bottom:4px'><b>전날({yesterday}) 교체 기록</b></p>"
        f"<table style='border-collapse:collapse;font-size:14px'>"
        f"<tr style='background:#f3f4f6'>"
        f"<th style='{td}'>설비</th><th style='{td}'>유닛</th><th style='{td}'>부품</th>"
        f"<th style='{td}'>규격</th><th style='{td}'>금액</th></tr>"
        f"{history_rows}</table>"
    )


def send_status_mail():
    """사내 메일 API로 횡전개 현황 리포트를 발송한다. (성공 여부, 메시지) 반환."""
    conn = get_db()
    receivers = [
        r["email_id"]
        for r in conn.execute("SELECT email_id FROM mail_recipients ORDER BY id").fetchall()
    ]
    conn.close()
    if not receivers:
        return False, "수신자가 등록되어 있지 않습니다. 메일 설정에서 수신자를 먼저 추가하세요."
    if "XXXX" in MAIL_AUTHORIZATION or not MAIL_AUTHORIZATION:
        return False, "메일 API 키가 설정되지 않았습니다. app.py 상단의 MAIL_AUTHORIZATION / MAIL_SYSTEM_ID 를 확인하세요."
    api = "https://openapi.samsung.net/mail/api/v2.0/mails/send?userId=" + MAIL_SENDER_ID
    header = {"Authorization": MAIL_AUTHORIZATION, "System-ID": MAIL_SYSTEM_ID}
    body = {
        "subject": MAIL_SUBJECT,
        "contents": build_mail_report_html(),
        "contentType": "HTML",
        "docSecuType": "PERSONAL",
        "sender": {"emailAddress": f"{MAIL_SENDER_ID}@samsung.com"},
        "recipients": [
            {"emailAddress": f"{r}@samsung.com", "recipientType": "TO"} for r in receivers
        ],
    }
    try:
        res = requests.post(api, headers=header, json=body, timeout=15)
        if res.status_code // 100 == 2:
            return True, f"수신자 {len(receivers)}명에게 발송 완료"
        return False, f"메일 API 오류 (HTTP {res.status_code}): {res.text[:200]}"
    except Exception as e:
        return False, f"메일 발송 실패: {e}"


def get_alert_parts():
    """모든 설비를 통틀어 교체 필요/임박 상태인 부품 목록 (경과가 급한 순)"""
    conn = get_db()
    rows = conn.execute("""
        SELECT p.*, u.id AS unit_id, u.name AS unit_name,
               e.id AS equipment_id, e.name AS equipment_name, e.icon AS equipment_icon
        FROM parts p
        JOIN units u ON p.unit_id = u.id
        JOIN equipments e ON u.equipment_id = e.id
        WHERE p.deleted_at IS NULL AND u.deleted_at IS NULL AND e.deleted_at IS NULL
    """).fetchall()
    conn.close()
    result = []
    for r in rows:
        info = part_status(r["cycle_days"], r["last_replaced_date"])
        if info["status"] in ("overdue", "soon"):
            d = dict(r)
            d.update(info)
            result.append(d)
    result.sort(key=lambda x: x["days_left"])
    return result


def search_parts(query, status=None, equipment_id=None):
    """부품명/규격으로 모든 설비를 통틀어 검색 (상태/설비로 추가 필터링 가능)"""
    conn = get_db()
    like = f"%{query}%"
    sql = """
        SELECT p.*, u.id AS unit_id, u.name AS unit_name,
               e.id AS equipment_id, e.name AS equipment_name, e.icon AS equipment_icon
        FROM parts p
        JOIN units u ON p.unit_id = u.id
        JOIN equipments e ON u.equipment_id = e.id
        WHERE (p.name LIKE ? OR p.spec LIKE ?)
          AND p.deleted_at IS NULL AND u.deleted_at IS NULL AND e.deleted_at IS NULL
    """
    params = [like, like]
    if equipment_id:
        sql += " AND e.id = ?"
        params.append(equipment_id)
    sql += " ORDER BY e.id, u.id, p.id"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    result = []
    for r in rows:
        info = part_status(r["cycle_days"], r["last_replaced_date"])
        if status and info["status"] != status:
            continue
        d = dict(r)
        d.update(info)
        result.append(d)
    return result


def get_part_spec_stats(conn, unit_names=None, start_date=None, end_date=None):
    """부품명+규격을 기준으로 시스템 전체(선택된 유닛 이름으로 범위 제한 가능)를 집계한다.
    start_date/end_date가 주어지면 금액/사용량은 해당 기간의 교체 이력(replacement_history)을
    기준으로 계산하고, 교체주기는 항상 현재 부품 구성 기준으로 계산한다."""
    query = """
        SELECT p.*, u.name AS unit_name
        FROM parts p
        JOIN units u ON p.unit_id = u.id
        WHERE p.deleted_at IS NULL AND u.deleted_at IS NULL
    """
    params = []
    if unit_names:
        placeholders = ",".join("?" for _ in unit_names)
        query += f" AND u.name IN ({placeholders})"
        params = list(unit_names)
    parts = conn.execute(query, params).fetchall()
    period_filter = bool(start_date or end_date)

    # 그룹마다 따로 교체 이력을 조회하는 대신, 관련된 모든 부품의 이력을 한 번에 가져와
    # part_id 기준으로 집계해둔다 (N+1 쿼리 방지).
    hist_by_part = {}
    part_ids = [p["id"] for p in parts]
    if part_ids:
        placeholders = ",".join("?" for _ in part_ids)
        hist_query = f"SELECT part_id, cost FROM replacement_history WHERE part_id IN ({placeholders})"
        hist_params = list(part_ids)
        if start_date:
            hist_query += " AND replaced_date >= ?"
            hist_params.append(start_date)
        if end_date:
            hist_query += " AND replaced_date <= ?"
            hist_params.append(end_date)
        for h in conn.execute(hist_query, hist_params).fetchall():
            entry = hist_by_part.setdefault(h["part_id"], {"n": 0, "total": 0})
            entry["n"] += 1
            entry["total"] += h["cost"] or 0

    groups = {}
    for p in parts:
        key = (p["name"], p["spec"] or "")
        g = groups.get(key)
        if g is None:
            g = {
                "name": p["name"],
                "spec": p["spec"] or "",
                "total_cost": 0,
                "instance_count": 0,
                "usage_count": 0,
                "min_cycle_days": None,
                "min_cycle_unit": "일",
            }
            groups[key] = g
        g["instance_count"] += 1
        if g["min_cycle_days"] is None or p["cycle_days"] < g["min_cycle_days"]:
            g["min_cycle_days"] = p["cycle_days"]
            g["min_cycle_unit"] = p["cycle_unit"]
        hist = hist_by_part.get(p["id"], {"n": 0, "total": 0})
        g["usage_count"] += hist["n"]
        if period_filter:
            g["total_cost"] += hist["total"]
        else:
            g["total_cost"] += p["cost"] or 0

    return list(groups.values())


def csv_response(filename, header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    data = "﻿" + buf.getvalue()
    encoded_name = quote(filename)
    return Response(
        data,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=export.csv; filename*=UTF-8''{encoded_name}"
        },
    )


LOGIN_EXEMPT_PREFIXES = ("/static/", "/login")


@app.before_request
def require_login():
    if request.path.startswith(LOGIN_EXEMPT_PREFIXES):
        return None
    if session.get("authenticated") and session.get("user_name"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "로그인이 필요합니다"}), 401
    return redirect(url_for("login_page", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password", "")
        if not name:
            return redirect(url_for("login_page", error="2"))
        conn = get_db()
        password_hash = get_config(conn, "password_hash")
        conn.close()
        if password_hash and check_password_hash(password_hash, password):
            session["authenticated"] = True
            session["user_name"] = name
            session.permanent = True
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        return redirect(url_for("login_page", error="1"))
    return LOGIN_HTML


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/api/auth/change-password", methods=["POST"])
def change_password():
    data = request.get_json()
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""
    if len(new_password) < 4:
        return jsonify({"error": "비밀번호는 4자 이상으로 설정하세요"}), 400
    conn = get_db()
    password_hash = get_config(conn, "password_hash")
    if not password_hash or not check_password_hash(password_hash, current_password):
        conn.close()
        return jsonify({"error": "현재 비밀번호가 올바르지 않습니다"}), 400
    set_config(conn, "password_hash", generate_password_hash(new_password))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/")
def dashboard():
    return DASHBOARD_HTML.replace("__USER_NAME__", _html_escape(session.get("user_name", "")))


@app.route("/alerts")
def alerts_page():
    return ALERTS_HTML


@app.route("/search")
def search_page():
    return SEARCH_HTML


@app.route("/stats")
def stats_page():
    return STATS_HTML


@app.route("/bulk-add-parts")
def bulk_add_parts_page():
    return BULK_ADD_PARTS_HTML


@app.route("/inventory")
def inventory_page():
    return INVENTORY_HTML


def get_inventory_rows(conn):
    """재고는 TEAG01호기(기준 설비) 기준으로 전 설비가 동일하게 관리되므로,
    나머지 설비는 동일한 내용이 중복되어 나타나는 것을 막기 위해 TEAG01호기 것만 보여준다."""
    return conn.execute("""
        SELECT p.id, p.name, p.spec, p.stock_qty, p.supplier, p.supplier_contact, p.lead_time_days,
               u.id AS unit_id, u.name AS unit_name
        FROM parts p
        JOIN units u ON p.unit_id = u.id
        JOIN equipments e ON u.equipment_id = e.id
        WHERE p.deleted_at IS NULL AND u.deleted_at IS NULL AND e.deleted_at IS NULL
          AND e.id = ?
        ORDER BY p.stock_qty ASC, u.id, p.id
    """, (MASTER_EQUIPMENT_ID,)).fetchall()


@app.route("/api/inventory")
def api_inventory():
    conn = get_db()
    rows = get_inventory_rows(conn)
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/inventory/export")
def export_inventory_csv():
    low_only = request.args.get("low_only") == "1"
    conn = get_db()
    rows = get_inventory_rows(conn)
    conn.close()
    header = ["부품이름", "규격", "소속 유닛", "재고 수량", "구매처", "연락처", "리드타임(일)"]
    data_rows = []
    for p in rows:
        if low_only and (p["stock_qty"] or 0) > 0:
            continue
        data_rows.append([
            p["name"], p["spec"] or "", p["unit_name"],
            p["stock_qty"] or 0, p["supplier"] or "", p["supplier_contact"] or "",
            p["lead_time_days"] if p["lead_time_days"] is not None else "",
        ])
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return csv_response(f"재고관리_{timestamp}.csv", header, data_rows)


@app.route("/api/mail/recipients")
def list_mail_recipients():
    conn = get_db()
    rows = conn.execute("SELECT * FROM mail_recipients ORDER BY id").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/mail/recipients", methods=["POST"])
def add_mail_recipient():
    data = request.get_json()
    email_id = (data.get("email_id") or "").strip().replace("@samsung.com", "")
    if not email_id:
        return jsonify({"error": "수신자 녹스 ID를 입력하세요"}), 400
    conn = get_db()
    try:
        conn.execute("INSERT INTO mail_recipients (email_id) VALUES (?)", (email_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "이미 등록된 수신자입니다"}), 409
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/mail/recipients/<int:rid>", methods=["DELETE"])
def delete_mail_recipient(rid):
    conn = get_db()
    conn.execute("DELETE FROM mail_recipients WHERE id = ?", (rid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/mail/send", methods=["POST"])
def api_send_mail():
    ok, msg = send_status_mail()
    if ok:
        conn = get_db()
        log_activity(conn, "mail", "mail", None, MAIL_SUBJECT, msg)
        conn.commit()
        conn.close()
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 500)


@app.route("/api/mail/schedule", methods=["GET", "PUT"])
def api_mail_schedule():
    conn = get_db()
    if request.method == "PUT":
        t = (request.get_json().get("time") or "").strip()  # "HH:MM" 또는 "" (해제)
        set_config(conn, "mail_schedule_time", t)
        conn.commit()
    t = get_config(conn, "mail_schedule_time") or ""
    conn.close()
    return jsonify({"time": t})


@app.route("/rollout")
def rollout_page():
    return ROLLOUT_HTML


@app.route("/api/rollout")
def list_rollout_items():
    conn = get_db()
    rows = conn.execute("SELECT * FROM rollout_items ORDER BY id").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/rollout", methods=["POST"])
def add_rollout_item():
    data = request.get_json()
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "제목을 입력하세요"}), 400
    data_html = sanitize_rich_html(data.get("data_html") or "")
    conn = get_db()
    cur = conn.execute("INSERT INTO rollout_items (title, data_html) VALUES (?, ?)", (title, data_html))
    new_id = cur.lastrowid
    log_activity(conn, "create", "rollout", new_id, title, "횡전개 항목 추가")
    conn.commit()
    row = conn.execute("SELECT * FROM rollout_items WHERE id = ?", (new_id,)).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


@app.route("/api/rollout/<int:item_id>", methods=["PUT"])
def update_rollout_item(item_id):
    data = request.get_json()
    conn = get_db()
    row = conn.execute("SELECT * FROM rollout_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "항목을 찾을 수 없습니다"}), 404
    title = (data.get("title") or row["title"]).strip()
    data_html = sanitize_rich_html(data.get("data_html", row["data_html"]))
    conn.execute(
        "UPDATE rollout_items SET title = ?, data_html = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (title, data_html, item_id),
    )
    log_activity(conn, "update", "rollout", item_id, title, "횡전개 항목 수정")
    conn.commit()
    updated = conn.execute("SELECT * FROM rollout_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return jsonify(dict(updated))


@app.route("/api/rollout/<int:item_id>", methods=["DELETE"])
def delete_rollout_item(item_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM rollout_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "항목을 찾을 수 없습니다"}), 404
    conn.execute("DELETE FROM rollout_items WHERE id = ?", (item_id,))
    log_activity(conn, "delete", "rollout", item_id, row["title"], "횡전개 항목 삭제")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/equipment/<int:equipment_id>")
def equipment_page(equipment_id):
    conn = get_db()
    equipment = conn.execute(
        "SELECT id FROM equipments WHERE id = ? AND deleted_at IS NULL", (equipment_id,)
    ).fetchone()
    conn.close()
    if not equipment:
        return "설비를 찾을 수 없습니다", 404
    return EQUIPMENT_HTML.replace("__EQUIPMENT_ID__", str(equipment_id))


@app.route("/config")
def unit_template_config():
    return CONFIG_HTML


@app.route("/unit/<int:unit_id>")
def unit_detail(unit_id):
    conn = get_db()
    unit = conn.execute(
        "SELECT id FROM units WHERE id = ? AND deleted_at IS NULL", (unit_id,)
    ).fetchone()
    conn.close()
    if not unit:
        return "유닛을 찾을 수 없습니다", 404
    return UNIT_HTML.replace("__UNIT_ID__", str(unit_id))


@app.route("/api/equipments")
def list_equipments():
    conn = get_db()
    equipments = conn.execute("SELECT * FROM equipments WHERE deleted_at IS NULL ORDER BY id").fetchall()
    result = equipments_with_status_bulk(conn, equipments)
    conn.close()
    return jsonify(result)


@app.route("/api/equipments", methods=["POST"])
def add_equipment():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "설비 이름을 입력하세요"}), 400
    icon = (data.get("icon") or "🏭").strip()
    conn = get_db()
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM equipments").fetchone()["n"]
        pos_x, pos_y = dashboard_grid_pos(count)
        cur = conn.execute(
            "INSERT INTO equipments (name, icon, pos_x, pos_y) VALUES (?, ?, ?, ?)",
            (name, icon, pos_x, pos_y),
        )
        new_id = cur.lastrowid
        conn.execute(
            "INSERT INTO equipment_notes (equipment_id, content) VALUES (?, '')", (new_id,)
        )
        seed_default_units_for_equipment(conn, new_id)
        log_activity(conn, "create", "equipment", new_id, name)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "이미 사용 중인 설비 이름입니다"}), 409
    equipment = conn.execute("SELECT * FROM equipments WHERE id = ?", (new_id,)).fetchone()
    d = equipment_with_status(conn, equipment)
    conn.close()
    return jsonify(d), 201


@app.route("/api/equipments/<int:equipment_id>")
def get_equipment(equipment_id):
    conn = get_db()
    equipment = conn.execute(
        "SELECT * FROM equipments WHERE id = ? AND deleted_at IS NULL", (equipment_id,)
    ).fetchone()
    if not equipment:
        conn.close()
        return jsonify({"error": "설비를 찾을 수 없습니다"}), 404
    d = equipment_with_status(conn, equipment)
    conn.close()
    return jsonify(d)


@app.route("/api/equipments/<int:equipment_id>", methods=["PUT"])
def update_equipment(equipment_id):
    data = request.get_json()
    conn = get_db()
    equipment = conn.execute("SELECT * FROM equipments WHERE id = ?", (equipment_id,)).fetchone()
    if not equipment:
        conn.close()
        return jsonify({"error": "설비를 찾을 수 없습니다"}), 404
    name = (data.get("name") or equipment["name"]).strip()
    icon = (data.get("icon") or equipment["icon"]).strip()
    pos_x = data.get("pos_x", equipment["pos_x"])
    pos_y = data.get("pos_y", equipment["pos_y"])
    location = data.get("location", equipment["location"])
    setup_date = data.get("setup_date", equipment["setup_date"]) or None
    meaningful_change = (
        name != equipment["name"] or icon != equipment["icon"]
        or location != equipment["location"] or setup_date != equipment["setup_date"]
    )
    try:
        conn.execute(
            "UPDATE equipments SET name = ?, icon = ?, pos_x = ?, pos_y = ?, location = ?, setup_date = ? WHERE id = ?",
            (name, icon, pos_x, pos_y, location, setup_date, equipment_id),
        )
        if meaningful_change:
            log_activity(conn, "update", "equipment", equipment_id, name, "설비 정보 수정")
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "이미 사용 중인 설비 이름입니다"}), 409
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/equipments/<int:equipment_id>", methods=["DELETE"])
def delete_equipment(equipment_id):
    conn = get_db()
    equipment = conn.execute("SELECT * FROM equipments WHERE id = ?", (equipment_id,)).fetchone()
    if not equipment:
        conn.close()
        return jsonify({"error": "설비를 찾을 수 없습니다"}), 404
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE equipments SET deleted_at = ? WHERE id = ?", (now, equipment_id))
    unit_ids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM units WHERE equipment_id = ? AND deleted_at IS NULL", (equipment_id,)
        ).fetchall()
    ]
    if unit_ids:
        placeholders = ",".join("?" for _ in unit_ids)
        conn.execute(f"UPDATE units SET deleted_at = ? WHERE id IN ({placeholders})", (now, *unit_ids))
        conn.execute(f"UPDATE parts SET deleted_at = ? WHERE unit_id IN ({placeholders})", (now, *unit_ids))
    log_activity(conn, "delete", "equipment", equipment_id, equipment["name"], "휴지통으로 이동")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/equipments/<int:equipment_id>/units")
def list_units(equipment_id):
    conn = get_db()
    units = conn.execute(
        "SELECT * FROM units WHERE equipment_id = ? AND deleted_at IS NULL ORDER BY id", (equipment_id,)
    ).fetchall()
    result = units_with_status_bulk(conn, units)
    conn.close()
    return jsonify(result)


@app.route("/api/equipments/<int:equipment_id>/units", methods=["POST"])
def add_unit(equipment_id):
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "유닛 이름을 입력하세요"}), 400
    icon = (data.get("icon") or "⚙️").strip()
    color = (data.get("color") or "#1a3a5c").strip()
    pos_x = data.get("pos_x")
    pos_y = data.get("pos_y")
    if pos_x is None or pos_y is None:
        pos_x, pos_y = random.uniform(15, 85), random.uniform(20, 80)
    width = data.get("width") or 140
    height = data.get("height") or 110
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO units (equipment_id, name, icon, color, pos_x, pos_y, width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (equipment_id, name, icon, color, pos_x, pos_y, width, height),
    )
    conn.commit()
    new_id = cur.lastrowid
    unit = conn.execute("SELECT * FROM units WHERE id = ?", (new_id,)).fetchone()
    log_activity(conn, "create", "unit", new_id, name)
    conn.commit()
    conn.close()
    d = dict(unit)
    d["part_count"] = 0
    d["overall_status"] = "empty"
    return jsonify(d), 201


@app.route("/api/units/<int:unit_id>")
def get_unit(unit_id):
    conn = get_db()
    unit = conn.execute(
        "SELECT * FROM units WHERE id = ? AND deleted_at IS NULL", (unit_id,)
    ).fetchone()
    if not unit:
        conn.close()
        return jsonify({"error": "유닛을 찾을 수 없습니다"}), 404
    d = unit_with_status(conn, unit)
    conn.close()
    return jsonify(d)


@app.route("/api/units/<int:unit_id>", methods=["PUT"])
def update_unit(unit_id):
    data = request.get_json()
    conn = get_db()
    unit = conn.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
    if not unit:
        conn.close()
        return jsonify({"error": "유닛을 찾을 수 없습니다"}), 404
    name = (data.get("name") or unit["name"]).strip()
    icon = (data.get("icon") or unit["icon"]).strip()
    color = (data.get("color") or unit["color"]).strip()
    pos_x = data.get("pos_x", unit["pos_x"])
    pos_y = data.get("pos_y", unit["pos_y"])
    width = data.get("width", unit["width"])
    height = data.get("height", unit["height"])
    meaningful_change = name != unit["name"] or icon != unit["icon"] or color != unit["color"]
    conn.execute(
        "UPDATE units SET name = ?, icon = ?, color = ?, pos_x = ?, pos_y = ?, width = ?, height = ? WHERE id = ?",
        (name, icon, color, pos_x, pos_y, width, height, unit_id),
    )
    if meaningful_change:
        log_activity(conn, "update", "unit", unit_id, name, "유닛 정보 수정")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/units/<int:unit_id>", methods=["DELETE"])
def delete_unit(unit_id):
    conn = get_db()
    unit = conn.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
    if not unit:
        conn.close()
        return jsonify({"error": "유닛을 찾을 수 없습니다"}), 404
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE units SET deleted_at = ? WHERE id = ?", (now, unit_id))
    conn.execute("UPDATE parts SET deleted_at = ? WHERE unit_id = ? AND deleted_at IS NULL", (now, unit_id))
    log_activity(conn, "delete", "unit", unit_id, unit["name"], "휴지통으로 이동")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/units/<int:unit_id>/parts")
def list_parts(unit_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM parts WHERE unit_id = ? AND deleted_at IS NULL ORDER BY id", (unit_id,)
    ).fetchall()
    conn.close()
    return jsonify([serialize_part(r) for r in rows])


@app.route("/api/units/<int:unit_id>/parts", methods=["POST"])
def add_part(unit_id):
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "부품 이름을 입력하세요"}), 400
    spec = (data.get("spec") or "").strip()
    cycle_days = int(data.get("cycle_days") or 90)
    cycle_unit = (data.get("cycle_unit") or "일").strip()
    cost = float(data.get("cost") or 0)
    last_replaced_date = data.get("last_replaced_date") or None
    note = (data.get("note") or "").strip()
    memo = sanitize_rich_html((data.get("memo") or "").strip())
    drawing_data = data.get("drawing_data") or None
    if drawing_data and len(drawing_data) > MAX_DRAWING_DATA_LEN:
        return jsonify({"error": "도면 이미지 용량이 너무 큽니다 (최대 5MB)"}), 400
    icon = (data.get("icon") or "🔩").strip()
    width = data.get("width") or 130
    height = data.get("height") or 110
    stock_qty = int(data.get("stock_qty") or 0)
    supplier = (data.get("supplier") or "").strip()
    supplier_contact = (data.get("supplier_contact") or "").strip()
    lead_time_days = data.get("lead_time_days")
    lead_time_days = int(lead_time_days) if lead_time_days not in (None, "") else None

    conn = get_db()
    part_id = insert_part(
        conn, unit_id, name, spec=spec, cycle_days=cycle_days, cycle_unit=cycle_unit, cost=cost,
        last_replaced_date=last_replaced_date, note=note, memo=memo, drawing_data=drawing_data, icon=icon,
        pos_x=data.get("pos_x"), pos_y=data.get("pos_y"), width=width, height=height,
        stock_qty=stock_qty, supplier=supplier, supplier_contact=supplier_contact, lead_time_days=lead_time_days,
    )
    log_activity(conn, "create", "part", part_id, name)
    conn.commit()
    row = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    conn.close()
    return jsonify(serialize_part(row)), 201


@app.route("/api/units/<int:unit_id>/apply-parts", methods=["POST"])
def apply_unit_parts(unit_id):
    conn = get_db()
    unit = conn.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
    if not unit:
        conn.close()
        return jsonify({"error": "유닛을 찾을 수 없습니다"}), 404
    if unit["equipment_id"] != MASTER_EQUIPMENT_ID:
        conn.close()
        return jsonify({"error": "기준 설비(TEAG01호기)의 유닛에서만 사용할 수 있습니다"}), 400
    equipment_count, part_count = apply_unit_parts_to_other_equipment(conn, unit_id)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "equipment_count": equipment_count, "part_count": part_count})


def parse_cycle_text(text):
    """"90", "90일", "2년" 등의 텍스트를 (cycle_days, cycle_unit) 튜플로 변환.
    비어있거나 해석할 수 없으면 기본값(90일)을 반환한다."""
    text = (text or "").strip()
    if not text:
        return 90, "일"
    if text.endswith("년"):
        try:
            years = float(text[:-1].strip())
            return round(years * 365), "년"
        except ValueError:
            return 90, "일"
    text = text[:-1].strip() if text.endswith("일") else text
    try:
        return round(float(text)), "일"
    except ValueError:
        return 90, "일"


def parse_bulk_paste_text(text):
    """붙여넣은 텍스트(탭 또는 쉼표 구분)를 (유닛이름, 부품이름, Q-CODE, 부가설명, 금액, 교체주기일수, 교체주기단위)
    튜플 목록으로 변환. 교체주기는 "90", "90일", "2년"과 같이 입력할 수 있다."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            cols = line.split(",")
        cols = [c.strip() for c in cols]
        while len(cols) < 6:
            cols.append("")
        unit_text, part_name, q_code, note, cost_text, cycle_text = cols[0], cols[1], cols[2], cols[3], cols[4], cols[5]
        if not part_name:
            continue
        try:
            cost = float(cost_text) if cost_text else 0
        except ValueError:
            cost = 0
        cycle_days, cycle_unit = parse_cycle_text(cycle_text)
        rows.append((unit_text, part_name, q_code, note, cost, cycle_days, cycle_unit))
    return rows


def serialize_bulk_entry(row):
    d = dict(row)
    return d


def get_template_mapped_units(conn):
    """기본 유닛 구성(unit_templates)의 이름을 기준으로, 기준 설비(TEAG01호기)의 실제
    유닛 id에 매핑한 목록을 반환한다. (부품은 실제 유닛에만 등록할 수 있으므로, 선택 항목의
    이름 기준은 기본 유닛 구성을 따르되 등록 대상은 TEAG01호기의 해당 유닛으로 연결한다.)"""
    return conn.execute("""
        SELECT u.id AS id, t.name AS name
        FROM unit_templates t
        JOIN units u ON u.equipment_id = ? AND u.name = t.name
        WHERE u.deleted_at IS NULL
        ORDER BY t.id
    """, (MASTER_EQUIPMENT_ID,)).fetchall()


@app.route("/api/bulk-parts")
def list_bulk_parts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM bulk_part_entries ORDER BY id DESC").fetchall()
    entries = []
    for r in rows:
        units = conn.execute("""
            SELECT u.id, u.name, u.icon
            FROM bulk_part_entry_units beu
            JOIN units u ON beu.unit_id = u.id
            WHERE beu.entry_id = ?
            ORDER BY u.id
        """, (r["id"],)).fetchall()
        d = serialize_bulk_entry(r)
        d["units"] = [dict(u) for u in units]
        entries.append(d)
    master_units = get_template_mapped_units(conn)
    conn.close()
    return jsonify({
        "entries": entries,
        "master_units": [dict(u) for u in master_units],
    })


@app.route("/api/bulk-parts/paste", methods=["POST"])
def paste_bulk_parts():
    data = request.get_json()
    text = data.get("text") or ""
    parsed = parse_bulk_paste_text(text)
    if not parsed:
        return jsonify({"error": "붙여넣은 내용에서 부품 정보를 찾을 수 없습니다"}), 400

    conn = get_db()
    created_ids = []
    for unit_text, part_name, q_code, note, cost, cycle_days, cycle_unit in parsed:
        cur = conn.execute(
            "INSERT INTO bulk_part_entries (raw_unit_text, part_name, q_code, note, cost, cycle_days, cycle_unit) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (unit_text, part_name, q_code, note, cost, cycle_days, cycle_unit),
        )
        created_ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "created_count": len(created_ids)}), 201


@app.route("/api/bulk-parts/<int:entry_id>", methods=["PUT"])
def update_bulk_part(entry_id):
    data = request.get_json()
    conn = get_db()
    entry = conn.execute("SELECT * FROM bulk_part_entries WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        conn.close()
        return jsonify({"error": "항목을 찾을 수 없습니다"}), 404
    part_name = (data.get("part_name") or entry["part_name"]).strip()
    q_code = data.get("q_code", entry["q_code"])
    note = data.get("note", entry["note"])
    cost = data.get("cost", entry["cost"])
    cycle_days = int(data.get("cycle_days") or entry["cycle_days"])
    cycle_unit = (data.get("cycle_unit") or entry["cycle_unit"]).strip()
    conn.execute(
        "UPDATE bulk_part_entries SET part_name = ?, q_code = ?, note = ?, cost = ?, cycle_days = ?, cycle_unit = ? WHERE id = ?",
        (part_name, q_code, note, cost, cycle_days, cycle_unit, entry_id),
    )
    if "unit_ids" in data:
        conn.execute("DELETE FROM bulk_part_entry_units WHERE entry_id = ?", (entry_id,))
        for uid in data["unit_ids"]:
            conn.execute(
                "INSERT OR IGNORE INTO bulk_part_entry_units (entry_id, unit_id) VALUES (?, ?)",
                (entry_id, uid),
            )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/bulk-parts/<int:entry_id>/register", methods=["POST"])
def register_bulk_part(entry_id):
    conn = get_db()
    entry = conn.execute("SELECT * FROM bulk_part_entries WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        conn.close()
        return jsonify({"error": "항목을 찾을 수 없습니다"}), 404
    unit_ids = [
        r["unit_id"] for r in conn.execute(
            "SELECT unit_id FROM bulk_part_entry_units WHERE entry_id = ?", (entry_id,)
        ).fetchall()
    ]
    if not unit_ids:
        conn.close()
        return jsonify({"error": "유닛을 먼저 선택하세요"}), 400

    part_ids = []
    for uid in unit_ids:
        unit = conn.execute("SELECT * FROM units WHERE id = ? AND deleted_at IS NULL", (uid,)).fetchone()
        if not unit:
            continue
        part_id = insert_part(
            conn, uid, entry["part_name"], spec=entry["q_code"] or "",
            cost=entry["cost"] or 0, note=entry["note"] or "",
            cycle_days=entry["cycle_days"] or 90, cycle_unit=entry["cycle_unit"] or "일",
        )
        part_ids.append(part_id)
        log_activity(conn, "create", "part", part_id, entry["part_name"], "부품 일괄 등록")
    conn.execute("UPDATE bulk_part_entries SET status = 'registered' WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "part_ids": part_ids})


@app.route("/api/bulk-parts/<int:entry_id>", methods=["DELETE"])
def delete_bulk_part(entry_id):
    conn = get_db()
    conn.execute("DELETE FROM bulk_part_entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/parts/<int:part_id>", methods=["PUT"])
def update_part(part_id):
    data = request.get_json()
    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    if not part:
        conn.close()
        return jsonify({"error": "부품을 찾을 수 없습니다"}), 404
    name = (data.get("name") or part["name"]).strip()
    spec = data.get("spec", part["spec"])
    cycle_days = int(data.get("cycle_days") or part["cycle_days"])
    cycle_unit = (data.get("cycle_unit") or part["cycle_unit"]).strip()
    cost = data.get("cost", part["cost"])
    note = data.get("note", part["note"])
    memo = sanitize_rich_html(data.get("memo", part["memo"]))
    drawing_data = data.get("drawing_data", part["drawing_data"])
    if drawing_data and len(drawing_data) > MAX_DRAWING_DATA_LEN:
        conn.close()
        return jsonify({"error": "도면 이미지 용량이 너무 큽니다 (최대 5MB)"}), 400
    icon = (data.get("icon") or part["icon"]).strip()
    pos_x = data.get("pos_x", part["pos_x"])
    pos_y = data.get("pos_y", part["pos_y"])
    width = data.get("width", part["width"])
    height = data.get("height", part["height"])
    stock_qty = int(data.get("stock_qty", part["stock_qty"]) or 0)
    supplier = data.get("supplier", part["supplier"])
    supplier_contact = data.get("supplier_contact", part["supplier_contact"])
    lead_time_days = data.get("lead_time_days", part["lead_time_days"])
    lead_time_days = int(lead_time_days) if lead_time_days not in (None, "") else None
    meaningful_change = (
        name != part["name"] or spec != part["spec"] or cycle_days != part["cycle_days"]
        or cycle_unit != part["cycle_unit"] or cost != part["cost"] or note != part["note"]
        or stock_qty != (part["stock_qty"] or 0) or supplier != part["supplier"]
    )
    conn.execute(
        """UPDATE parts SET name = ?, spec = ?, cycle_days = ?, cycle_unit = ?, cost = ?, note = ?, memo = ?,
           drawing_data = ?, icon = ?, pos_x = ?, pos_y = ?, width = ?, height = ?,
           stock_qty = ?, supplier = ?, supplier_contact = ?, lead_time_days = ? WHERE id = ?""",
        (name, spec, cycle_days, cycle_unit, cost, note, memo, drawing_data, icon, pos_x, pos_y, width, height,
         stock_qty, supplier, supplier_contact, lead_time_days, part_id),
    )
    if meaningful_change:
        log_activity(conn, "update", "part", part_id, name, "부품 정보 수정")
    conn.commit()
    row = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    conn.close()
    return jsonify(serialize_part(row))


@app.route("/api/parts/<int:part_id>", methods=["DELETE"])
def delete_part(part_id):
    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    if not part:
        conn.close()
        return jsonify({"error": "부품을 찾을 수 없습니다"}), 404
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE parts SET deleted_at = ? WHERE id = ?", (now, part_id))
    log_activity(conn, "delete", "part", part_id, part["name"], "휴지통으로 이동")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/parts/<int:part_id>/replace", methods=["POST"])
def replace_part(part_id):
    data = request.get_json()
    replaced_date = data.get("replaced_date") or str(date.today())
    cost = float(data.get("cost") or 0)
    note = (data.get("note") or "").strip()

    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    if not part:
        conn.close()
        return jsonify({"error": "부품을 찾을 수 없습니다"}), 404
    conn.execute(
        "INSERT INTO replacement_history (part_id, replaced_date, cost, note) VALUES (?, ?, ?, ?)",
        (part_id, replaced_date, cost, note),
    )
    conn.execute(
        "UPDATE parts SET last_replaced_date = ? WHERE id = ? AND (last_replaced_date IS NULL OR ? >= last_replaced_date)",
        (replaced_date, part_id, replaced_date),
    )
    log_activity(conn, "replace", "part", part_id, part["name"], f"교체 기록 추가 ({replaced_date})")
    conn.commit()
    row = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    conn.close()
    return jsonify(serialize_part(row)), 201


@app.route("/api/parts/<int:part_id>/history")
def part_history(part_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM replacement_history WHERE part_id = ? ORDER BY replaced_date DESC, id DESC",
        (part_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/history/<int:history_id>", methods=["DELETE"])
def delete_history(history_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM replacement_history WHERE id = ?", (history_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "이력을 찾을 수 없습니다"}), 404
    part_id = row["part_id"]
    conn.execute("DELETE FROM replacement_history WHERE id = ?", (history_id,))
    # 삭제 후 남아있는 이력 중 가장 최근 날짜를 기준으로 교체주기 계산용 last_replaced_date를 다시 계산한다
    # (남은 이력이 없으면 "미기록" 상태로 되돌아간다).
    remaining = conn.execute(
        "SELECT MAX(replaced_date) AS last_date FROM replacement_history WHERE part_id = ?", (part_id,)
    ).fetchone()
    conn.execute(
        "UPDATE parts SET last_replaced_date = ? WHERE id = ?",
        (remaining["last_date"], part_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/equipments/<int:equipment_id>/notes")
def get_notes(equipment_id):
    conn = get_db()
    row = conn.execute(
        "SELECT content, updated_at FROM equipment_notes WHERE equipment_id = ?", (equipment_id,)
    ).fetchone()
    conn.close()
    return jsonify(dict(row) if row else {"content": "", "updated_at": None})


@app.route("/api/equipments/<int:equipment_id>/notes", methods=["PUT"])
def update_notes(equipment_id):
    data = request.get_json()
    content = sanitize_rich_html(data.get("content") or "")
    conn = get_db()
    conn.execute(
        "INSERT INTO equipment_notes (equipment_id, content, updated_at) VALUES (?, ?, datetime('now','localtime')) "
        "ON CONFLICT(equipment_id) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at",
        (equipment_id, content),
    )
    conn.commit()
    row = conn.execute(
        "SELECT content, updated_at FROM equipment_notes WHERE equipment_id = ?", (equipment_id,)
    ).fetchone()
    conn.close()
    return jsonify(dict(row))


@app.route("/api/units/<int:unit_id>/notes")
def get_unit_notes(unit_id):
    conn = get_db()
    row = conn.execute(
        "SELECT content, updated_at FROM unit_notes WHERE unit_id = ?", (unit_id,)
    ).fetchone()
    conn.close()
    return jsonify(dict(row) if row else {"content": "", "updated_at": None})


@app.route("/api/units/<int:unit_id>/notes", methods=["PUT"])
def update_unit_notes(unit_id):
    data = request.get_json()
    content = sanitize_rich_html(data.get("content") or "")
    conn = get_db()
    conn.execute(
        "INSERT INTO unit_notes (unit_id, content, updated_at) VALUES (?, ?, datetime('now','localtime')) "
        "ON CONFLICT(unit_id) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at",
        (unit_id, content),
    )
    conn.commit()
    row = conn.execute(
        "SELECT content, updated_at FROM unit_notes WHERE unit_id = ?", (unit_id,)
    ).fetchone()
    conn.close()
    return jsonify(dict(row))


@app.route("/api/unit-templates")
def list_unit_templates():
    conn = get_db()
    rows = conn.execute("SELECT * FROM unit_templates ORDER BY id").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/unit-templates", methods=["POST"])
def add_unit_template():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "유닛 이름을 입력하세요"}), 400
    icon = (data.get("icon") or "⚙️").strip()
    color = (data.get("color") or "#1a3a5c").strip()
    pos_x = data.get("pos_x")
    pos_y = data.get("pos_y")
    if pos_x is None or pos_y is None:
        pos_x, pos_y = random.uniform(15, 85), random.uniform(20, 80)
    width = data.get("width") or 140
    height = data.get("height") or 110
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO unit_templates (name, icon, color, pos_x, pos_y, width, height) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, icon, color, pos_x, pos_y, width, height),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM unit_templates WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


@app.route("/api/unit-templates/<int:template_id>", methods=["PUT"])
def update_unit_template(template_id):
    data = request.get_json()
    conn = get_db()
    t = conn.execute("SELECT * FROM unit_templates WHERE id = ?", (template_id,)).fetchone()
    if not t:
        conn.close()
        return jsonify({"error": "유닛을 찾을 수 없습니다"}), 404
    name = (data.get("name") or t["name"]).strip()
    icon = (data.get("icon") or t["icon"]).strip()
    color = (data.get("color") or t["color"]).strip()
    pos_x = data.get("pos_x", t["pos_x"])
    pos_y = data.get("pos_y", t["pos_y"])
    width = data.get("width", t["width"])
    height = data.get("height", t["height"])
    conn.execute(
        "UPDATE unit_templates SET name = ?, icon = ?, color = ?, pos_x = ?, pos_y = ?, width = ?, height = ? WHERE id = ?",
        (name, icon, color, pos_x, pos_y, width, height, template_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/unit-templates/<int:template_id>", methods=["DELETE"])
def delete_unit_template(template_id):
    conn = get_db()
    conn.execute("DELETE FROM unit_templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/unit-templates/apply", methods=["POST"])
def apply_unit_templates():
    conn = get_db()
    templates = conn.execute("SELECT * FROM unit_templates ORDER BY id").fetchall()
    equipments = conn.execute("SELECT id FROM equipments WHERE deleted_at IS NULL").fetchall()
    template_names = {t["name"] for t in templates}

    for eq in equipments:
        existing = {
            u["name"]: u
            for u in conn.execute(
                "SELECT * FROM units WHERE equipment_id = ? AND deleted_at IS NULL", (eq["id"],)
            ).fetchall()
        }
        for t in templates:
            if t["name"] in existing:
                u = existing[t["name"]]
                conn.execute(
                    "UPDATE units SET icon = ?, color = ?, pos_x = ?, pos_y = ?, width = ?, height = ? WHERE id = ?",
                    (t["icon"], t["color"], t["pos_x"], t["pos_y"], t["width"], t["height"], u["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO units (equipment_id, name, icon, color, pos_x, pos_y, width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (eq["id"], t["name"], t["icon"], t["color"], t["pos_x"], t["pos_y"], t["width"], t["height"]),
                )
        for name, u in existing.items():
            if name not in template_names:
                conn.execute("DELETE FROM units WHERE id = ?", (u["id"],))

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "equipment_count": len(equipments), "unit_count": len(templates)})


@app.route("/api/alerts")
def api_alerts():
    return jsonify(get_alert_parts())


@app.route("/api/alerts/export")
def export_alerts_csv():
    parts = get_alert_parts()
    header = ["상태", "설비", "유닛", "부품명", "규격", "교체주기(일)", "최근교체일", "다음교체예정일", "남은/초과일수", "비고"]
    rows = []
    for p in parts:
        days_text = f"{abs(p['days_left'])}일 초과" if p["status"] == "overdue" else f"{p['days_left']}일 남음"
        rows.append([
            p["label"], p["equipment_name"], p["unit_name"], p["name"], p.get("spec") or "",
            p["cycle_days"], p.get("last_replaced_date") or "", p.get("next_due") or "",
            days_text, p.get("note") or "",
        ])
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return csv_response(f"교체현황_{timestamp}.csv", header, rows)


@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status") or None
    equipment_id = request.args.get("equipment_id")
    equipment_id = int(equipment_id) if equipment_id else None
    return jsonify(search_parts(q, status=status, equipment_id=equipment_id))


def build_stats_payload(conn, unit_names, start_date=None, end_date=None):
    """부품 규격 기준(금액순/사용량 많은순/교체주기 짧은순)과 유닛 기준(부품수 많은순) 통계를 함께 만든다."""
    spec_rows = get_part_spec_stats(conn, unit_names, start_date=start_date, end_date=end_date)
    by_cost = sorted(spec_rows, key=lambda r: r["total_cost"], reverse=True)
    by_usage = sorted(spec_rows, key=lambda r: r["usage_count"], reverse=True)
    by_short_cycle = sorted(
        (r for r in spec_rows if r["min_cycle_days"] is not None), key=lambda r: r["min_cycle_days"]
    )

    unit_query = """
        SELECT u.id AS unit_id, u.name AS unit_name, u.icon AS unit_icon,
               e.id AS equipment_id, e.name AS equipment_name, e.icon AS equipment_icon,
               (SELECT COUNT(*) FROM parts p WHERE p.unit_id = u.id AND p.deleted_at IS NULL) AS part_count
        FROM units u
        JOIN equipments e ON u.equipment_id = e.id
        WHERE u.deleted_at IS NULL AND e.deleted_at IS NULL
    """
    params = []
    if unit_names:
        placeholders = ",".join("?" for _ in unit_names)
        unit_query += f" AND u.name IN ({placeholders})"
        params = unit_names
    unit_query += " ORDER BY u.id"
    unit_rows = [dict(r) for r in conn.execute(unit_query, params).fetchall()]
    by_part_count = sorted(unit_rows, key=lambda r: r["part_count"], reverse=True)

    all_unit_names = [
        r["name"] for r in conn.execute(
            "SELECT DISTINCT name FROM units WHERE deleted_at IS NULL ORDER BY name"
        ).fetchall()
    ]

    return {
        "by_cost": by_cost,
        "by_usage": by_usage,
        "by_short_cycle": by_short_cycle,
        "by_part_count": by_part_count,
        "unit_names": all_unit_names,
        "period_active": bool(start_date or end_date),
    }


@app.route("/api/stats")
def api_stats():
    unit_names = request.args.getlist("unit_name")
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None
    conn = get_db()
    payload = build_stats_payload(conn, unit_names, start_date=start_date, end_date=end_date)
    conn.close()
    return jsonify(payload)


def format_cycle_for_export(days, unit):
    if unit == "년":
        return f"{round(days / 365, 2)}년"
    return f"{days}일"


@app.route("/api/stats/export.csv")
def export_stats_csv():
    unit_names = request.args.getlist("unit_name")
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None
    conn = get_db()
    payload = build_stats_payload(conn, unit_names, start_date=start_date, end_date=end_date)
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(["[금액순 (부품 규격 기준)]"])
    writer.writerow(["순위", "부품명", "규격", "총 금액", "등록 수", "교체 횟수"])
    for i, r in enumerate(payload["by_cost"], 1):
        writer.writerow([i, r["name"], r["spec"], r["total_cost"], r["instance_count"], r["usage_count"]])
    writer.writerow([])

    writer.writerow(["[사용량 많은순 (부품 규격 기준)]"])
    writer.writerow(["순위", "부품명", "규격", "교체 횟수", "등록 수", "총 금액"])
    for i, r in enumerate(payload["by_usage"], 1):
        writer.writerow([i, r["name"], r["spec"], r["usage_count"], r["instance_count"], r["total_cost"]])
    writer.writerow([])

    writer.writerow(["[교체 주기 짧은순 (부품 규격 기준)]"])
    writer.writerow(["순위", "부품명", "규격", "교체 주기", "등록 수"])
    for i, r in enumerate(payload["by_short_cycle"], 1):
        writer.writerow([
            i, r["name"], r["spec"], format_cycle_for_export(r["min_cycle_days"], r["min_cycle_unit"]),
            r["instance_count"],
        ])
    writer.writerow([])

    writer.writerow(["[부품수 많은순 (유닛 기준)]"])
    writer.writerow(["순위", "설비", "유닛", "부품수"])
    for i, r in enumerate(payload["by_part_count"], 1):
        writer.writerow([i, r["equipment_name"], r["unit_name"], r["part_count"]])

    data = "﻿" + buf.getvalue()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"통계_{timestamp}.csv"
    encoded_name = quote(filename)
    return Response(
        data,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=stats.csv; filename*=UTF-8''{encoded_name}"
        },
    )


def build_stats_pptx(payload, unit_names):
    """조건별(금액순/사용량순/교체주기순/부품수순) TOP 5를 표+막대 그래프로 함께 보여주는
    보고서 형태의 PPT를 생성한다."""
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    title_slide = prs.slides.add_slide(prs.slide_layouts[0])
    title_slide.shapes.title.text = "부품/유닛 통계 리포트"
    filter_text = ", ".join(unit_names) if unit_names else "전체 유닛"
    title_slide.placeholders[1].text = (
        f"생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n필터: {filter_text}\n"
        f"조건별 TOP 5 표 및 막대 그래프"
    )

    blank_layout = prs.slide_layouts[6]

    def add_report_slide(title, headers, rows, chart_labels, chart_values, chart_value_title, number_format="0"):
        """표(왼쪽, 상위 5건)와 막대 그래프(오른쪽)를 함께 보여주는 보고서 슬라이드를 추가한다."""
        slide = prs.slides.add_slide(blank_layout)
        title_box = slide.shapes.add_textbox(Inches(0.4), Inches(0.3), Inches(12.5), Inches(0.7))
        tf = title_box.text_frame
        tf.text = title
        tf.paragraphs[0].font.size = Pt(28)
        tf.paragraphs[0].font.bold = True

        n_rows = len(rows) + 1
        n_cols = len(headers)
        if rows:
            table = slide.shapes.add_table(
                n_rows, n_cols, Inches(0.4), Inches(1.3), Inches(6.1), Inches(0.5 * n_rows)
            ).table
            for c, h in enumerate(headers):
                cell = table.cell(0, c)
                cell.text = str(h)
                cell.text_frame.paragraphs[0].font.bold = True
                cell.text_frame.paragraphs[0].font.size = Pt(13)
            for r_i, row in enumerate(rows, 1):
                for c_i, val in enumerate(row):
                    cell = table.cell(r_i, c_i)
                    cell.text = str(val)
                    cell.text_frame.paragraphs[0].font.size = Pt(12)
        else:
            empty_box = slide.shapes.add_textbox(Inches(0.4), Inches(1.5), Inches(6.1), Inches(1.0))
            empty_box.text_frame.text = "표시할 데이터가 없습니다."

        if chart_labels:
            chart_data = CategoryChartData()
            chart_data.categories = chart_labels
            chart_data.add_series(chart_value_title, chart_values)
            chart_frame = slide.shapes.add_chart(
                XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(6.9), Inches(1.3), Inches(6.0), Inches(4.8), chart_data
            )
            chart = chart_frame.chart
            chart.has_legend = False
            plot = chart.plots[0]
            plot.has_data_labels = True
            plot.data_labels.font.size = Pt(11)
            plot.data_labels.number_format = number_format
            plot.data_labels.number_format_is_linked = False
            chart.category_axis.tick_labels.font.size = Pt(10)
            chart.value_axis.tick_labels.font.size = Pt(10)

    def chart_label(r):
        return f'{r["name"]} ({r["spec"]})' if r.get("spec") else r["name"]

    by_cost5 = payload["by_cost"][:5]
    add_report_slide(
        "금액순 TOP 5 (부품 규격 기준)",
        ["순위", "부품명", "규격", "총 금액(원)", "등록 수", "교체 횟수"],
        [
            [i, r["name"], r["spec"], f'{r["total_cost"]:,.0f}', r["instance_count"], r["usage_count"]]
            for i, r in enumerate(by_cost5, 1)
        ],
        [chart_label(r) for r in by_cost5],
        [r["total_cost"] for r in by_cost5],
        "총 금액(원)",
        number_format="#,##0",
    )

    by_usage5 = payload["by_usage"][:5]
    add_report_slide(
        "사용량 많은순 TOP 5 (부품 규격 기준)",
        ["순위", "부품명", "규격", "교체 횟수", "등록 수", "총 금액(원)"],
        [
            [i, r["name"], r["spec"], r["usage_count"], r["instance_count"], f'{r["total_cost"]:,.0f}']
            for i, r in enumerate(by_usage5, 1)
        ],
        [chart_label(r) for r in by_usage5],
        [r["usage_count"] for r in by_usage5],
        "교체 횟수",
    )

    by_short5 = payload["by_short_cycle"][:5]
    add_report_slide(
        "교체 주기 짧은순 TOP 5 (부품 규격 기준)",
        ["순위", "부품명", "규격", "교체 주기", "등록 수"],
        [
            [i, r["name"], r["spec"], format_cycle_for_export(r["min_cycle_days"], r["min_cycle_unit"]), r["instance_count"]]
            for i, r in enumerate(by_short5, 1)
        ],
        [chart_label(r) for r in by_short5],
        [r["min_cycle_days"] for r in by_short5],
        "교체 주기(일)",
    )

    by_part5 = payload["by_part_count"][:5]
    add_report_slide(
        "부품수 많은순 TOP 5 (유닛 기준)",
        ["순위", "설비", "유닛", "부품수"],
        [
            [i, r["equipment_name"], r["unit_name"], r["part_count"]]
            for i, r in enumerate(by_part5, 1)
        ],
        [f'{r["equipment_name"]} {r["unit_name"]}' for r in by_part5],
        [r["part_count"] for r in by_part5],
        "부품수",
    )

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf


@app.route("/api/stats/export.pptx")
def export_stats_pptx():
    unit_names = request.args.getlist("unit_name")
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None
    try:
        conn = get_db()
        payload = build_stats_payload(conn, unit_names, start_date=start_date, end_date=end_date)
        conn.close()
        buf = build_stats_pptx(payload, unit_names)
    except Exception:
        # PPT 생성 중 예기치 못한 오류가 나도 서버가 조용히 죽거나 모호한 메시지 대신,
        # 콘솔에 원인을 남기고 클라이언트에는 명확한 오류를 반환한다.
        traceback.print_exc()
        return jsonify({"error": "PPT 생성 중 오류가 발생했습니다. 서버 콘솔의 오류 메시지를 확인해주세요."}), 500
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"통계_{timestamp}.pptx",
        mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@app.route("/api/backup", methods=["POST"])
def download_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"equipment_backup_{timestamp}.db"
    dest_path = os.path.join(BACKUP_DIR, filename)
    shutil.copyfile(DB_PATH, dest_path)
    conn = get_db()
    log_activity(conn, "backup", "backup", None, filename, f"백업 파일 저장: {dest_path}")
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "filename": filename, "path": dest_path})


@app.route("/trash")
def trash_page():
    return TRASH_HTML


@app.route("/api/trash")
def api_trash():
    conn = get_db()
    equipments = conn.execute("""
        SELECT *, (SELECT COUNT(*) FROM units WHERE equipment_id = equipments.id) AS unit_count
        FROM equipments WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC
    """).fetchall()
    # 소속 설비/유닛이 함께 삭제된(연쇄 삭제된) 항목은 부모를 복원하면 같이 복원되므로
    # 여기서는 "단독으로" 삭제된 유닛/부품만 보여준다 (부모는 살아있는데 이것만 삭제된 경우).
    units = conn.execute("""
        SELECT u.*, e.name AS equipment_name
        FROM units u
        JOIN equipments e ON u.equipment_id = e.id
        WHERE u.deleted_at IS NOT NULL AND e.deleted_at IS NULL
        ORDER BY u.deleted_at DESC
    """).fetchall()
    parts = conn.execute("""
        SELECT p.*, u.name AS unit_name, e.name AS equipment_name
        FROM parts p
        JOIN units u ON p.unit_id = u.id
        JOIN equipments e ON u.equipment_id = e.id
        WHERE p.deleted_at IS NOT NULL AND u.deleted_at IS NULL
        ORDER BY p.deleted_at DESC
    """).fetchall()
    conn.close()
    return jsonify({
        "equipments": [dict(r) for r in equipments],
        "units": [dict(r) for r in units],
        "parts": [dict(r) for r in parts],
    })


@app.route("/api/trash/equipment/<int:equipment_id>/restore", methods=["POST"])
def restore_equipment(equipment_id):
    conn = get_db()
    equipment = conn.execute("SELECT * FROM equipments WHERE id = ?", (equipment_id,)).fetchone()
    if not equipment:
        conn.close()
        return jsonify({"error": "설비를 찾을 수 없습니다"}), 404
    conn.execute("UPDATE equipments SET deleted_at = NULL WHERE id = ?", (equipment_id,))
    unit_ids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM units WHERE equipment_id = ?", (equipment_id,)
        ).fetchall()
    ]
    if unit_ids:
        placeholders = ",".join("?" for _ in unit_ids)
        conn.execute(f"UPDATE units SET deleted_at = NULL WHERE id IN ({placeholders})", unit_ids)
        conn.execute(f"UPDATE parts SET deleted_at = NULL WHERE unit_id IN ({placeholders})", unit_ids)
    log_activity(conn, "restore", "equipment", equipment_id, equipment["name"], "휴지통에서 복원")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/trash/unit/<int:unit_id>/restore", methods=["POST"])
def restore_unit(unit_id):
    conn = get_db()
    unit = conn.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
    if not unit:
        conn.close()
        return jsonify({"error": "유닛을 찾을 수 없습니다"}), 404
    conn.execute("UPDATE units SET deleted_at = NULL WHERE id = ?", (unit_id,))
    conn.execute("UPDATE parts SET deleted_at = NULL WHERE unit_id = ?", (unit_id,))
    log_activity(conn, "restore", "unit", unit_id, unit["name"], "휴지통에서 복원")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/trash/part/<int:part_id>/restore", methods=["POST"])
def restore_part(part_id):
    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    if not part:
        conn.close()
        return jsonify({"error": "부품을 찾을 수 없습니다"}), 404
    conn.execute("UPDATE parts SET deleted_at = NULL WHERE id = ?", (part_id,))
    log_activity(conn, "restore", "part", part_id, part["name"], "휴지통에서 복원")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/trash/equipment/<int:equipment_id>", methods=["DELETE"])
def permanent_delete_equipment(equipment_id):
    conn = get_db()
    equipment = conn.execute("SELECT * FROM equipments WHERE id = ?", (equipment_id,)).fetchone()
    if equipment:
        conn.execute("DELETE FROM equipments WHERE id = ?", (equipment_id,))
        log_activity(conn, "permanent_delete", "equipment", equipment_id, equipment["name"], "영구 삭제")
        conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/trash/unit/<int:unit_id>", methods=["DELETE"])
def permanent_delete_unit(unit_id):
    conn = get_db()
    unit = conn.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
    if unit:
        conn.execute("DELETE FROM units WHERE id = ?", (unit_id,))
        log_activity(conn, "permanent_delete", "unit", unit_id, unit["name"], "영구 삭제")
        conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/trash/part/<int:part_id>", methods=["DELETE"])
def permanent_delete_part(part_id):
    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    if part:
        conn.execute("DELETE FROM parts WHERE id = ?", (part_id,))
        log_activity(conn, "permanent_delete", "part", part_id, part["name"], "영구 삭제")
        conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/activity-log")
def activity_log_page():
    return ACTIVITY_LOG_HTML


@app.route("/api/activity-log")
def api_activity_log():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM activity_log ORDER BY id DESC LIMIT 300"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


def mail_scheduler_loop():
    """30초마다 예약 시간을 확인해, 설정된 시각(HH:MM)이 되면 하루 1회 발송한다."""
    last_sent_date = None
    while True:
        try:
            conn = get_db()
            t = get_config(conn, "mail_schedule_time")
            conn.close()
            if t:
                now = datetime.now()
                if now.strftime("%H:%M") == t and last_sent_date != now.strftime("%Y-%m-%d"):
                    ok, msg = send_status_mail()
                    print(f"[예약 메일] {now:%Y-%m-%d %H:%M} → {msg}")
                    last_sent_date = now.strftime("%Y-%m-%d")
        except Exception as e:
            print("[예약 메일] 오류:", e)
        time.sleep(30)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>설비 대시보드 - 설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <i class="bi bi-grid-3x3-gap-fill"></i>
    <h1>설비 대시보드</h1>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span id="clock" class="clock"></span>
    <a href="/alerts" class="btn btn-sm btn-outline-light">
      <i class="bi bi-exclamation-triangle"></i> 전체 현황<span id="alertsNavBadge"></span>
    </a>
    <a href="/search" class="btn btn-sm btn-outline-light">
      <i class="bi bi-search"></i> 부품 검색
    </a>
    <button id="statsBtn" class="btn btn-sm btn-outline-light">
      <i class="bi bi-bar-chart-fill"></i> 통계
    </button>
    <a href="/config" class="btn btn-sm btn-outline-light">
      <i class="bi bi-diagram-3"></i> 기본 유닛 구성
    </a>
    <a href="/bulk-add-parts" class="btn btn-sm btn-outline-light">
      <i class="bi bi-stack"></i> 부품 추가
    </a>
    <a href="/inventory" class="btn btn-sm btn-outline-light">
      <i class="bi bi-boxes"></i> 재고 관리
    </a>
    <a href="/rollout" class="btn btn-sm btn-outline-light">
      <i class="bi bi-clipboard2-check"></i> 횡전개 현황판
    </a>
    <button id="mailBtn" class="btn btn-sm btn-outline-light">
      <i class="bi bi-envelope"></i> 메일 보내기
    </button>
    <button id="backupBtn" class="btn btn-sm btn-outline-light">
      <i class="bi bi-download"></i> DB 백업
    </button>
    <button id="addEquipmentBtn" class="btn btn-sm btn-outline-light">
      <i class="bi bi-plus-lg"></i> 설비 추가
    </button>
    <button id="editModeBtn" class="btn btn-sm btn-outline-light">
      <i class="bi bi-pencil-square"></i> 설비 편집
    </button>
    <div class="dropdown">
      <button class="btn btn-sm btn-outline-light dropdown-toggle" type="button" id="accountMenuBtn" data-bs-toggle="dropdown">
        <i class="bi bi-person-circle"></i> __USER_NAME__
      </button>
      <ul class="dropdown-menu dropdown-menu-end">
        <li><a class="dropdown-item" href="/trash"><i class="bi bi-trash3"></i> 휴지통</a></li>
        <li><a class="dropdown-item" href="/activity-log"><i class="bi bi-clock-history"></i> 변경 이력</a></li>
        <li><hr class="dropdown-divider"></li>
        <li><button type="button" class="dropdown-item" id="changePasswordBtn"><i class="bi bi-key"></i> 비밀번호 변경</button></li>
        <li><a class="dropdown-item" href="/logout"><i class="bi bi-box-arrow-right"></i> 로그아웃</a></li>
      </ul>
    </div>
  </div>
</header>

<main class="container-fluid py-4">

  <div class="legend mb-3">
    <span class="legend-item"><span class="dot dot-ok"></span> 정상</span>
    <span class="legend-item"><span class="dot dot-soon"></span> 교체 임박</span>
    <span class="legend-item"><span class="dot dot-overdue"></span> 교체 필요</span>
    <span class="legend-item"><span class="dot dot-unknown"></span> 미기록 / 부품 없음</span>
  </div>

  <div class="dashboard-toolbar">
    <div class="search-box">
      <i class="bi bi-search"></i>
      <input type="text" id="equipmentSearch" class="form-control form-control-sm" placeholder="설비 이름 검색">
    </div>
    <select id="equipmentSort" class="form-select form-select-sm w-auto">
      <option value="custom" selected>자유 배치</option>
      <option value="name">이름순</option>
      <option value="overdue">교체 필요 많은 순</option>
    </select>
  </div>

  <div class="equipment-frame">
    <div class="equipment-label">편집 모드에서 설비를 드래그해 자유롭게 배치 · 클릭하면 상세 페이지로 이동</div>
    <div id="equipmentGrid" class="equipment-canvas dashboard-canvas"></div>
  </div>
  <p id="noResultsMsg" class="text-muted text-center py-4 d-none">검색 결과가 없습니다.</p>

</main>

<!-- 메일 발송/설정 모달 -->
<div class="modal fade" id="mailModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title"><i class="bi bi-envelope"></i> 현황 메일 발송</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <label class="form-label">수신자 (녹스 ID, @samsung.com 제외)</label>
        <div class="d-flex gap-2 mb-2">
          <input type="text" class="form-control form-control-sm" id="mailRecipientInput" placeholder="예: hong.gildong">
          <button id="addRecipientBtn" class="btn btn-sm btn-primary flex-shrink-0">추가</button>
        </div>
        <ul id="mailRecipientList" class="list-group mb-3"></ul>

        <label class="form-label">매일 자동 발송 시각 (비워두면 자동 발송 안 함)</label>
        <div class="d-flex gap-2 mb-3">
          <input type="time" class="form-control form-control-sm" id="mailScheduleTime">
          <button id="saveScheduleBtn" class="btn btn-sm btn-outline-secondary flex-shrink-0">시간 저장</button>
          <button id="clearScheduleBtn" class="btn btn-sm btn-outline-secondary flex-shrink-0">해제</button>
        </div>

        <button id="sendMailNowBtn" class="btn btn-primary w-100">
          <i class="bi bi-send"></i> 지금 바로 발송
        </button>
        <p id="mailStatusMsg" class="text-muted small text-center mt-2 mb-0"></p>
      </div>
    </div>
  </div>
</div>

<!-- 설비 편집 모달 -->
<div class="modal fade" id="equipmentEditModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="equipmentEditTitle">설비 편집</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <form id="equipmentEditForm">
          <input type="hidden" id="equipmentEditId">
          <div class="mb-2">
            <label class="form-label">설비 이름</label>
            <input type="text" class="form-control" id="equipmentEditName" required>
          </div>
          <div class="mb-2">
            <label class="form-label">아이콘 (이모지)</label>
            <input type="text" class="form-control" id="equipmentEditIcon" placeholder="🏭">
          </div>
          <div id="equipmentIconPicker" class="icon-picker"></div>
          <button type="submit" class="btn btn-primary w-100 mt-3">저장</button>
        </form>
      </div>
    </div>
  </div>
</div>

<!-- 비밀번호 변경 모달 -->
<div class="modal fade" id="changePasswordModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">비밀번호 변경</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <form id="changePasswordForm">
          <div class="mb-2">
            <label class="form-label">현재 비밀번호</label>
            <input type="password" class="form-control" id="currentPasswordInput" required>
          </div>
          <div class="mb-2">
            <label class="form-label">새 비밀번호 (4자 이상)</label>
            <input type="password" class="form-control" id="newPasswordInput" minlength="4" required>
          </div>
          <button type="submit" class="btn btn-primary w-100 mt-2">변경</button>
        </form>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
let editMode = false;
let equipmentEditModal;
let allEquipments = [];

const ICON_CHOICES = [
  "🏭", "⚙️", "🔧", "🔩", "🛠️", "🪛", "🔨", "📦",
  "🖥️", "🖨️", "💻", "📡", "🎛️", "🚨", "💡", "🔌",
  "⚡", "🔋", "🌡️", "💧", "🧪", "🧯", "🧰", "⚗️",
  "🌀", "🗜️", "🧲", "📊", "🛞", "🚿", "🔥", "❄️",
];

function renderIconPicker(containerId, inputId, current) {
  const container = document.getElementById(containerId);
  container.innerHTML = ICON_CHOICES.map(
    (ic) => `<button type="button" class="icon-choice ${ic === current ? "selected" : ""}" data-icon="${ic}">${ic}</button>`
  ).join("");
  container.querySelectorAll(".icon-choice").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById(inputId).value = btn.dataset.icon;
      container.querySelectorAll(".icon-choice").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
    });
  });
}

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "요청 처리 중 오류가 발생했습니다");
  }
  return res.status === 204 ? null : res.json();
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function gridPos(index, cols = 5) {
  const row = Math.floor(index / cols);
  const col = index % cols;
  const x = 10 + col * (80 / Math.max(cols - 1, 1));
  const y = Math.min(15 + row * 22, 92);
  return { x, y };
}

async function loadEquipments() {
  allEquipments = await fetchJson("/api/equipments");
  updateAlertsNavBadge();
  applyFilterSort();
}

function updateAlertsNavBadge() {
  const total = allEquipments.reduce((sum, eq) => sum + eq.overdue_count, 0);
  const badge = document.getElementById("alertsNavBadge");
  badge.innerHTML = total > 0 ? `<span class="nav-badge">${total}</span>` : "";
}

function applyFilterSort() {
  const q = document.getElementById("equipmentSearch").value.trim().toLowerCase();
  const sortBy = document.getElementById("equipmentSort").value;
  let list = allEquipments.filter((eq) => eq.name.toLowerCase().includes(q));
  if (sortBy === "overdue") {
    list = [...list].sort((a, b) => b.overdue_count - a.overdue_count || a.id - b.id);
  } else if (sortBy === "name") {
    list = [...list].sort((a, b) => a.name.localeCompare(b.name, "ko") || a.id - b.id);
  } else {
    list = [...list].sort((a, b) => a.id - b.id);
  }
  document.getElementById("noResultsMsg").classList.toggle("d-none", list.length > 0);
  renderGrid(list, sortBy);
}

function renderGrid(equipments, sortBy) {
  const grid = document.getElementById("equipmentGrid");
  grid.innerHTML = equipments.map(equipmentCardHtml).join("") +
    '<div id="alignGuideV" class="align-guide align-guide-v d-none"></div>' +
    '<div id="alignGuideH" class="align-guide align-guide-h d-none"></div>';
  equipments.forEach((eq, idx) => {
    const card = grid.querySelector(`[data-equipment-id="${eq.id}"]`);
    let x, y;
    if (sortBy === "custom") {
      x = eq.pos_x;
      y = eq.pos_y;
    } else {
      const p = gridPos(idx);
      x = p.x;
      y = p.y;
    }
    card.style.left = `${x}%`;
    card.style.top = `${y}%`;

    card.querySelector(".edit-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openEquipmentEditModal(eq);
    });
    card.querySelector(".delete-unit-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`"${eq.name}" 설비를 삭제할까요? 소속 유닛/부품도 함께 휴지통으로 이동합니다. (휴지통에서 복원할 수 있습니다)`)) return;
      await fetchJson(`/api/equipments/${eq.id}`, { method: "DELETE" });
      loadEquipments();
    });
    card.addEventListener("click", (e) => {
      if (editMode) return;
      if (e.target.closest(".unit-edit-actions")) return;
      window.location.href = `/equipment/${eq.id}`;
    });

    makeDraggable(card, eq);
  });
}

function makeDraggable(card, eq) {
  card.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    e.preventDefault();

    const canvas = document.getElementById("equipmentGrid");
    const rect = canvas.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = (parseFloat(card.style.left) / 100) * rect.width;
    const startTop = (parseFloat(card.style.top) / 100) * rect.height;
    let moved = false;

    const SNAP_PX = 8;
    const otherPositions = Array.from(canvas.querySelectorAll(".equipment-card"))
      .filter((c) => c !== card)
      .map((c) => ({
        x: (parseFloat(c.style.left) / 100) * rect.width,
        y: (parseFloat(c.style.top) / 100) * rect.height,
      }));
    const guideV = document.getElementById("alignGuideV");
    const guideH = document.getElementById("alignGuideH");

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
      let px = Math.min(Math.max(startLeft + dx, rect.width * 0.04), rect.width * 0.96);
      let py = Math.min(Math.max(startTop + dy, rect.height * 0.04), rect.height * 0.96);

      let snappedX = null;
      let snappedY = null;
      let bestDx = SNAP_PX;
      let bestDy = SNAP_PX;
      for (const pos of otherPositions) {
        const dxAbs = Math.abs(pos.x - px);
        if (dxAbs <= bestDx) {
          bestDx = dxAbs;
          snappedX = pos.x;
        }
        const dyAbs = Math.abs(pos.y - py);
        if (dyAbs <= bestDy) {
          bestDy = dyAbs;
          snappedY = pos.y;
        }
      }
      if (snappedX !== null) px = snappedX;
      if (snappedY !== null) py = snappedY;

      guideV.classList.toggle("d-none", snappedX === null);
      if (snappedX !== null) guideV.style.left = `${(snappedX / rect.width) * 100}%`;
      guideH.classList.toggle("d-none", snappedY === null);
      if (snappedY !== null) guideH.style.top = `${(snappedY / rect.height) * 100}%`;

      card.style.left = `${(px / rect.width) * 100}%`;
      card.style.top = `${(py / rect.height) * 100}%`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      guideV.classList.add("d-none");
      guideH.classList.add("d-none");
      if (moved) {
        const pos_x = parseFloat(card.style.left);
        const pos_y = parseFloat(card.style.top);
        eq.pos_x = pos_x;
        eq.pos_y = pos_y;
        await fetchJson(`/api/equipments/${eq.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pos_x, pos_y }),
        });
        const sortSelect = document.getElementById("equipmentSort");
        if (sortSelect.value !== "custom") sortSelect.value = "custom";
        applyFilterSort();
      }
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function equipmentCardHtml(eq) {
  return `
    <div class="equipment-card ${editMode ? "edit-mode" : ""}" data-equipment-id="${eq.id}">
      <span class="unit-status-dot dot-${eq.overall_status}"></span>
      <div class="unit-icon-wrap">
        <span class="unit-icon">${eq.icon}</span>
        ${eq.soon_count > 0 ? `<span class="soon-badge" title="교체 임박 부품 ${eq.soon_count}건">${eq.soon_count}</span>` : ""}
        ${eq.overdue_count > 0 ? `<span class="overdue-badge" title="교체 필요 부품 ${eq.overdue_count}건">${eq.overdue_count}</span>` : ""}
      </div>
      <div class="unit-name">${escapeHtml(eq.name)}</div>
      <div class="unit-part-count">${eq.unit_count}개 유닛</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="편집"><i class="bi bi-pencil"></i></button>
        <button class="delete-unit-btn" title="삭제"><i class="bi bi-trash"></i></button>
      </div>
    </div>`;
}

function openEquipmentEditModal(eq) {
  document.getElementById("equipmentEditTitle").textContent = eq ? "설비 편집" : "설비 추가";
  document.getElementById("equipmentEditId").value = eq ? eq.id : "";
  document.getElementById("equipmentEditName").value = eq ? eq.name : "";
  const icon = eq ? eq.icon : "🏭";
  document.getElementById("equipmentEditIcon").value = icon;
  renderIconPicker("equipmentIconPicker", "equipmentEditIcon", icon);
  equipmentEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  equipmentEditModal = new bootstrap.Modal(document.getElementById("equipmentEditModal"));
  const changePasswordModal = new bootstrap.Modal(document.getElementById("changePasswordModal"));

  tick();
  setInterval(tick, 1000);
  loadEquipments();

  document.getElementById("changePasswordBtn").addEventListener("click", () => {
    document.getElementById("changePasswordForm").reset();
    changePasswordModal.show();
  });

  document.getElementById("backupBtn").addEventListener("click", async () => {
    try {
      const result = await fetchJson("/api/backup", { method: "POST" });
      alert(`백업이 저장되었습니다.\n${result.path}`);
    } catch (err) {
      alert(err.message);
    }
  });
  document.getElementById("changePasswordForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await fetchJson("/api/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: document.getElementById("currentPasswordInput").value,
          new_password: document.getElementById("newPasswordInput").value,
        }),
      });
      changePasswordModal.hide();
      alert("비밀번호가 변경되었습니다.");
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("editModeBtn").addEventListener("click", (e) => {
    editMode = !editMode;
    e.currentTarget.classList.toggle("btn-outline-light", !editMode);
    e.currentTarget.classList.toggle("btn-warning", editMode);
    loadEquipments();
  });

  document.getElementById("addEquipmentBtn").addEventListener("click", () => openEquipmentEditModal(null));
  document.getElementById("statsBtn").addEventListener("click", () => {
    window.open("/stats", "_blank", "noopener,noreferrer");
  });

  document.getElementById("equipmentSearch").addEventListener("input", applyFilterSort);
  document.getElementById("equipmentSort").addEventListener("change", applyFilterSort);

  document.getElementById("equipmentEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("equipmentEditId").value;
    const payload = {
      name: document.getElementById("equipmentEditName").value.trim(),
      icon: document.getElementById("equipmentEditIcon").value.trim(),
    };
    try {
      if (id) {
        await fetchJson(`/api/equipments/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await fetchJson("/api/equipments", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      equipmentEditModal.hide();
      loadEquipments();
    } catch (err) {
      alert(err.message);
    }
  });
});

// ── 현황 메일 발송 (사내 메일 API) ─────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  const mailModalEl = document.getElementById("mailModal");
  if (!mailModalEl) return;
  const mailModal = new bootstrap.Modal(mailModalEl);
  const statusMsg = document.getElementById("mailStatusMsg");

  async function loadRecipients() {
    const list = await fetchJson("/api/mail/recipients");
    const ul = document.getElementById("mailRecipientList");
    if (list.length === 0) {
      ul.innerHTML = '<li class="list-group-item text-muted small">등록된 수신자가 없습니다.</li>';
      return;
    }
    ul.innerHTML = list
      .map(
        (r) => `
      <li class="list-group-item d-flex justify-content-between align-items-center py-1">
        <span>${r.email_id}@samsung.com</span>
        <button class="btn btn-sm btn-outline-danger py-0 del-recipient-btn" data-id="${r.id}">삭제</button>
      </li>`
      )
      .join("");
    ul.querySelectorAll(".del-recipient-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await fetchJson(`/api/mail/recipients/${btn.dataset.id}`, { method: "DELETE" });
        loadRecipients();
      });
    });
  }

  async function loadSchedule() {
    const data = await fetchJson("/api/mail/schedule");
    document.getElementById("mailScheduleTime").value = data.time || "";
  }

  document.getElementById("mailBtn").addEventListener("click", () => {
    statusMsg.textContent = "";
    loadRecipients();
    loadSchedule();
    mailModal.show();
  });

  document.getElementById("addRecipientBtn").addEventListener("click", async () => {
    const input = document.getElementById("mailRecipientInput");
    if (!input.value.trim()) return;
    try {
      await fetchJson("/api/mail/recipients", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email_id: input.value.trim() }),
      });
      input.value = "";
      loadRecipients();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("saveScheduleBtn").addEventListener("click", async () => {
    const t = document.getElementById("mailScheduleTime").value;
    await fetchJson("/api/mail/schedule", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ time: t }),
    });
    statusMsg.textContent = t ? `매일 ${t}에 자동 발송됩니다.` : "자동 발송이 해제되었습니다.";
  });

  document.getElementById("clearScheduleBtn").addEventListener("click", async () => {
    document.getElementById("mailScheduleTime").value = "";
    await fetchJson("/api/mail/schedule", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ time: "" }),
    });
    statusMsg.textContent = "자동 발송이 해제되었습니다.";
  });

  document.getElementById("sendMailNowBtn").addEventListener("click", async () => {
    const btn = document.getElementById("sendMailNowBtn");
    btn.disabled = true;
    statusMsg.textContent = "발송 중...";
    try {
      const res = await fetch("/api/mail/send", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      statusMsg.textContent = data.message || "오류가 발생했습니다";
    } catch (err) {
      statusMsg.textContent = "발송 요청 실패: " + err.message;
    } finally {
      btn.disabled = false;
    }
  });
});
</script>
</body>
</html>
"""


EQUIPMENT_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <a href="/" class="btn btn-sm btn-outline-light"><i class="bi bi-arrow-left"></i> 대시보드</a>
    <i class="bi bi-cpu"></i>
    <h1 id="equipmentPageTitle">설비</h1>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span id="clock" class="clock"></span>
    <button id="editModeBtn" class="btn btn-sm btn-outline-light">
      <i class="bi bi-pencil-square"></i> 설비 구성 편집
    </button>
  </div>
</header>

<main class="container-fluid py-4">

  <div class="d-flex flex-wrap gap-2 mb-3">
    <div class="legend">
      <span class="legend-item"><span class="dot dot-ok"></span> 정상</span>
      <span class="legend-item"><span class="dot dot-soon"></span> 교체 임박</span>
      <span class="legend-item"><span class="dot dot-overdue"></span> 교체 필요</span>
      <span class="legend-item"><span class="dot dot-unknown"></span> 미기록 / 부품 없음</span>
    </div>
    <div class="legend">
      <span class="legend-item"><i class="bi bi-geo-alt"></i> 위치: <span id="equipmentLocationView">-</span></span>
      <span class="legend-item"><i class="bi bi-calendar-event"></i> SETUP: <span id="equipmentSetupDateView">-</span></span>
      <span class="legend-item"><i class="bi bi-hourglass-split"></i> 가동: <span id="equipmentRuntimeView">-</span></span>
      <button type="button" id="editEquipmentInfoBtn" class="btn btn-sm btn-link p-0 text-decoration-none legend-edit-btn">
        <i class="bi bi-pencil"></i>
      </button>
    </div>
  </div>

  <div id="equipment" class="equipment-frame">
    <div class="equipment-label" id="equipmentLabel">유닛을 클릭하면 상세 페이지로 이동 · 편집 모드에서 드래그로 배치/크기 변경</div>
    <div id="canvas" class="equipment-canvas"></div>
    <div class="canvas-actions">
      <button id="addUnitBtn" class="btn btn-sm btn-outline-primary d-none">
        <i class="bi bi-plus-lg"></i> 유닛 추가
      </button>
      <button id="pasteUnitBtn" class="btn btn-sm btn-outline-secondary d-none">
        <i class="bi bi-clipboard-check"></i> 붙여넣기
      </button>
    </div>
  </div>

  <div class="notes-section">
    <div class="d-flex justify-content-between align-items-center mb-2">
      <h6 class="mb-0"><i class="bi bi-journal-text"></i> 설비 메모 / 구매처 링크</h6>
      <div class="d-flex align-items-center gap-2">
        <span id="notesSavedAt" class="text-muted small"></span>
        <button id="editNotesBtn" class="btn btn-sm btn-outline-secondary">
          <i class="bi bi-pencil"></i> 편집
        </button>
        <button id="saveNotesBtn" class="btn btn-sm btn-primary d-none">저장</button>
        <button id="cancelNotesBtn" class="btn btn-sm btn-outline-secondary d-none">취소</button>
      </div>
    </div>
    <div id="notesView" class="notes-view"></div>
    <div id="notesEdit" class="form-control d-none rich-edit" contenteditable="true" style="min-height: 160px;"
      data-placeholder="설비 정보, 부품 구매 사이트 URL, 엑셀 표 등을 자유롭게 기록하세요. (엑셀 표를 붙여넣으면 서식이 유지되고, http://, https://로 시작하는 링크는 자동으로 클릭 가능한 링크가 됩니다)"></div>
  </div>

</main>

<!-- 유닛 추가/편집 모달 -->
<div class="modal fade" id="unitEditModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="unitEditTitle">유닛 추가</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <form id="unitEditForm">
          <input type="hidden" id="unitEditId">
          <div class="mb-2">
            <label class="form-label">유닛 이름</label>
            <input type="text" class="form-control" id="unitEditName" required>
          </div>
          <div class="row g-2">
            <div class="col-6">
              <label class="form-label">아이콘 (이모지)</label>
              <input type="text" class="form-control" id="unitEditIcon" placeholder="⚙️">
            </div>
            <div class="col-6">
              <label class="form-label">색상</label>
              <input type="color" class="form-control form-control-color w-100" id="unitEditColor" value="#1a3a5c">
            </div>
          </div>
          <div id="unitIconPicker" class="icon-picker"></div>
          <button type="submit" class="btn btn-primary w-100 mt-3">저장</button>
        </form>
      </div>
    </div>
  </div>
</div>

<!-- 설비 위치/SETUP 일자 편집 모달 -->
<div class="modal fade" id="equipmentInfoModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">설비 정보 편집</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <form id="equipmentInfoForm">
          <div class="mb-2">
            <label class="form-label">설비 위치</label>
            <input type="text" class="form-control" id="equipmentLocationInput" placeholder="예: 2공장 3층 A라인">
          </div>
          <div class="mb-2">
            <label class="form-label">SETUP 일자</label>
            <input type="date" class="form-control" id="equipmentSetupDateInput">
          </div>
          <button type="submit" class="btn btn-primary w-100 mt-2">저장</button>
        </form>
      </div>
    </div>
  </div>
</div>

<script>const EQUIPMENT_ID = __EQUIPMENT_ID__;</script>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
let editMode = false;
let unitEditModal, equipmentInfoModal;
let currentEquipmentData = null;

const ICON_CHOICES = [
  "⚙️", "🔧", "🔩", "🛠️", "🪛", "🔨", "📦", "🖥️",
  "🖨️", "💻", "📡", "🎛️", "🚨", "💡", "🔌", "⚡",
  "🔋", "🌡️", "💧", "🧪", "🧯", "🧰", "🏭", "⚗️",
  "🌀", "🗜️", "🧲", "📊", "🛞", "🚿", "🔥", "❄️",
];

function renderIconPicker(containerId, inputId, current) {
  const container = document.getElementById(containerId);
  const input = document.getElementById(inputId);
  container.innerHTML = ICON_CHOICES.map(
    (ic) => `<button type="button" class="icon-choice ${ic === current ? "selected" : ""}" data-icon="${ic}">${ic}</button>`
  ).join("");
  container.classList.add("d-none");
  container.querySelectorAll(".icon-choice").forEach((btn) => {
    btn.addEventListener("click", () => {
      input.value = btn.dataset.icon;
      container.querySelectorAll(".icon-choice").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      container.classList.add("d-none");
    });
  });
  if (!container.dataset.toggleBound) {
    container.dataset.toggleBound = "1";
    input.addEventListener("click", () => {
      container.classList.toggle("d-none");
    });
    document.addEventListener("click", (e) => {
      if (!container.contains(e.target) && e.target !== input) {
        container.classList.add("d-none");
      }
    });
  }
}

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

// ── 리치 메모/노트: 엑셀 표 붙여넣기 시 서식(표 구조) 유지 ─────────────────
const RICH_ALLOWED_TAGS = {
  TABLE: [], THEAD: [], TBODY: [], TFOOT: [], TR: [], COL: [], COLGROUP: [], CAPTION: [],
  TH: ["colspan", "rowspan"], TD: ["colspan", "rowspan"],
  B: [], STRONG: [], I: [], EM: [], U: [], BR: [], P: [], DIV: [], SPAN: [],
  UL: [], OL: [], LI: [], A: ["href"],
};
const RICH_STRIP_TAGS = new Set([
  "SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "LINK", "META", "SVG",
  "FORM", "IMG", "INPUT", "BUTTON", "TEXTAREA", "SELECT", "VIDEO", "AUDIO", "SOURCE",
]);

function sanitizeRichNode(node) {
  Array.from(node.childNodes).forEach((child) => {
    if (child.nodeType === Node.COMMENT_NODE) {
      child.remove();
      return;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) return;
    const tag = child.tagName;
    if (RICH_STRIP_TAGS.has(tag)) {
      child.remove();
      return;
    }
    const allowed = RICH_ALLOWED_TAGS[tag];
    if (!allowed) {
      sanitizeRichNode(child);
      while (child.firstChild) node.insertBefore(child.firstChild, child);
      child.remove();
      return;
    }
    Array.from(child.attributes).forEach((attr) => {
      if (!allowed.includes(attr.name)) child.removeAttribute(attr.name);
    });
    if (tag === "A") {
      const href = child.getAttribute("href") || "";
      if (!/^https?:\/\//i.test(href)) {
        child.removeAttribute("href");
      } else {
        child.setAttribute("target", "_blank");
        child.setAttribute("rel", "noopener noreferrer");
      }
    }
    sanitizeRichNode(child);
  });
}

function sanitizeRichHtml(rawHtml) {
  const container = document.createElement("div");
  container.innerHTML = rawHtml || "";
  sanitizeRichNode(container);
  return container.innerHTML;
}

function linkifyRichHtml(rawHtml) {
  const container = document.createElement("div");
  container.innerHTML = rawHtml || "";
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const targets = [];
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement && node.parentElement.closest("a")) continue;
    if (/https?:\/\//.test(node.nodeValue)) targets.push(node);
  }
  targets.forEach((textNode) => {
    const frag = document.createDocumentFragment();
    textNode.nodeValue.split(/(https?:\/\/[^\s<]+)/g).forEach((part) => {
      if (/^https?:\/\//.test(part)) {
        const a = document.createElement("a");
        a.href = part;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = part;
        frag.appendChild(a);
      } else if (part) {
        frag.appendChild(document.createTextNode(part));
      }
    });
    textNode.parentNode.replaceChild(frag, textNode);
  });
  return container.innerHTML;
}

function isRichContentEmpty(html) {
  const container = document.createElement("div");
  container.innerHTML = html || "";
  return container.textContent.trim() === "";
}

function attachRichPasteHandler(el) {
  if (!el || el.dataset.richPasteBound) return;
  el.dataset.richPasteBound = "1";
  el.addEventListener("paste", (e) => {
    e.preventDefault();
    const html = e.clipboardData.getData("text/html");
    const text = e.clipboardData.getData("text/plain");
    if (html) {
      document.execCommand("insertHTML", false, sanitizeRichHtml(html));
    } else if (text) {
      document.execCommand("insertText", false, text);
    }
  });
}

// ── 표 편집 툴바: 붙여넣은 표 안을 클릭하면 행/열 추가·삭제 버튼 표시 ───────
let tableEditToolbar = null;
let tableEditActiveCell = null;

function ensureTableEditToolbar() {
  if (tableEditToolbar) return tableEditToolbar;
  const bar = document.createElement("div");
  bar.className = "table-edit-toolbar d-none";
  bar.innerHTML = `
    <button type="button" data-action="add-row" title="아래에 행 추가"><i class="bi bi-plus-lg"></i>행</button>
    <button type="button" data-action="del-row" title="이 행 삭제"><i class="bi bi-dash-lg"></i>행</button>
    <button type="button" data-action="add-col" title="오른쪽에 열 추가"><i class="bi bi-plus-lg"></i>열</button>
    <button type="button" data-action="del-col" title="이 열 삭제"><i class="bi bi-dash-lg"></i>열</button>
  `;
  document.body.appendChild(bar);
  bar.addEventListener("mousedown", (e) => e.preventDefault());
  bar.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn || !tableEditActiveCell || !document.body.contains(tableEditActiveCell)) return;
    const actions = {
      "add-row": insertTableRow, "del-row": deleteTableRow,
      "add-col": insertTableColumn, "del-col": deleteTableColumn,
    };
    actions[btn.dataset.action](tableEditActiveCell);
  });
  tableEditToolbar = bar;
  return bar;
}

function showTableEditToolbar(cell) {
  const bar = ensureTableEditToolbar();
  tableEditActiveCell = cell;
  const rect = cell.getBoundingClientRect();
  bar.classList.remove("d-none");
  bar.style.top = `${Math.max(rect.top - bar.offsetHeight - 4, 4)}px`;
  bar.style.left = `${rect.left}px`;
}

function hideTableEditToolbar() {
  if (tableEditToolbar) tableEditToolbar.classList.add("d-none");
  tableEditActiveCell = null;
}

function cellColumnIndex(cell) {
  return Array.from(cell.parentElement.children).indexOf(cell);
}

function insertTableRow(cell) {
  const tr = cell.closest("tr");
  if (!tr) return;
  const newRow = document.createElement("tr");
  Array.from(tr.children).forEach(() => newRow.appendChild(document.createElement("td")));
  tr.after(newRow);
}

function deleteTableRow(cell) {
  const tr = cell.closest("tr");
  const table = cell.closest("table");
  if (!tr || !table) return;
  tr.remove();
  if (!table.querySelector("tr")) table.remove();
  hideTableEditToolbar();
}

function insertTableColumn(cell) {
  const table = cell.closest("table");
  if (!table) return;
  const colIndex = cellColumnIndex(cell);
  table.querySelectorAll("tr").forEach((row) => {
    const rowCell = row.children[colIndex];
    const newCell = document.createElement(rowCell && rowCell.tagName === "TH" ? "th" : "td");
    if (rowCell) rowCell.after(newCell);
    else row.appendChild(newCell);
  });
}

function deleteTableColumn(cell) {
  const table = cell.closest("table");
  if (!table) return;
  const colIndex = cellColumnIndex(cell);
  table.querySelectorAll("tr").forEach((row) => {
    const rowCell = row.children[colIndex];
    if (rowCell) rowCell.remove();
  });
  if (!table.querySelector("td, th")) table.remove();
  hideTableEditToolbar();
}

function attachTableEditToolbar(el) {
  if (!el || el.dataset.tableToolbarBound) return;
  el.dataset.tableToolbarBound = "1";
  const check = () => {
    const sel = window.getSelection();
    let node = sel.rangeCount ? sel.anchorNode : null;
    if (node && node.nodeType === Node.TEXT_NODE) node = node.parentElement;
    const cell = node && node.closest ? node.closest("td, th") : null;
    if (cell && el.contains(cell)) {
      showTableEditToolbar(cell);
    } else {
      hideTableEditToolbar();
    }
  };
  el.addEventListener("keyup", check);
  el.addEventListener("mouseup", check);
  el.addEventListener("focus", check);
  el.addEventListener("blur", () => {
    setTimeout(() => {
      if (!tableEditToolbar || !tableEditToolbar.matches(":hover")) hideTableEditToolbar();
    }, 150);
  });
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "요청 처리 중 오류가 발생했습니다");
  }
  return res.status === 204 ? null : res.json();
}

async function loadEquipmentHeader() {
  try {
    const equipment = await fetchJson(`/api/equipments/${EQUIPMENT_ID}`);
    currentEquipmentData = equipment;
    document.getElementById("equipmentPageTitle").textContent = equipment.name;
    document.title = `${equipment.name} - 설비 부품 교체 관리 시스템`;
    renderEquipmentInfo(equipment);
  } catch (err) {
    alert("설비 정보를 불러올 수 없습니다.");
    window.location.href = "/";
  }
}

function renderEquipmentInfo(equipment) {
  document.getElementById("equipmentLocationView").textContent = equipment.location || "미입력";
  document.getElementById("equipmentSetupDateView").textContent = equipment.setup_date || "미입력";
  document.getElementById("equipmentRuntimeView").textContent = equipment.runtime_display || "-";
}

async function loadUnits() {
  const units = await fetchJson(`/api/equipments/${EQUIPMENT_ID}/units`);
  renderCanvas(units);
}

function renderCanvas(units) {
  const canvas = document.getElementById("canvas");
  canvas.innerHTML = units.map(unitCardHtml).join("");
  units.forEach((u) => {
    const card = canvas.querySelector(`[data-unit-id="${u.id}"]`);
    card.style.left = `${u.pos_x}%`;
    card.style.top = `${u.pos_y}%`;
    card.style.width = `${u.width}px`;
    card.style.height = `${u.height}px`;

    card.querySelector(".edit-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openUnitEditModal(u);
    });
    card.querySelector(".delete-unit-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`"${u.name}" 유닛을 삭제할까요? 등록된 부품도 함께 휴지통으로 이동합니다. (휴지통에서 복원할 수 있습니다)`)) return;
      await fetchJson(`/api/units/${u.id}`, { method: "DELETE" });
      loadUnits();
    });

    card.querySelector(".copy-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      copyUnit(u);
    });

    makeDraggable(card, u);
    makeResizable(card, u);
    makeResizableHorizontal(card, u);
  });
}

const MIN_UNIT_WIDTH = 90;
const MIN_UNIT_HEIGHT = 80;
const MAX_UNIT_WIDTH = 320;
const MAX_UNIT_HEIGHT = 260;

function makeResizable(card, unit) {
  const handle = card.querySelector(".resize-handle");
  if (!handle) return;

  handle.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    e.preventDefault();
    e.stopPropagation();

    const startX = e.clientX;
    const startY = e.clientY;
    const startWidth = card.offsetWidth;
    const startHeight = card.offsetHeight;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      const w = Math.min(Math.max(startWidth + dx, MIN_UNIT_WIDTH), MAX_UNIT_WIDTH);
      const h = Math.min(Math.max(startHeight + dy, MIN_UNIT_HEIGHT), MAX_UNIT_HEIGHT);
      card.style.width = `${w}px`;
      card.style.height = `${h}px`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      const width = card.offsetWidth;
      const height = card.offsetHeight;
      unit.width = width;
      unit.height = height;
      await fetchJson(`/api/units/${unit.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width, height }),
      });
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function makeResizableHorizontal(card, unit) {
  const handle = card.querySelector(".resize-handle-h");
  if (!handle) return;

  handle.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    e.preventDefault();
    e.stopPropagation();

    const startX = e.clientX;
    const startWidth = card.offsetWidth;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const w = Math.min(Math.max(startWidth + dx, MIN_UNIT_WIDTH), MAX_UNIT_WIDTH);
      card.style.width = `${w}px`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      const width = card.offsetWidth;
      unit.width = width;
      await fetchJson(`/api/units/${unit.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width }),
      });
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function makeDraggable(card, unit) {
  card.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    e.preventDefault();

    const canvas = document.getElementById("canvas");
    const rect = canvas.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = (parseFloat(card.style.left) / 100) * rect.width;
    const startTop = (parseFloat(card.style.top) / 100) * rect.height;
    let moved = false;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
      const px = Math.min(Math.max(startLeft + dx, rect.width * 0.04), rect.width * 0.96);
      const py = Math.min(Math.max(startTop + dy, rect.height * 0.04), rect.height * 0.96);
      card.style.left = `${(px / rect.width) * 100}%`;
      card.style.top = `${(py / rect.height) * 100}%`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      if (moved) {
        const pos_x = parseFloat(card.style.left);
        const pos_y = parseFloat(card.style.top);
        unit.pos_x = pos_x;
        unit.pos_y = pos_y;
        await fetchJson(`/api/units/${unit.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pos_x, pos_y }),
        });
      } else {
        window.location.href = `/unit/${unit.id}`;
      }
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  card.addEventListener("click", (e) => {
    if (editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    window.location.href = `/unit/${unit.id}`;
  });
}

function unitCardHtml(u) {
  return `
    <div class="unit-card ${editMode ? "edit-mode" : ""}" data-unit-id="${u.id}" style="--uc:${u.color}">
      <span class="unit-status-dot dot-${u.overall_status}"></span>
      <div class="unit-icon-wrap">
        <span class="unit-icon">${u.icon}</span>
        ${u.soon_count > 0 ? `<span class="soon-badge" title="교체 임박 부품 ${u.soon_count}건">${u.soon_count}</span>` : ""}
        ${u.overdue_count > 0 ? `<span class="overdue-badge" title="교체 필요 부품 ${u.overdue_count}건">${u.overdue_count}</span>` : ""}
      </div>
      <div class="unit-name">${escapeHtml(u.name)}</div>
      <div class="unit-part-count">${u.part_count}개 부품 등록</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="편집"><i class="bi bi-pencil"></i></button>
        <button class="copy-unit-btn" title="복사"><i class="bi bi-copy"></i></button>
        <button class="delete-unit-btn" title="삭제"><i class="bi bi-trash"></i></button>
      </div>
      <div class="resize-handle" title="크기 조절 (가로+세로)"></div>
      <div class="resize-handle-h" title="가로 크기 조절"></div>
    </div>`;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

let lastNotesContent = "";

async function loadNotes() {
  const data = await fetchJson(`/api/equipments/${EQUIPMENT_ID}/notes`);
  lastNotesContent = data.content || "";
  renderNotesView(lastNotesContent);
  document.getElementById("notesEdit").innerHTML = lastNotesContent;
  document.getElementById("notesSavedAt").textContent = data.updated_at
    ? `최종 수정: ${data.updated_at}`
    : "";
}

function renderNotesView(content) {
  const view = document.getElementById("notesView");
  if (isRichContentEmpty(content)) {
    view.innerHTML = "";
    view.classList.add("is-empty");
  } else {
    view.classList.remove("is-empty");
    view.innerHTML = linkifyRichHtml(content);
  }
}

function setNotesEditing(editing) {
  document.getElementById("notesView").classList.toggle("d-none", editing);
  document.getElementById("notesEdit").classList.toggle("d-none", !editing);
  document.getElementById("editNotesBtn").classList.toggle("d-none", editing);
  document.getElementById("saveNotesBtn").classList.toggle("d-none", !editing);
  document.getElementById("cancelNotesBtn").classList.toggle("d-none", !editing);
  if (editing) document.getElementById("notesEdit").focus();
}

const UNIT_CLIPBOARD_KEY = "unitClipboard";

async function copyUnit(unit) {
  const parts = await fetchJson(`/api/units/${unit.id}/parts`);
  const clipboard = {
    name: unit.name,
    icon: unit.icon,
    color: unit.color,
    width: unit.width,
    height: unit.height,
    parts: parts.map((p) => ({
      name: p.name,
      spec: p.spec,
      cycle_days: p.cycle_days,
      cycle_unit: p.cycle_unit,
      cost: p.cost,
      note: p.note,
      icon: p.icon,
    })),
  };
  localStorage.setItem(UNIT_CLIPBOARD_KEY, JSON.stringify(clipboard));
  updatePasteButton();
  alert(`"${unit.name}" 유닛을 복사했습니다. (부품 ${parts.length}개 포함)\n"붙여넣기" 버튼으로 동일한 유닛을 만들 수 있습니다.`);
}

function getUnitClipboard() {
  const raw = localStorage.getItem(UNIT_CLIPBOARD_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function updatePasteButton() {
  const clipboard = getUnitClipboard();
  const btn = document.getElementById("pasteUnitBtn");
  btn.classList.toggle("d-none", !editMode || !clipboard);
  if (clipboard) btn.title = `"${clipboard.name}" 붙여넣기 (부품 ${clipboard.parts.length}개 포함)`;
}

async function pasteUnit() {
  const clipboard = getUnitClipboard();
  if (!clipboard) {
    alert("복사된 유닛이 없습니다. 먼저 유닛의 복사 아이콘을 눌러주세요.");
    return;
  }
  const newUnit = await fetchJson(`/api/equipments/${EQUIPMENT_ID}/units`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: `${clipboard.name} 복사본`,
      icon: clipboard.icon,
      color: clipboard.color,
      width: clipboard.width,
      height: clipboard.height,
    }),
  });
  for (const part of clipboard.parts) {
    await fetchJson(`/api/units/${newUnit.id}/parts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(part),
    });
  }
  loadUnits();
}

function openUnitEditModal(unit) {
  document.getElementById("unitEditTitle").textContent = unit ? "유닛 편집" : "유닛 추가";
  document.getElementById("unitEditId").value = unit ? unit.id : "";
  document.getElementById("unitEditName").value = unit ? unit.name : "";
  const icon = unit ? unit.icon : "⚙️";
  document.getElementById("unitEditIcon").value = icon;
  document.getElementById("unitEditColor").value = unit ? unit.color : "#1a3a5c";
  renderIconPicker("unitIconPicker", "unitEditIcon", icon);
  unitEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  unitEditModal = new bootstrap.Modal(document.getElementById("unitEditModal"));
  equipmentInfoModal = new bootstrap.Modal(document.getElementById("equipmentInfoModal"));

  tick();
  setInterval(tick, 1000);
  loadEquipmentHeader();
  loadUnits();
  loadNotes();

  document.getElementById("editEquipmentInfoBtn").addEventListener("click", () => {
    document.getElementById("equipmentLocationInput").value = currentEquipmentData?.location || "";
    document.getElementById("equipmentSetupDateInput").value = currentEquipmentData?.setup_date || "";
    equipmentInfoModal.show();
  });

  document.getElementById("equipmentInfoForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      location: document.getElementById("equipmentLocationInput").value.trim(),
      setup_date: document.getElementById("equipmentSetupDateInput").value || null,
    };
    try {
      await fetchJson(`/api/equipments/${EQUIPMENT_ID}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      equipmentInfoModal.hide();
      loadEquipmentHeader();
    } catch (err) {
      alert(err.message);
    }
  });

  attachRichPasteHandler(document.getElementById("notesEdit"));
  attachTableEditToolbar(document.getElementById("notesEdit"));
  document.getElementById("editNotesBtn").addEventListener("click", () => setNotesEditing(true));
  document.getElementById("cancelNotesBtn").addEventListener("click", () => {
    document.getElementById("notesEdit").innerHTML = lastNotesContent;
    setNotesEditing(false);
  });
  document.getElementById("saveNotesBtn").addEventListener("click", async () => {
    const content = sanitizeRichHtml(document.getElementById("notesEdit").innerHTML);
    try {
      const data = await fetchJson(`/api/equipments/${EQUIPMENT_ID}/notes`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      lastNotesContent = data.content || "";
      renderNotesView(lastNotesContent);
      document.getElementById("notesSavedAt").textContent = data.updated_at
        ? `최종 수정: ${data.updated_at}`
        : "";
      setNotesEditing(false);
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("editModeBtn").addEventListener("click", (e) => {
    editMode = !editMode;
    e.currentTarget.classList.toggle("btn-outline-light", !editMode);
    e.currentTarget.classList.toggle("btn-warning", editMode);
    document.getElementById("addUnitBtn").classList.toggle("d-none", !editMode);
    updatePasteButton();
    loadUnits();
  });

  document.getElementById("addUnitBtn").addEventListener("click", () => openUnitEditModal(null));
  document.getElementById("pasteUnitBtn").addEventListener("click", pasteUnit);

  document.getElementById("unitEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("unitEditId").value;
    const payload = {
      name: document.getElementById("unitEditName").value.trim(),
      icon: document.getElementById("unitEditIcon").value.trim(),
      color: document.getElementById("unitEditColor").value,
    };
    try {
      if (id) {
        await fetchJson(`/api/units/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await fetchJson(`/api/equipments/${EQUIPMENT_ID}/units`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      unitEditModal.hide();
      loadUnits();
    } catch (err) {
      alert(err.message);
    }
  });
});
</script>
</body>
</html>
"""


UNIT_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>유닛 상세 - 설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <a href="/" id="backToEquipmentBtn" class="btn btn-sm btn-outline-light"><i class="bi bi-arrow-left"></i> 설비로</a>
    <a href="/" class="btn btn-sm btn-outline-light"><i class="bi bi-grid-3x3-gap"></i> 대시보드</a>
    <h1 id="unitPageTitle">유닛 상세</h1>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span id="clock" class="clock"></span>
    <button id="editModeBtn" class="btn btn-sm btn-outline-light">
      <i class="bi bi-pencil-square"></i> 부품 구성 편집
    </button>
  </div>
</header>

<main class="container-fluid py-4">

  <div class="legend mb-3">
    <span class="legend-item"><span class="dot dot-ok"></span> 정상</span>
    <span class="legend-item"><span class="dot dot-soon"></span> 교체 임박</span>
    <span class="legend-item"><span class="dot dot-overdue"></span> 교체 필요</span>
    <span class="legend-item"><span class="dot dot-unknown"></span> 미기록</span>
  </div>

  <div id="unitShape" class="equipment-frame unit-shape">
    <div class="equipment-label" id="unitLabel">부품을 클릭해서 교체일 관리 · 편집 모드에서 드래그로 배치/크기 변경</div>
    <div id="masterHint" class="master-hint d-none">
      <i class="bi bi-broadcast"></i> 기준 설비: 부품 구성을 완료한 뒤 "전체 설비에 적용" 버튼을 눌러야 나머지 설비에 반영됩니다.
    </div>
    <div class="unit-shape-header">
      <span class="unit-shape-icon" id="unitIcon">⚙️</span>
      <span class="unit-shape-name" id="unitName">유닛</span>
    </div>
    <div id="partsCanvas" class="equipment-canvas"></div>
    <div class="canvas-actions">
      <button id="addPartBtn" class="btn btn-sm btn-outline-primary d-none">
        <i class="bi bi-plus-lg"></i> 부품 추가
      </button>
      <button id="pastePartBtn" class="btn btn-sm btn-outline-secondary d-none">
        <i class="bi bi-clipboard-check"></i> 붙여넣기
      </button>
      <button id="applyPartsBtn" class="btn btn-sm btn-warning d-none">
        <i class="bi bi-cloud-arrow-up"></i> 전체 설비에 적용
      </button>
    </div>
  </div>

  <div class="notes-section">
    <div class="d-flex justify-content-between align-items-center mb-2">
      <h6 class="mb-0"><i class="bi bi-journal-text"></i> 유닛 메모 / 부품 정보 링크</h6>
      <div class="d-flex align-items-center gap-2">
        <span id="notesSavedAt" class="text-muted small"></span>
        <button id="editNotesBtn" class="btn btn-sm btn-outline-secondary">
          <i class="bi bi-pencil"></i> 편집
        </button>
        <button id="saveNotesBtn" class="btn btn-sm btn-primary d-none">저장</button>
        <button id="cancelNotesBtn" class="btn btn-sm btn-outline-secondary d-none">취소</button>
      </div>
    </div>
    <div id="notesView" class="notes-view"></div>
    <div id="notesEdit" class="form-control d-none rich-edit" contenteditable="true" style="min-height: 160px;"
      data-placeholder="부품 규격, 구매처 URL, 엑셀 표 등을 자유롭게 기록하세요. (엑셀 표를 붙여넣으면 서식이 유지되고, http://, https://로 시작하는 링크는 자동으로 클릭 가능한 링크가 됩니다)"></div>
  </div>

</main>

<!-- 부품 상세 모달 -->
<div class="modal fade" id="partDetailModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="partDetailTitle">부품</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <div id="partDetailBody"></div>
        <div class="d-flex gap-2 mt-3">
          <button id="partDetailReplaceBtn" class="btn btn-primary btn-sm flex-fill">
            <i class="bi bi-arrow-repeat"></i> 교체 기록 추가
          </button>
          <button id="partDetailHistoryBtn" class="btn btn-outline-secondary btn-sm flex-fill">
            <i class="bi bi-clock-history"></i> 이력 보기
          </button>
          <button id="partDetailDrawingBtn" class="btn btn-outline-secondary btn-sm flex-fill d-none">
            <i class="bi bi-image"></i> 도면
          </button>
        </div>
        <div class="part-memo-section mt-3">
          <div class="d-flex justify-content-between align-items-center mb-1">
            <div class="small text-muted"><i class="bi bi-journal-text"></i> 메모</div>
            <div class="d-flex align-items-center gap-1">
              <button id="editPartMemoBtn" class="btn btn-sm btn-outline-secondary py-0 px-1">
                <i class="bi bi-pencil"></i>
              </button>
              <button id="savePartMemoBtn" class="btn btn-sm btn-primary py-0 px-1 d-none">저장</button>
              <button id="cancelPartMemoBtn" class="btn btn-sm btn-outline-secondary py-0 px-1 d-none">취소</button>
            </div>
          </div>
          <div id="partDetailMemo" class="part-memo-view"></div>
          <div id="partDetailMemoEdit" class="form-control form-control-sm d-none rich-edit" contenteditable="true" style="min-height: 90px;"
            data-placeholder="부품 관련 메모를 자유롭게 기록하세요. (엑셀 표를 붙여넣으면 서식이 유지되고, http://, https://로 시작하는 링크는 자동으로 클릭 가능한 링크가 됩니다)"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- 교체 기록 모달 -->
<div class="modal fade" id="replaceModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">부품 교체 기록</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <form id="replaceForm">
          <div class="row g-2">
            <div class="col-6">
              <label class="form-label">교체일</label>
              <input type="date" class="form-control" id="replaceDate" required>
            </div>
            <div class="col-6">
              <label class="form-label">금액 (원)</label>
              <input type="number" class="form-control" id="replaceCost" value="0" min="0" step="100">
            </div>
          </div>
          <div class="mb-2 mt-2">
            <label class="form-label">비고</label>
            <input type="text" class="form-control" id="replaceNote" placeholder="교체 사유 / 작업자 등">
          </div>
          <button type="submit" class="btn btn-primary w-100">기록 저장</button>
        </form>
      </div>
    </div>
  </div>
</div>

<!-- 교체 이력 모달 -->
<div class="modal fade" id="historyModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">교체 이력</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <div id="historyList"></div>
      </div>
    </div>
  </div>
</div>

<!-- 도면 보기 모달 -->
<div class="modal fade" id="drawingModal" tabindex="-1">
  <div class="modal-dialog modal-lg">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="drawingModalTitle">도면</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body text-center">
        <img id="drawingModalImg" class="drawing-modal-img">
      </div>
    </div>
  </div>
</div>

<!-- 부품 추가/편집 모달 -->
<div class="modal fade" id="partEditModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="partEditTitle">부품 추가</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <form id="partEditForm">
          <input type="hidden" id="partEditId">
          <div class="mb-2">
            <label class="form-label">부품명</label>
            <input type="text" class="form-control" id="partEditName" placeholder="예: O-Ring" required>
          </div>
          <div class="row g-2">
            <div class="col-6">
              <label class="form-label">규격 / 모델명</label>
              <input type="text" class="form-control" id="partEditSpec">
            </div>
            <div class="col-6">
              <label class="form-label">아이콘 (이모지)</label>
              <input type="text" class="form-control" id="partEditIcon" placeholder="🔩">
            </div>
          </div>
          <div id="partIconPicker" class="icon-picker"></div>
          <div class="row g-2 mt-1">
            <div class="col-6">
              <label class="form-label">교체 주기</label>
              <div class="input-group">
                <input type="number" class="form-control" id="partEditCycle" value="90" min="1" required>
                <select class="form-select flex-grow-0 w-auto" id="partEditCycleUnit">
                  <option value="일">일</option>
                  <option value="년">년</option>
                </select>
              </div>
            </div>
            <div class="col-6" id="partEditLastDateWrap">
              <label class="form-label">최초 교체일 (선택)</label>
              <input type="date" class="form-control" id="partEditLastDate">
            </div>
          </div>
          <div class="row g-2 mt-1">
            <div class="col-6">
              <label class="form-label">금액 (원)</label>
              <input type="number" class="form-control" id="partEditCost" value="0" min="0" step="100">
            </div>
            <div class="col-6">
              <label class="form-label">비고</label>
              <input type="text" class="form-control" id="partEditNote">
            </div>
          </div>
          <div class="mb-2 mt-1">
            <label class="form-label">메모장</label>
            <div class="form-control rich-edit" id="partEditMemo" contenteditable="true" style="min-height: 90px;"
              data-placeholder="부품 관련 세부 정보를 자유롭게 기록하세요. (엑셀 표를 붙여넣으면 서식이 유지되고, http://, https://로 시작하는 링크는 자동으로 클릭 가능한 링크가 됩니다)"></div>
          </div>
          <div class="row g-2 mt-1">
            <div class="col-6">
              <label class="form-label">재고 수량</label>
              <input type="number" class="form-control" id="partEditStockQty" value="0" min="0" step="1">
            </div>
            <div class="col-6">
              <label class="form-label">리드타임 (일)</label>
              <input type="number" class="form-control" id="partEditLeadTime" min="0" step="1">
            </div>
          </div>
          <div class="row g-2 mt-1">
            <div class="col-6">
              <label class="form-label">구매처</label>
              <input type="text" class="form-control" id="partEditSupplier" placeholder="예: OO상사">
            </div>
            <div class="col-6">
              <label class="form-label">구매처 연락처</label>
              <input type="text" class="form-control" id="partEditSupplierContact" placeholder="예: 010-0000-0000">
            </div>
          </div>
          <div class="mb-2 mt-1">
            <div class="d-flex justify-content-between align-items-center">
              <label class="form-label mb-0">도면</label>
              <div class="d-flex align-items-center gap-2">
                <span id="partDrawingStatus" class="small text-muted"></span>
                <button type="button" id="partDrawingBtn" class="btn btn-sm btn-outline-secondary">
                  <i class="bi bi-image"></i> 도면 추가/변경
                </button>
              </div>
            </div>
            <div id="partDrawingArea" class="part-drawing-paste d-none" tabindex="0">
              <div id="partDrawingPlaceholder" class="part-drawing-placeholder">
                <i class="bi bi-clipboard"></i> 이 영역을 클릭한 후 이미지를 붙여넣으세요 (Ctrl+V)
              </div>
              <img id="partDrawingPreview" class="part-drawing-preview d-none">
            </div>
            <button type="button" id="partDrawingRemoveBtn" class="btn btn-sm btn-outline-danger d-none mt-2">
              <i class="bi bi-trash"></i> 도면 삭제
            </button>
          </div>
          <button type="submit" class="btn btn-primary w-100 mt-2">저장</button>
        </form>
      </div>
    </div>
  </div>
</div>

<script>const UNIT_ID = __UNIT_ID__;</script>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
let editMode = false;
let currentPartId = null;
let partDetailModal, replaceModal, historyModal, partEditModal, drawingModal;
let currentParts = [];
let currentEquipmentId = null;
let currentPartDrawingData = null;
const MASTER_EQUIPMENT_ID = 1;
const MAX_DRAWING_BYTES = 5 * 1024 * 1024;

const statusColor = { ok: "#22c55e", soon: "#f59e0b", overdue: "#ef4444", unknown: "#9ca3af" };
const statusBadge = { ok: "badge-ok", soon: "badge-soon", overdue: "badge-overdue", unknown: "badge-unknown" };
const statusLabel = { ok: "정상", soon: "교체 임박", overdue: "교체 필요", unknown: "미기록" };

const ICON_CHOICES = [
  "🔩", "⚙️", "🔧", "🛠️", "🪛", "🔨", "📦", "🖥️",
  "🖨️", "💻", "📡", "🎛️", "🚨", "💡", "🔌", "⚡",
  "🔋", "🌡️", "💧", "🧪", "🧯", "🧰", "🏭", "⚗️",
  "🌀", "🗜️", "🧲", "📊", "🛞", "🚿", "🔥", "❄️",
];

function renderIconPicker(containerId, inputId, current) {
  const container = document.getElementById(containerId);
  const input = document.getElementById(inputId);
  container.innerHTML = ICON_CHOICES.map(
    (ic) => `<button type="button" class="icon-choice ${ic === current ? "selected" : ""}" data-icon="${ic}">${ic}</button>`
  ).join("");
  container.classList.add("d-none");
  container.querySelectorAll(".icon-choice").forEach((btn) => {
    btn.addEventListener("click", () => {
      input.value = btn.dataset.icon;
      container.querySelectorAll(".icon-choice").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      container.classList.add("d-none");
    });
  });
  if (!container.dataset.toggleBound) {
    container.dataset.toggleBound = "1";
    input.addEventListener("click", () => {
      container.classList.toggle("d-none");
    });
    document.addEventListener("click", (e) => {
      if (!container.contains(e.target) && e.target !== input) {
        container.classList.add("d-none");
      }
    });
  }
}

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "요청 처리 중 오류가 발생했습니다");
  }
  return res.status === 204 ? null : res.json();
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

// ── 리치 메모/노트: 엑셀 표 붙여넣기 시 서식(표 구조) 유지 ─────────────────
const RICH_ALLOWED_TAGS = {
  TABLE: [], THEAD: [], TBODY: [], TFOOT: [], TR: [], COL: [], COLGROUP: [], CAPTION: [],
  TH: ["colspan", "rowspan"], TD: ["colspan", "rowspan"],
  B: [], STRONG: [], I: [], EM: [], U: [], BR: [], P: [], DIV: [], SPAN: [],
  UL: [], OL: [], LI: [], A: ["href"],
};
const RICH_STRIP_TAGS = new Set([
  "SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "LINK", "META", "SVG",
  "FORM", "IMG", "INPUT", "BUTTON", "TEXTAREA", "SELECT", "VIDEO", "AUDIO", "SOURCE",
]);

function sanitizeRichNode(node) {
  Array.from(node.childNodes).forEach((child) => {
    if (child.nodeType === Node.COMMENT_NODE) {
      child.remove();
      return;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) return;
    const tag = child.tagName;
    if (RICH_STRIP_TAGS.has(tag)) {
      child.remove();
      return;
    }
    const allowed = RICH_ALLOWED_TAGS[tag];
    if (!allowed) {
      sanitizeRichNode(child);
      while (child.firstChild) node.insertBefore(child.firstChild, child);
      child.remove();
      return;
    }
    Array.from(child.attributes).forEach((attr) => {
      if (!allowed.includes(attr.name)) child.removeAttribute(attr.name);
    });
    if (tag === "A") {
      const href = child.getAttribute("href") || "";
      if (!/^https?:\/\//i.test(href)) {
        child.removeAttribute("href");
      } else {
        child.setAttribute("target", "_blank");
        child.setAttribute("rel", "noopener noreferrer");
      }
    }
    sanitizeRichNode(child);
  });
}

function sanitizeRichHtml(rawHtml) {
  const container = document.createElement("div");
  container.innerHTML = rawHtml || "";
  sanitizeRichNode(container);
  return container.innerHTML;
}

function linkifyRichHtml(rawHtml) {
  const container = document.createElement("div");
  container.innerHTML = rawHtml || "";
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const targets = [];
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement && node.parentElement.closest("a")) continue;
    if (/https?:\/\//.test(node.nodeValue)) targets.push(node);
  }
  targets.forEach((textNode) => {
    const frag = document.createDocumentFragment();
    textNode.nodeValue.split(/(https?:\/\/[^\s<]+)/g).forEach((part) => {
      if (/^https?:\/\//.test(part)) {
        const a = document.createElement("a");
        a.href = part;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = part;
        frag.appendChild(a);
      } else if (part) {
        frag.appendChild(document.createTextNode(part));
      }
    });
    textNode.parentNode.replaceChild(frag, textNode);
  });
  return container.innerHTML;
}

function isRichContentEmpty(html) {
  const container = document.createElement("div");
  container.innerHTML = html || "";
  return container.textContent.trim() === "";
}

function attachRichPasteHandler(el) {
  if (!el || el.dataset.richPasteBound) return;
  el.dataset.richPasteBound = "1";
  el.addEventListener("paste", (e) => {
    e.preventDefault();
    const html = e.clipboardData.getData("text/html");
    const text = e.clipboardData.getData("text/plain");
    if (html) {
      document.execCommand("insertHTML", false, sanitizeRichHtml(html));
    } else if (text) {
      document.execCommand("insertText", false, text);
    }
  });
}

// ── 표 편집 툴바: 붙여넣은 표 안을 클릭하면 행/열 추가·삭제 버튼 표시 ───────
let tableEditToolbar = null;
let tableEditActiveCell = null;

function ensureTableEditToolbar() {
  if (tableEditToolbar) return tableEditToolbar;
  const bar = document.createElement("div");
  bar.className = "table-edit-toolbar d-none";
  bar.innerHTML = `
    <button type="button" data-action="add-row" title="아래에 행 추가"><i class="bi bi-plus-lg"></i>행</button>
    <button type="button" data-action="del-row" title="이 행 삭제"><i class="bi bi-dash-lg"></i>행</button>
    <button type="button" data-action="add-col" title="오른쪽에 열 추가"><i class="bi bi-plus-lg"></i>열</button>
    <button type="button" data-action="del-col" title="이 열 삭제"><i class="bi bi-dash-lg"></i>열</button>
  `;
  document.body.appendChild(bar);
  bar.addEventListener("mousedown", (e) => e.preventDefault());
  bar.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn || !tableEditActiveCell || !document.body.contains(tableEditActiveCell)) return;
    const actions = {
      "add-row": insertTableRow, "del-row": deleteTableRow,
      "add-col": insertTableColumn, "del-col": deleteTableColumn,
    };
    actions[btn.dataset.action](tableEditActiveCell);
  });
  tableEditToolbar = bar;
  return bar;
}

function showTableEditToolbar(cell) {
  const bar = ensureTableEditToolbar();
  tableEditActiveCell = cell;
  const rect = cell.getBoundingClientRect();
  bar.classList.remove("d-none");
  bar.style.top = `${Math.max(rect.top - bar.offsetHeight - 4, 4)}px`;
  bar.style.left = `${rect.left}px`;
}

function hideTableEditToolbar() {
  if (tableEditToolbar) tableEditToolbar.classList.add("d-none");
  tableEditActiveCell = null;
}

function cellColumnIndex(cell) {
  return Array.from(cell.parentElement.children).indexOf(cell);
}

function insertTableRow(cell) {
  const tr = cell.closest("tr");
  if (!tr) return;
  const newRow = document.createElement("tr");
  Array.from(tr.children).forEach(() => newRow.appendChild(document.createElement("td")));
  tr.after(newRow);
}

function deleteTableRow(cell) {
  const tr = cell.closest("tr");
  const table = cell.closest("table");
  if (!tr || !table) return;
  tr.remove();
  if (!table.querySelector("tr")) table.remove();
  hideTableEditToolbar();
}

function insertTableColumn(cell) {
  const table = cell.closest("table");
  if (!table) return;
  const colIndex = cellColumnIndex(cell);
  table.querySelectorAll("tr").forEach((row) => {
    const rowCell = row.children[colIndex];
    const newCell = document.createElement(rowCell && rowCell.tagName === "TH" ? "th" : "td");
    if (rowCell) rowCell.after(newCell);
    else row.appendChild(newCell);
  });
}

function deleteTableColumn(cell) {
  const table = cell.closest("table");
  if (!table) return;
  const colIndex = cellColumnIndex(cell);
  table.querySelectorAll("tr").forEach((row) => {
    const rowCell = row.children[colIndex];
    if (rowCell) rowCell.remove();
  });
  if (!table.querySelector("td, th")) table.remove();
  hideTableEditToolbar();
}

function attachTableEditToolbar(el) {
  if (!el || el.dataset.tableToolbarBound) return;
  el.dataset.tableToolbarBound = "1";
  const check = () => {
    const sel = window.getSelection();
    let node = sel.rangeCount ? sel.anchorNode : null;
    if (node && node.nodeType === Node.TEXT_NODE) node = node.parentElement;
    const cell = node && node.closest ? node.closest("td, th") : null;
    if (cell && el.contains(cell)) {
      showTableEditToolbar(cell);
    } else {
      hideTableEditToolbar();
    }
  };
  el.addEventListener("keyup", check);
  el.addEventListener("mouseup", check);
  el.addEventListener("focus", check);
  el.addEventListener("blur", () => {
    setTimeout(() => {
      if (!tableEditToolbar || !tableEditToolbar.matches(":hover")) hideTableEditToolbar();
    }, 150);
  });
}

let lastNotesContent = "";

async function loadNotes() {
  const data = await fetchJson(`/api/units/${UNIT_ID}/notes`);
  lastNotesContent = data.content || "";
  renderNotesView(lastNotesContent);
  document.getElementById("notesEdit").innerHTML = lastNotesContent;
  document.getElementById("notesSavedAt").textContent = data.updated_at
    ? `최종 수정: ${data.updated_at}`
    : "";
}

function renderNotesView(content) {
  const view = document.getElementById("notesView");
  if (isRichContentEmpty(content)) {
    view.innerHTML = "";
    view.classList.add("is-empty");
  } else {
    view.classList.remove("is-empty");
    view.innerHTML = linkifyRichHtml(content);
  }
}

function setNotesEditing(editing) {
  document.getElementById("notesView").classList.toggle("d-none", editing);
  document.getElementById("notesEdit").classList.toggle("d-none", !editing);
  document.getElementById("editNotesBtn").classList.toggle("d-none", editing);
  document.getElementById("saveNotesBtn").classList.toggle("d-none", !editing);
  document.getElementById("cancelNotesBtn").classList.toggle("d-none", !editing);
  if (editing) document.getElementById("notesEdit").focus();
}

async function loadUnitHeader() {
  try {
    const unit = await fetchJson(`/api/units/${UNIT_ID}`);
    currentEquipmentId = unit.equipment_id;
    document.getElementById("unitPageTitle").textContent = unit.name;
    document.getElementById("unitIcon").textContent = unit.icon;
    document.getElementById("unitName").textContent = unit.name;
    document.getElementById("unitShape").style.setProperty("--shape-color", unit.color);
    document.getElementById("backToEquipmentBtn").href = `/equipment/${unit.equipment_id}`;
    const isMaster = unit.equipment_id === MASTER_EQUIPMENT_ID;
    document.getElementById("masterHint").classList.toggle("d-none", !isMaster);
    document.getElementById("applyPartsBtn").classList.toggle("d-none", !isMaster);
  } catch (err) {
    alert("유닛 정보를 불러올 수 없습니다.");
    window.location.href = "/";
  }
}

async function loadParts() {
  currentParts = await fetchJson(`/api/units/${UNIT_ID}/parts`);
  renderPartsCanvas(currentParts);
}

function renderPartsCanvas(parts) {
  const canvas = document.getElementById("partsCanvas");
  canvas.innerHTML = parts.map(partShapeHtml).join("");
  parts.forEach((p) => {
    const card = canvas.querySelector(`[data-part-id="${p.id}"]`);
    card.style.left = `${p.pos_x}%`;
    card.style.top = `${p.pos_y}%`;
    card.style.width = `${p.width}px`;
    card.style.height = `${p.height}px`;

    card.querySelector(".edit-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openPartEditModal(p);
    });
    card.querySelector(".delete-unit-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`"${p.name}" 부품을 삭제할까요? 휴지통으로 이동하며, 나중에 복원할 수 있습니다.`)) return;
      await fetchJson(`/api/parts/${p.id}`, { method: "DELETE" });
      loadParts();
    });
    card.querySelector(".copy-part-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      copyPart(p);
    });

    makeDraggable(card, p);
    makeResizable(card, p);
    makeResizableHorizontal(card, p);
  });
}

const MIN_SIZE_W = 90;
const MIN_SIZE_H = 80;
const MAX_SIZE_W = 320;
const MAX_SIZE_H = 260;

function makeResizable(card, part) {
  const handle = card.querySelector(".resize-handle");
  if (!handle) return;

  handle.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    e.preventDefault();
    e.stopPropagation();

    const startX = e.clientX;
    const startY = e.clientY;
    const startWidth = card.offsetWidth;
    const startHeight = card.offsetHeight;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      const w = Math.min(Math.max(startWidth + dx, MIN_SIZE_W), MAX_SIZE_W);
      const h = Math.min(Math.max(startHeight + dy, MIN_SIZE_H), MAX_SIZE_H);
      card.style.width = `${w}px`;
      card.style.height = `${h}px`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      const width = card.offsetWidth;
      const height = card.offsetHeight;
      part.width = width;
      part.height = height;
      await fetchJson(`/api/parts/${part.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width, height }),
      });
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function makeResizableHorizontal(card, part) {
  const handle = card.querySelector(".resize-handle-h");
  if (!handle) return;

  handle.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    e.preventDefault();
    e.stopPropagation();

    const startX = e.clientX;
    const startWidth = card.offsetWidth;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const w = Math.min(Math.max(startWidth + dx, MIN_SIZE_W), MAX_SIZE_W);
      card.style.width = `${w}px`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      const width = card.offsetWidth;
      part.width = width;
      await fetchJson(`/api/parts/${part.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width }),
      });
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function makeDraggable(card, part) {
  card.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    e.preventDefault();

    const canvas = document.getElementById("partsCanvas");
    const rect = canvas.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = (parseFloat(card.style.left) / 100) * rect.width;
    const startTop = (parseFloat(card.style.top) / 100) * rect.height;
    let moved = false;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
      const px = Math.min(Math.max(startLeft + dx, rect.width * 0.04), rect.width * 0.96);
      const py = Math.min(Math.max(startTop + dy, rect.height * 0.04), rect.height * 0.96);
      card.style.left = `${(px / rect.width) * 100}%`;
      card.style.top = `${(py / rect.height) * 100}%`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      if (moved) {
        const pos_x = parseFloat(card.style.left);
        const pos_y = parseFloat(card.style.top);
        part.pos_x = pos_x;
        part.pos_y = pos_y;
        await fetchJson(`/api/parts/${part.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pos_x, pos_y }),
        });
      } else {
        openPartDetailModal(part.id);
      }
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  card.addEventListener("click", (e) => {
    if (editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    openPartDetailModal(part.id);
  });
}

function partShapeHtml(p) {
  const color = statusColor[p.status];
  return `
    <div class="unit-card ${editMode ? "edit-mode" : ""}" data-part-id="${p.id}" style="--uc:${color}">
      <span class="unit-status-dot dot-${p.status}"></span>
      <div class="unit-icon-wrap"><span class="unit-icon">${p.icon}</span></div>
      <div class="unit-name">${escapeHtml(p.name)}</div>
      <div class="unit-part-count">${statusLabel[p.status]}</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="편집"><i class="bi bi-pencil"></i></button>
        <button class="copy-part-btn" title="복사"><i class="bi bi-copy"></i></button>
        <button class="delete-unit-btn" title="삭제"><i class="bi bi-trash"></i></button>
      </div>
      <div class="resize-handle" title="크기 조절 (가로+세로)"></div>
      <div class="resize-handle-h" title="가로 크기 조절"></div>
    </div>`;
}

const PART_CLIPBOARD_KEY = "partClipboard";

function copyPart(part) {
  const clipboard = {
    name: part.name,
    spec: part.spec,
    cycle_days: part.cycle_days,
    cycle_unit: part.cycle_unit,
    cost: part.cost,
    note: part.note,
    memo: part.memo,
    icon: part.icon,
    width: part.width,
    height: part.height,
    supplier: part.supplier,
    supplier_contact: part.supplier_contact,
    lead_time_days: part.lead_time_days,
  };
  localStorage.setItem(PART_CLIPBOARD_KEY, JSON.stringify(clipboard));
  updatePartPasteButton();
  alert(`"${part.name}" 부품을 복사했습니다.\n"붙여넣기" 버튼으로 동일한 부품을 만들 수 있습니다.`);
}

function getPartClipboard() {
  const raw = localStorage.getItem(PART_CLIPBOARD_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function updatePartPasteButton() {
  const clipboard = getPartClipboard();
  const btn = document.getElementById("pastePartBtn");
  btn.classList.toggle("d-none", !editMode || !clipboard);
  if (clipboard) btn.title = `"${clipboard.name}" 붙여넣기`;
}

async function pastePart() {
  const clipboard = getPartClipboard();
  if (!clipboard) {
    alert("복사된 부품이 없습니다. 먼저 부품의 복사 아이콘을 눌러주세요.");
    return;
  }
  await fetchJson(`/api/units/${UNIT_ID}/parts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: `${clipboard.name} 복사본`,
      spec: clipboard.spec,
      cycle_days: clipboard.cycle_days,
      cycle_unit: clipboard.cycle_unit,
      cost: clipboard.cost,
      note: clipboard.note,
      memo: clipboard.memo,
      icon: clipboard.icon,
      width: clipboard.width,
      height: clipboard.height,
      supplier: clipboard.supplier,
      supplier_contact: clipboard.supplier_contact,
      lead_time_days: clipboard.lead_time_days,
    }),
  });
  loadParts();
}

function formatCycleDisplay(cycleDays, cycleUnit) {
  if (cycleUnit === "년") {
    const years = Math.round((cycleDays / 365) * 100) / 100;
    return `${years}년`;
  }
  return `${cycleDays}일`;
}

function cycleDaysToDisplayValue(cycleDays, cycleUnit) {
  if (cycleUnit === "년") {
    return Math.round((cycleDays / 365) * 100) / 100;
  }
  return cycleDays;
}

function formatCost(cost) {
  return `${Number(cost || 0).toLocaleString("ko-KR")}원`;
}

function setDrawingPreview(dataUrl) {
  currentPartDrawingData = dataUrl;
  const preview = document.getElementById("partDrawingPreview");
  const placeholder = document.getElementById("partDrawingPlaceholder");
  const removeBtn = document.getElementById("partDrawingRemoveBtn");
  const status = document.getElementById("partDrawingStatus");
  if (dataUrl) {
    preview.src = dataUrl;
    preview.classList.remove("d-none");
    placeholder.classList.add("d-none");
    removeBtn.classList.remove("d-none");
    status.textContent = "등록됨";
    document.getElementById("partDrawingArea").classList.remove("d-none");
  } else {
    preview.classList.add("d-none");
    preview.src = "";
    placeholder.classList.remove("d-none");
    removeBtn.classList.add("d-none");
    status.textContent = "";
    document.getElementById("partDrawingArea").classList.add("d-none");
  }
}

function handleDrawingPaste(e) {
  const items = e.clipboardData ? e.clipboardData.items : null;
  if (!items) return;
  for (const item of items) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      e.preventDefault();
      const file = item.getAsFile();
      if (file.size > MAX_DRAWING_BYTES) {
        alert("이미지 용량이 너무 큽니다 (최대 5MB).");
        return;
      }
      const reader = new FileReader();
      reader.onload = () => setDrawingPreview(reader.result);
      reader.readAsDataURL(file);
      return;
    }
  }
}

function openPartDetailModal(partId) {
  const p = currentParts.find((x) => x.id === partId);
  if (!p) return;
  currentPartId = partId;
  document.getElementById("partDetailTitle").textContent = p.name;
  const badge = statusBadge[p.status];
  const label = statusLabel[p.status];
  const lastText = p.last_replaced_date ? `최근 교체: ${p.last_replaced_date}` : "교체 이력 없음";
  const dueText = p.next_due
    ? `다음 교체 예정: ${p.next_due} (${p.days_left >= 0 ? p.days_left + "일 남음" : Math.abs(p.days_left) + "일 초과"})`
    : "";
  const stockText = `재고: <span class="${(p.stock_qty || 0) <= 0 ? "text-danger fw-bold" : ""}">${p.stock_qty || 0}개</span>`;
  const supplierText = p.supplier
    ? ` &middot; 구매처: ${escapeHtml(p.supplier)}${p.supplier_contact ? " (" + escapeHtml(p.supplier_contact) + ")" : ""}`
    : "";
  const leadTimeText = p.lead_time_days ? ` &middot; 리드타임: ${p.lead_time_days}일` : "";
  document.getElementById("partDetailBody").innerHTML = `
    <span class="badge ${badge} mb-2">${label}</span>
    ${p.spec ? `<div class="part-spec mb-1">규격: ${escapeHtml(p.spec)}</div>` : ""}
    <div class="small text-muted">
      교체 주기: ${formatCycleDisplay(p.cycle_days, p.cycle_unit)} &middot; 금액: ${formatCost(p.cost)} &middot; ${lastText}
      ${dueText ? `<br>${dueText}` : ""}
      ${p.note ? `<br>비고: ${escapeHtml(p.note)}` : ""}
      <br>${stockText}${supplierText}${leadTimeText}
    </div>`;
  renderPartMemoView(p.memo);
  document.getElementById("partDetailMemoEdit").innerHTML = p.memo || "";
  setPartMemoEditing(false);
  document.getElementById("partDetailDrawingBtn").classList.toggle("d-none", !p.drawing_data);
  partDetailModal.show();
}

function renderPartMemoView(memo) {
  const memoView = document.getElementById("partDetailMemo");
  if (isRichContentEmpty(memo)) {
    memoView.innerHTML = "";
    memoView.classList.add("is-empty");
  } else {
    memoView.classList.remove("is-empty");
    memoView.innerHTML = linkifyRichHtml(memo);
  }
}

function setPartMemoEditing(editing) {
  document.getElementById("partDetailMemo").classList.toggle("d-none", editing);
  document.getElementById("partDetailMemoEdit").classList.toggle("d-none", !editing);
  document.getElementById("editPartMemoBtn").classList.toggle("d-none", editing);
  document.getElementById("savePartMemoBtn").classList.toggle("d-none", !editing);
  document.getElementById("cancelPartMemoBtn").classList.toggle("d-none", !editing);
  if (editing) document.getElementById("partDetailMemoEdit").focus();
}

function openDrawingModal() {
  const p = currentParts.find((x) => x.id === currentPartId);
  if (!p || !p.drawing_data) return;
  document.getElementById("drawingModalTitle").textContent = `도면 - ${p.name}`;
  document.getElementById("drawingModalImg").src = p.drawing_data;
  drawingModal.show();
}

function openReplaceModal() {
  document.getElementById("replaceDate").value = new Date().toISOString().slice(0, 10);
  document.getElementById("replaceCost").value = 0;
  document.getElementById("replaceNote").value = "";
  replaceModal.show();
}

async function openHistoryModal() {
  const p = currentParts.find((x) => x.id === currentPartId);
  const history = await fetchJson(`/api/parts/${currentPartId}/history`);
  const list = document.getElementById("historyList");
  document.querySelector("#historyModal .modal-title").textContent = `교체 이력 - ${p ? p.name : ""}`;
  if (history.length === 0) {
    list.innerHTML = `<p class="text-muted">교체 이력이 없습니다.</p>`;
  } else {
    list.innerHTML = history
      .map(
        (h) => `
      <div class="history-row d-flex justify-content-between">
        <div><strong>${h.replaced_date}</strong> &middot; ${formatCost(h.cost)} ${h.note ? " - " + escapeHtml(h.note) : ""}</div>
        <button class="btn btn-sm btn-link text-danger p-0 del-history-btn" data-id="${h.id}">삭제</button>
      </div>`
      )
      .join("");
    list.querySelectorAll(".del-history-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await fetchJson(`/api/history/${btn.dataset.id}`, { method: "DELETE" });
        loadParts().then(() => openHistoryModal());
      });
    });
  }
  historyModal.show();
}

function openPartEditModal(part) {
  document.getElementById("partEditTitle").textContent = part ? "부품 편집" : "부품 추가";
  document.getElementById("partEditId").value = part ? part.id : "";
  document.getElementById("partEditName").value = part ? part.name : "";
  document.getElementById("partEditSpec").value = part ? part.spec || "" : "";
  const icon = part ? part.icon : "🔩";
  document.getElementById("partEditIcon").value = icon;
  const cycleUnit = part ? part.cycle_unit || "일" : "일";
  document.getElementById("partEditCycleUnit").value = cycleUnit;
  document.getElementById("partEditCycle").value = part ? cycleDaysToDisplayValue(part.cycle_days, cycleUnit) : 90;
  document.getElementById("partEditCost").value = part ? part.cost || 0 : 0;
  document.getElementById("partEditNote").value = part ? part.note || "" : "";
  document.getElementById("partEditMemo").innerHTML = part ? part.memo || "" : "";
  document.getElementById("partEditStockQty").value = part ? part.stock_qty || 0 : 0;
  document.getElementById("partEditLeadTime").value = part && part.lead_time_days != null ? part.lead_time_days : "";
  document.getElementById("partEditSupplier").value = part ? part.supplier || "" : "";
  document.getElementById("partEditSupplierContact").value = part ? part.supplier_contact || "" : "";
  document.getElementById("partEditLastDate").value = "";
  document.getElementById("partEditLastDateWrap").classList.toggle("d-none", !!part);
  renderIconPicker("partIconPicker", "partEditIcon", icon);
  setDrawingPreview(part ? part.drawing_data || null : null);
  partEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  partDetailModal = new bootstrap.Modal(document.getElementById("partDetailModal"));
  replaceModal = new bootstrap.Modal(document.getElementById("replaceModal"));
  historyModal = new bootstrap.Modal(document.getElementById("historyModal"));
  partEditModal = new bootstrap.Modal(document.getElementById("partEditModal"));
  drawingModal = new bootstrap.Modal(document.getElementById("drawingModal"));

  tick();
  setInterval(tick, 1000);
  loadUnitHeader();
  loadParts();
  loadNotes();
  attachRichPasteHandler(document.getElementById("partEditMemo"));
  attachTableEditToolbar(document.getElementById("partEditMemo"));

  document.getElementById("editModeBtn").addEventListener("click", (e) => {
    editMode = !editMode;
    e.currentTarget.classList.toggle("btn-outline-light", !editMode);
    e.currentTarget.classList.toggle("btn-warning", editMode);
    document.getElementById("addPartBtn").classList.toggle("d-none", !editMode);
    updatePartPasteButton();
    renderPartsCanvas(currentParts);
  });

  document.getElementById("addPartBtn").addEventListener("click", () => openPartEditModal(null));
  document.getElementById("pastePartBtn").addEventListener("click", pastePart);

  attachRichPasteHandler(document.getElementById("notesEdit"));
  attachTableEditToolbar(document.getElementById("notesEdit"));
  document.getElementById("editNotesBtn").addEventListener("click", () => setNotesEditing(true));
  document.getElementById("cancelNotesBtn").addEventListener("click", () => {
    document.getElementById("notesEdit").innerHTML = lastNotesContent;
    setNotesEditing(false);
  });
  document.getElementById("saveNotesBtn").addEventListener("click", async () => {
    const content = sanitizeRichHtml(document.getElementById("notesEdit").innerHTML);
    try {
      const data = await fetchJson(`/api/units/${UNIT_ID}/notes`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      lastNotesContent = data.content || "";
      renderNotesView(lastNotesContent);
      document.getElementById("notesSavedAt").textContent = data.updated_at
        ? `최종 수정: ${data.updated_at}`
        : "";
      setNotesEditing(false);
    } catch (err) {
      alert(err.message);
    }
  });

  attachRichPasteHandler(document.getElementById("partDetailMemoEdit"));
  attachTableEditToolbar(document.getElementById("partDetailMemoEdit"));
  document.getElementById("editPartMemoBtn").addEventListener("click", () => setPartMemoEditing(true));
  document.getElementById("cancelPartMemoBtn").addEventListener("click", () => {
    const p = currentParts.find((x) => x.id === currentPartId);
    document.getElementById("partDetailMemoEdit").innerHTML = (p && p.memo) || "";
    setPartMemoEditing(false);
  });
  document.getElementById("savePartMemoBtn").addEventListener("click", async () => {
    const memo = sanitizeRichHtml(document.getElementById("partDetailMemoEdit").innerHTML);
    try {
      const updated = await fetchJson(`/api/parts/${currentPartId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ memo }),
      });
      const p = currentParts.find((x) => x.id === currentPartId);
      if (p) p.memo = updated.memo;
      renderPartMemoView(updated.memo);
      setPartMemoEditing(false);
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("partDetailReplaceBtn").addEventListener("click", () => {
    partDetailModal.hide();
    openReplaceModal();
  });
  document.getElementById("partDetailHistoryBtn").addEventListener("click", () => {
    partDetailModal.hide();
    openHistoryModal();
  });
  document.getElementById("partDetailDrawingBtn").addEventListener("click", () => {
    partDetailModal.hide();
    openDrawingModal();
  });

  document.getElementById("partDrawingBtn").addEventListener("click", () => {
    const area = document.getElementById("partDrawingArea");
    area.classList.toggle("d-none");
    if (!area.classList.contains("d-none")) area.focus();
  });
  document.getElementById("partDrawingArea").addEventListener("paste", handleDrawingPaste);
  document.getElementById("partDrawingRemoveBtn").addEventListener("click", () => {
    setDrawingPreview(null);
  });

  document.getElementById("replaceForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      replaced_date: document.getElementById("replaceDate").value,
      cost: parseFloat(document.getElementById("replaceCost").value) || 0,
      note: document.getElementById("replaceNote").value.trim(),
    };
    try {
      await fetchJson(`/api/parts/${currentPartId}/replace`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      replaceModal.hide();
      loadParts();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("partEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("partEditId").value;
    const cycleUnit = document.getElementById("partEditCycleUnit").value;
    const cycleValue = parseFloat(document.getElementById("partEditCycle").value);
    const cycleDays = cycleUnit === "년" ? Math.round(cycleValue * 365) : Math.round(cycleValue);
    const payload = {
      name: document.getElementById("partEditName").value.trim(),
      spec: document.getElementById("partEditSpec").value.trim(),
      icon: document.getElementById("partEditIcon").value.trim(),
      cycle_days: cycleDays,
      cycle_unit: cycleUnit,
      cost: parseFloat(document.getElementById("partEditCost").value) || 0,
      note: document.getElementById("partEditNote").value.trim(),
      memo: sanitizeRichHtml(document.getElementById("partEditMemo").innerHTML),
      drawing_data: currentPartDrawingData,
      stock_qty: parseInt(document.getElementById("partEditStockQty").value, 10) || 0,
      lead_time_days: document.getElementById("partEditLeadTime").value || null,
      supplier: document.getElementById("partEditSupplier").value.trim(),
      supplier_contact: document.getElementById("partEditSupplierContact").value.trim(),
    };
    try {
      if (id) {
        await fetchJson(`/api/parts/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        payload.last_replaced_date = document.getElementById("partEditLastDate").value || null;
        await fetchJson(`/api/units/${UNIT_ID}/parts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      partEditModal.hide();
      loadParts();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("applyPartsBtn").addEventListener("click", async () => {
    const ok = confirm(
      "현재 이 유닛의 부품 구성을 동일한 이름의 유닛을 가진 나머지 설비 전체에 적용합니다.\n" +
      "- 이름이 같은 부품은 규격/교체주기/비고/메모/도면/재고수량/구매처/리드타임/아이콘/위치/크기가 이 구성대로 갱신됩니다.\n" +
      "- 여기 없는 이름의 부품은 각 설비에서 삭제되며, 등록된 교체 이력도 함께 삭제됩니다.\n\n" +
      "계속하시겠습니까?"
    );
    if (!ok) return;
    try {
      const result = await fetchJson(`/api/units/${UNIT_ID}/apply-parts`, { method: "POST" });
      alert(`설비 ${result.equipment_count}대에 부품 구성(${result.part_count}개)을 적용했습니다.`);
    } catch (err) {
      alert(err.message);
    }
  });
});
</script>
</body>
</html>
"""


CONFIG_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>기본 유닛 구성 - 설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <a href="/" class="btn btn-sm btn-outline-light"><i class="bi bi-arrow-left"></i> 대시보드</a>
    <i class="bi bi-diagram-3"></i>
    <h1>기본 유닛 구성 (모든 설비 공통 템플릿)</h1>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span id="clock" class="clock"></span>
    <button id="applyBtn" class="btn btn-sm btn-warning">
      <i class="bi bi-cloud-arrow-up"></i> 모든 설비에 적용
    </button>
  </div>
</header>

<main class="container-fluid py-4">

  <p class="text-muted small mb-3">
    여기서 구성한 유닛(이름·아이콘·색상·위치·크기)은 <strong>모든 설비에 공통으로 적용</strong>되는 기본 템플릿입니다.
    유닛을 자유롭게 추가/편집/삭제/드래그 배치한 뒤 "모든 설비에 적용" 버튼을 눌러야 실제 설비 페이지에 반영됩니다.
  </p>

  <div id="templateFrame" class="equipment-frame">
    <div class="equipment-label">유닛을 드래그로 배치 · 모서리로 크기 조절 · 클릭해서 이름/아이콘/색상 편집</div>
    <div id="canvas" class="equipment-canvas"></div>
    <div class="canvas-actions">
      <button id="addUnitBtn" class="btn btn-sm btn-outline-primary">
        <i class="bi bi-plus-lg"></i> 유닛 추가
      </button>
      <button id="pasteUnitBtn" class="btn btn-sm btn-outline-secondary d-none">
        <i class="bi bi-clipboard-check"></i> 붙여넣기
      </button>
    </div>
  </div>

</main>

<!-- 유닛 추가/편집 모달 -->
<div class="modal fade" id="unitEditModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="unitEditTitle">유닛 추가</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <form id="unitEditForm">
          <input type="hidden" id="unitEditId">
          <div class="mb-2">
            <label class="form-label">유닛 이름</label>
            <input type="text" class="form-control" id="unitEditName" required>
          </div>
          <div class="row g-2">
            <div class="col-6">
              <label class="form-label">아이콘 (이모지)</label>
              <input type="text" class="form-control" id="unitEditIcon" placeholder="⚙️">
            </div>
            <div class="col-6">
              <label class="form-label">색상</label>
              <input type="color" class="form-control form-control-color w-100" id="unitEditColor" value="#1a3a5c">
            </div>
          </div>
          <div id="unitIconPicker" class="icon-picker"></div>
          <button type="submit" class="btn btn-primary w-100 mt-3">저장</button>
        </form>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
let unitEditModal;

const ICON_CHOICES = [
  "⚙️", "🔧", "🔩", "🛠️", "🪛", "🔨", "📦", "🖥️",
  "🖨️", "💻", "📡", "🎛️", "🚨", "💡", "🔌", "⚡",
  "🔋", "🌡️", "💧", "🧪", "🧯", "🧰", "🏭", "⚗️",
  "🌀", "🗜️", "🧲", "📊", "🛞", "🚿", "🔥", "❄️",
];

function renderIconPicker(containerId, inputId, current) {
  const container = document.getElementById(containerId);
  const input = document.getElementById(inputId);
  container.innerHTML = ICON_CHOICES.map(
    (ic) => `<button type="button" class="icon-choice ${ic === current ? "selected" : ""}" data-icon="${ic}">${ic}</button>`
  ).join("");
  container.classList.add("d-none");
  container.querySelectorAll(".icon-choice").forEach((btn) => {
    btn.addEventListener("click", () => {
      input.value = btn.dataset.icon;
      container.querySelectorAll(".icon-choice").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      container.classList.add("d-none");
    });
  });
  if (!container.dataset.toggleBound) {
    container.dataset.toggleBound = "1";
    input.addEventListener("click", () => {
      container.classList.toggle("d-none");
    });
    document.addEventListener("click", (e) => {
      if (!container.contains(e.target) && e.target !== input) {
        container.classList.add("d-none");
      }
    });
  }
}

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "요청 처리 중 오류가 발생했습니다");
  }
  return res.status === 204 ? null : res.json();
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

async function loadTemplates() {
  const templates = await fetchJson("/api/unit-templates");
  renderCanvas(templates);
}

function renderCanvas(templates) {
  const canvas = document.getElementById("canvas");
  canvas.innerHTML = templates.map(templateCardHtml).join("");
  templates.forEach((t) => {
    const card = canvas.querySelector(`[data-template-id="${t.id}"]`);
    card.style.left = `${t.pos_x}%`;
    card.style.top = `${t.pos_y}%`;
    card.style.width = `${t.width}px`;
    card.style.height = `${t.height}px`;

    card.querySelector(".edit-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openUnitEditModal(t);
    });
    card.querySelector(".delete-unit-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`"${t.name}" 유닛을 기본 구성에서 삭제할까요?`)) return;
      await fetchJson(`/api/unit-templates/${t.id}`, { method: "DELETE" });
      loadTemplates();
    });
    card.querySelector(".copy-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      copyUnit(t);
    });

    makeDraggable(card, t);
    makeResizable(card, t);
    makeResizableHorizontal(card, t);
  });
}

const MIN_UNIT_WIDTH = 90;
const MIN_UNIT_HEIGHT = 80;
const MAX_UNIT_WIDTH = 320;
const MAX_UNIT_HEIGHT = 260;

function makeResizable(card, template) {
  const handle = card.querySelector(".resize-handle");
  if (!handle) return;

  handle.addEventListener("mousedown", (e) => {
    e.preventDefault();
    e.stopPropagation();

    const startX = e.clientX;
    const startY = e.clientY;
    const startWidth = card.offsetWidth;
    const startHeight = card.offsetHeight;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      const w = Math.min(Math.max(startWidth + dx, MIN_UNIT_WIDTH), MAX_UNIT_WIDTH);
      const h = Math.min(Math.max(startHeight + dy, MIN_UNIT_HEIGHT), MAX_UNIT_HEIGHT);
      card.style.width = `${w}px`;
      card.style.height = `${h}px`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      const width = card.offsetWidth;
      const height = card.offsetHeight;
      template.width = width;
      template.height = height;
      await fetchJson(`/api/unit-templates/${template.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width, height }),
      });
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function makeResizableHorizontal(card, template) {
  const handle = card.querySelector(".resize-handle-h");
  if (!handle) return;

  handle.addEventListener("mousedown", (e) => {
    e.preventDefault();
    e.stopPropagation();

    const startX = e.clientX;
    const startWidth = card.offsetWidth;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const w = Math.min(Math.max(startWidth + dx, MIN_UNIT_WIDTH), MAX_UNIT_WIDTH);
      card.style.width = `${w}px`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      const width = card.offsetWidth;
      template.width = width;
      await fetchJson(`/api/unit-templates/${template.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width }),
      });
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function makeDraggable(card, template) {
  card.addEventListener("mousedown", (e) => {
    if (e.target.closest(".unit-edit-actions")) return;
    e.preventDefault();

    const canvas = document.getElementById("canvas");
    const rect = canvas.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = (parseFloat(card.style.left) / 100) * rect.width;
    const startTop = (parseFloat(card.style.top) / 100) * rect.height;
    let moved = false;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
      const px = Math.min(Math.max(startLeft + dx, rect.width * 0.04), rect.width * 0.96);
      const py = Math.min(Math.max(startTop + dy, rect.height * 0.04), rect.height * 0.96);
      card.style.left = `${(px / rect.width) * 100}%`;
      card.style.top = `${(py / rect.height) * 100}%`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      if (moved) {
        const pos_x = parseFloat(card.style.left);
        const pos_y = parseFloat(card.style.top);
        template.pos_x = pos_x;
        template.pos_y = pos_y;
        await fetchJson(`/api/unit-templates/${template.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pos_x, pos_y }),
        });
      } else {
        openUnitEditModal(template);
      }
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function templateCardHtml(t) {
  return `
    <div class="unit-card edit-mode" data-template-id="${t.id}" style="--uc:${t.color}">
      <div class="unit-icon-wrap"><span class="unit-icon">${t.icon}</span></div>
      <div class="unit-name">${escapeHtml(t.name)}</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="편집"><i class="bi bi-pencil"></i></button>
        <button class="copy-unit-btn" title="복사"><i class="bi bi-copy"></i></button>
        <button class="delete-unit-btn" title="삭제"><i class="bi bi-trash"></i></button>
      </div>
      <div class="resize-handle" title="크기 조절 (가로+세로)"></div>
      <div class="resize-handle-h" title="가로 크기 조절"></div>
    </div>`;
}

const UNIT_CLIPBOARD_KEY = "unitClipboard";

function copyUnit(template) {
  const clipboard = {
    name: template.name,
    icon: template.icon,
    color: template.color,
    width: template.width,
    height: template.height,
    parts: [],
  };
  localStorage.setItem(UNIT_CLIPBOARD_KEY, JSON.stringify(clipboard));
  updatePasteButton();
  alert(`"${template.name}" 유닛을 복사했습니다.\n"붙여넣기" 버튼으로 동일한 유닛을 만들 수 있습니다.`);
}

function getUnitClipboard() {
  const raw = localStorage.getItem(UNIT_CLIPBOARD_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function updatePasteButton() {
  const clipboard = getUnitClipboard();
  const btn = document.getElementById("pasteUnitBtn");
  btn.classList.toggle("d-none", !clipboard);
  if (clipboard) btn.title = `"${clipboard.name}" 붙여넣기`;
}

async function pasteUnit() {
  const clipboard = getUnitClipboard();
  if (!clipboard) {
    alert("복사된 유닛이 없습니다. 먼저 유닛의 복사 아이콘을 눌러주세요.");
    return;
  }
  await fetchJson("/api/unit-templates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: `${clipboard.name} 복사본`,
      icon: clipboard.icon,
      color: clipboard.color,
      width: clipboard.width,
      height: clipboard.height,
    }),
  });
  loadTemplates();
}

function openUnitEditModal(template) {
  document.getElementById("unitEditTitle").textContent = template ? "유닛 편집" : "유닛 추가";
  document.getElementById("unitEditId").value = template ? template.id : "";
  document.getElementById("unitEditName").value = template ? template.name : "";
  const icon = template ? template.icon : "⚙️";
  document.getElementById("unitEditIcon").value = icon;
  document.getElementById("unitEditColor").value = template ? template.color : "#1a3a5c";
  renderIconPicker("unitIconPicker", "unitEditIcon", icon);
  unitEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  unitEditModal = new bootstrap.Modal(document.getElementById("unitEditModal"));

  tick();
  setInterval(tick, 1000);
  loadTemplates();
  updatePasteButton();

  document.getElementById("addUnitBtn").addEventListener("click", () => openUnitEditModal(null));
  document.getElementById("pasteUnitBtn").addEventListener("click", pasteUnit);

  document.getElementById("applyBtn").addEventListener("click", async () => {
    const ok = confirm(
      "현재 기본 유닛 구성을 20개 설비 전체에 적용합니다.\n" +
      "- 이름이 같은 유닛은 위치/아이콘/색상이 이 구성대로 갱신됩니다.\n" +
      "- 여기 없는 이름의 유닛은 각 설비에서 삭제되며, 등록된 부품/이력도 함께 삭제됩니다.\n\n" +
      "계속하시겠습니까?"
    );
    if (!ok) return;
    try {
      const result = await fetchJson("/api/unit-templates/apply", { method: "POST" });
      alert(`설비 ${result.equipment_count}대에 유닛 구성(${result.unit_count}개)을 적용했습니다.`);
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("unitEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("unitEditId").value;
    const payload = {
      name: document.getElementById("unitEditName").value.trim(),
      icon: document.getElementById("unitEditIcon").value.trim(),
      color: document.getElementById("unitEditColor").value,
    };
    try {
      if (id) {
        await fetchJson(`/api/unit-templates/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await fetchJson(`/api/unit-templates`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      unitEditModal.hide();
      loadTemplates();
    } catch (err) {
      alert(err.message);
    }
  });
});
</script>
</body>
</html>
"""


ALERTS_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>전체 교체 현황 - 설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <a href="/" class="btn btn-sm btn-outline-light"><i class="bi bi-arrow-left"></i> 대시보드</a>
    <i class="bi bi-exclamation-triangle-fill"></i>
    <h1>전체 교체 현황</h1>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span id="clock" class="clock"></span>
    <a href="/api/alerts/export" class="btn btn-sm btn-outline-light">
      <i class="bi bi-file-earmark-spreadsheet"></i> CSV 내보내기
    </a>
  </div>
</header>

<main class="container-fluid py-4">

  <div class="legend mb-3">
    <span class="legend-item"><span class="dot dot-overdue"></span> 교체 필요</span>
    <span class="legend-item"><span class="dot dot-soon"></span> 교체 임박</span>
  </div>

  <div id="alertsList" class="alerts-list"></div>

</main>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

async function loadAlerts() {
  const res = await fetch("/api/alerts");
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const parts = await res.json();
  renderAlerts(parts);
}

function renderAlerts(parts) {
  const list = document.getElementById("alertsList");
  if (parts.length === 0) {
    list.innerHTML = `<p class="text-muted text-center py-5">교체가 필요하거나 임박한 부품이 없습니다.</p>`;
    return;
  }
  list.innerHTML = parts.map(alertRowHtml).join("");
  parts.forEach((p) => {
    const row = list.querySelector(`[data-row-id="${p.id}"]`);
    row.addEventListener("click", () => {
      window.location.href = `/unit/${p.unit_id}`;
    });
  });
}

function alertRowHtml(p) {
  const isOverdue = p.status === "overdue";
  const badge = isOverdue ? "badge-overdue" : "badge-soon";
  const label = isOverdue ? "교체 필요" : "교체 임박";
  const daysText = isOverdue ? `${Math.abs(p.days_left)}일 초과` : `${p.days_left}일 남음`;
  const lastText = p.last_replaced_date ? `최근 교체 ${p.last_replaced_date}` : "교체 이력 없음";
  return `
    <div class="alert-row" data-row-id="${p.id}">
      <span class="badge ${badge} alert-badge">${label}</span>
      <div class="alert-main">
        <div class="alert-title">
          <span>${p.equipment_icon}</span> ${escapeHtml(p.equipment_name)}
          <span class="alert-sep">›</span> ${escapeHtml(p.unit_name)}
          <span class="alert-sep">›</span> <strong>${p.icon} ${escapeHtml(p.name)}</strong>
        </div>
        <div class="alert-meta">교체 주기 ${p.cycle_days}일 &middot; ${lastText} &middot; ${daysText}</div>
      </div>
      <i class="bi bi-chevron-right alert-chevron"></i>
    </div>`;
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadAlerts();
});
</script>
</body>
</html>
"""


SEARCH_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>부품 검색 - 설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <a href="/" class="btn btn-sm btn-outline-light"><i class="bi bi-arrow-left"></i> 대시보드</a>
    <i class="bi bi-search"></i>
    <h1>부품 검색</h1>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span id="clock" class="clock"></span>
  </div>
</header>

<main class="container-fluid py-4">

  <div class="legend mb-3">
    <span class="legend-item"><span class="dot dot-ok"></span> 정상</span>
    <span class="legend-item"><span class="dot dot-soon"></span> 교체 임박</span>
    <span class="legend-item"><span class="dot dot-overdue"></span> 교체 필요</span>
    <span class="legend-item"><span class="dot dot-unknown"></span> 미기록</span>
  </div>

  <div class="dashboard-toolbar">
    <div class="search-box">
      <i class="bi bi-search"></i>
      <input type="text" id="partSearchInput" class="form-control form-control-sm" placeholder="부품명 또는 규격으로 검색 (예: O-Ring)" autofocus>
    </div>
    <select id="statusFilter" class="form-select form-select-sm w-auto">
      <option value="">전체 상태</option>
      <option value="overdue">교체 필요</option>
      <option value="soon">교체 임박</option>
      <option value="ok">정상</option>
      <option value="unknown">미기록</option>
    </select>
    <select id="equipmentFilter" class="form-select form-select-sm w-auto">
      <option value="">전체 설비</option>
    </select>
  </div>

  <div id="searchResults" class="alerts-list mt-3"></div>
  <p id="searchHintMsg" class="text-muted text-center py-4">불러오는 중...</p>

</main>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

const STATUS_LABEL = { ok: "정상", soon: "교체 임박", overdue: "교체 필요", unknown: "미기록" };

let searchTimer;

async function loadEquipmentFilterOptions() {
  const res = await fetch("/api/equipments");
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const equipments = await res.json();
  const select = document.getElementById("equipmentFilter");
  select.innerHTML =
    '<option value="">전체 설비</option>' +
    equipments.map((e) => `<option value="${e.id}">${escapeHtml(e.name)}</option>`).join("");
}

async function runSearch() {
  const q = document.getElementById("partSearchInput").value.trim();
  const status = document.getElementById("statusFilter").value;
  const equipmentId = document.getElementById("equipmentFilter").value;
  const list = document.getElementById("searchResults");
  const hint = document.getElementById("searchHintMsg");

  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (status) params.set("status", status);
  if (equipmentId) params.set("equipment_id", equipmentId);
  const res = await fetch(`/api/search?${params.toString()}`);
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const parts = await res.json();

  if (parts.length === 0) {
    list.innerHTML = "";
    hint.textContent = q || status || equipmentId ? "검색 결과가 없습니다." : "등록된 부품이 없습니다.";
    hint.classList.remove("d-none");
    return;
  }

  hint.classList.add("d-none");
  list.innerHTML = parts.map(searchRowHtml).join("");
  parts.forEach((p) => {
    const row = list.querySelector(`[data-row-id="${p.id}"]`);
    row.addEventListener("click", () => {
      window.location.href = `/unit/${p.unit_id}`;
    });
  });
}

function searchRowHtml(p) {
  const label = STATUS_LABEL[p.status] || "미기록";
  const lastText = p.last_replaced_date ? `최근 교체 ${p.last_replaced_date}` : "교체 이력 없음";
  const daysText = p.status === "overdue" ? `${Math.abs(p.days_left)}일 초과`
    : (p.status === "soon" || p.status === "ok") ? `${p.days_left}일 남음`
    : "";
  const specText = p.spec ? `${escapeHtml(p.spec)} &middot; ` : "";
  return `
    <div class="alert-row" data-row-id="${p.id}">
      <span class="badge badge-${p.status} alert-badge">${label}</span>
      <div class="alert-main">
        <div class="alert-title">
          <span>${p.equipment_icon}</span> ${escapeHtml(p.equipment_name)}
          <span class="alert-sep">›</span> ${escapeHtml(p.unit_name)}
          <span class="alert-sep">›</span> <strong>${p.icon} ${escapeHtml(p.name)}</strong>
        </div>
        <div class="alert-meta">${specText}교체 주기 ${p.cycle_days}일 &middot; ${lastText}${daysText ? " &middot; " + daysText : ""}</div>
      </div>
      <i class="bi bi-chevron-right alert-chevron"></i>
    </div>`;
}

document.addEventListener("DOMContentLoaded", async () => {
  tick();
  setInterval(tick, 1000);
  await loadEquipmentFilterOptions();
  document.getElementById("partSearchInput").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 200);
  });
  document.getElementById("statusFilter").addEventListener("change", runSearch);
  document.getElementById("equipmentFilter").addEventListener("change", runSearch);

  const q = new URLSearchParams(window.location.search).get("q");
  if (q) {
    document.getElementById("partSearchInput").value = q;
  }
  runSearch();
});
</script>
</body>
</html>
"""


STATS_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>통계 - 설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <i class="bi bi-bar-chart-fill"></i>
    <h1>부품/유닛 통계</h1>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span id="clock" class="clock"></span>
  </div>
</header>

<main class="container-fluid py-4">

  <div class="dashboard-toolbar">
    <div class="dropdown">
      <button class="btn btn-sm btn-outline-primary dropdown-toggle" type="button" id="unitFilterBtn"
        data-bs-toggle="dropdown" data-bs-auto-close="outside">
        <i class="bi bi-funnel"></i> 유닛 선택<span id="unitFilterCount"></span>
      </button>
      <div class="dropdown-menu p-2 unit-filter-menu" id="unitFilterMenu"></div>
    </div>
    <button id="clearFilterBtn" class="btn btn-sm btn-outline-secondary d-none">
      <i class="bi bi-x-lg"></i> 필터 해제
    </button>
    <div class="d-flex align-items-center gap-1">
      <input type="date" id="statsStartDate" class="form-control form-control-sm" style="width:150px">
      <span class="text-muted small">~</span>
      <input type="date" id="statsEndDate" class="form-control form-control-sm" style="width:150px">
      <button id="clearPeriodBtn" class="btn btn-sm btn-outline-secondary d-none">
        <i class="bi bi-x-lg"></i> 기간 해제
      </button>
    </div>
    <a id="exportCsvBtn" href="/api/stats/export.csv" class="btn btn-sm btn-outline-secondary">
      <i class="bi bi-file-earmark-spreadsheet"></i> CSV 다운로드
    </a>
    <a id="exportPptxBtn" href="/api/stats/export.pptx" class="btn btn-sm btn-outline-secondary">
      <i class="bi bi-file-earmark-slides"></i> PPT 다운로드
    </a>
  </div>

  <div class="stats-grid">
    <div class="stats-panel">
      <h6><i class="bi bi-cash-coin"></i> 금액순 <span id="costSubtitle" class="stats-subtitle">(부품 규격 기준)</span></h6>
      <div id="statsCost" class="stats-list"></div>
    </div>
    <div class="stats-panel">
      <h6><i class="bi bi-arrow-repeat"></i> 사용량 많은순 <span id="usageSubtitle" class="stats-subtitle">(부품 규격 기준)</span></h6>
      <div id="statsUsage" class="stats-list"></div>
    </div>
    <div class="stats-panel">
      <h6><i class="bi bi-hourglass-split"></i> 교체 주기 짧은순 <span class="stats-subtitle">(부품 규격 기준)</span></h6>
      <div id="statsCycle" class="stats-list"></div>
    </div>
    <div class="stats-panel">
      <h6><i class="bi bi-box-seam"></i> 부품수 많은순 <span class="stats-subtitle">(유닛 기준)</span></h6>
      <div id="statsPartCount" class="stats-list"></div>
    </div>
  </div>

</main>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
let selectedUnitNames = [];

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function formatMoney(v) {
  return `${Number(v || 0).toLocaleString("ko-KR")}원`;
}

function formatCycle(days, unit) {
  if (unit === "년") {
    return `${Math.round((days / 365) * 100) / 100}년`;
  }
  return `${days}일`;
}

function currentPeriodParams() {
  const params = new URLSearchParams();
  selectedUnitNames.forEach((name) => params.append("unit_name", name));
  const startDate = document.getElementById("statsStartDate").value;
  const endDate = document.getElementById("statsEndDate").value;
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  return params;
}

async function loadStats() {
  const params = currentPeriodParams();
  const res = await fetch(`/api/stats?${params.toString()}`);
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const data = await res.json();
  renderUnitFilter(data.unit_names);
  renderPartSpecPanel("statsCost", data.by_cost, (r) => formatMoney(r.total_cost));
  renderPartSpecPanel("statsUsage", data.by_usage, (r) => `${r.usage_count}회 교체`);
  renderPartSpecPanel("statsCycle", data.by_short_cycle, (r) => formatCycle(r.min_cycle_days, r.min_cycle_unit) + " 주기");
  renderUnitPanel("statsPartCount", data.by_part_count, (r) => `${r.part_count}개`);
  document.getElementById("costSubtitle").textContent = data.period_active ? "(선택 기간 교체 이력 기준)" : "(부품 규격 기준)";
  document.getElementById("usageSubtitle").textContent = data.period_active ? "(선택 기간 교체 이력 기준)" : "(부품 규격 기준)";
  document.getElementById("clearPeriodBtn").classList.toggle("d-none", !data.period_active);
  updateExportLinks();
}

function renderPartSpecPanel(elId, rows, metricText) {
  const el = document.getElementById(elId);
  if (rows.length === 0) {
    el.innerHTML = `<p class="text-muted text-center py-4 mb-0">데이터가 없습니다.</p>`;
    return;
  }
  el.innerHTML = rows
    .map(
      (r, i) => `
    <div class="stats-row" data-row-idx="${i}">
      <span class="stats-rank">${i + 1}</span>
      <div class="alert-main">
        <div class="alert-title">
          <strong>${escapeHtml(r.name)}</strong>
          ${r.spec ? `<span class="alert-sep">›</span> ${escapeHtml(r.spec)}` : ""}
        </div>
        <div class="alert-meta">${metricText(r)} &middot; ${r.instance_count}곳에 등록됨</div>
      </div>
      <i class="bi bi-chevron-right alert-chevron"></i>
    </div>`
    )
    .join("");
  rows.forEach((r, i) => {
    const row = el.querySelector(`[data-row-idx="${i}"]`);
    row.addEventListener("click", () => {
      window.location.href = `/search?q=${encodeURIComponent(r.name)}`;
    });
  });
}

function renderUnitPanel(elId, rows, metricText) {
  const el = document.getElementById(elId);
  if (rows.length === 0) {
    el.innerHTML = `<p class="text-muted text-center py-4 mb-0">데이터가 없습니다.</p>`;
    return;
  }
  el.innerHTML = rows
    .map(
      (r, i) => `
    <div class="stats-row" data-unit-id="${r.unit_id}">
      <span class="stats-rank">${i + 1}</span>
      <div class="alert-main">
        <div class="alert-title">
          <span>${r.equipment_icon}</span> ${escapeHtml(r.equipment_name)}
          <span class="alert-sep">›</span> ${r.unit_icon} ${escapeHtml(r.unit_name)}
        </div>
        <div class="alert-meta">${metricText(r)}</div>
      </div>
      <i class="bi bi-chevron-right alert-chevron"></i>
    </div>`
    )
    .join("");
  rows.forEach((r) => {
    const row = el.querySelector(`[data-unit-id="${r.unit_id}"]`);
    row.addEventListener("click", () => {
      window.location.href = `/unit/${r.unit_id}`;
    });
  });
}

function renderUnitFilter(names) {
  const menu = document.getElementById("unitFilterMenu");
  if (menu.dataset.rendered === "1") return;
  menu.dataset.rendered = "1";
  menu.innerHTML = names
    .map(
      (name, i) => `
    <div class="form-check">
      <input class="form-check-input" type="checkbox" value="${escapeHtml(name)}" id="unitFilter${i}">
      <label class="form-check-label" for="unitFilter${i}">${escapeHtml(name)}</label>
    </div>`
    )
    .join("");
  menu.querySelectorAll(".form-check-input").forEach((cb) => {
    cb.addEventListener("change", () => {
      selectedUnitNames = Array.from(menu.querySelectorAll(".form-check-input:checked")).map(
        (el) => el.value
      );
      updateFilterUi();
      loadStats();
    });
  });
}

function updateFilterUi() {
  const countEl = document.getElementById("unitFilterCount");
  const clearBtn = document.getElementById("clearFilterBtn");
  if (selectedUnitNames.length > 0) {
    countEl.innerHTML = `<span class="nav-badge">${selectedUnitNames.length}</span>`;
    clearBtn.classList.remove("d-none");
  } else {
    countEl.innerHTML = "";
    clearBtn.classList.add("d-none");
  }
}

function updateExportLinks() {
  const qs = currentPeriodParams().toString();
  document.getElementById("exportCsvBtn").href = `/api/stats/export.csv${qs ? "?" + qs : ""}`;
  document.getElementById("exportPptxBtn").href = `/api/stats/export.pptx${qs ? "?" + qs : ""}`;
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadStats();

  document.getElementById("clearFilterBtn").addEventListener("click", () => {
    selectedUnitNames = [];
    document
      .querySelectorAll("#unitFilterMenu .form-check-input")
      .forEach((cb) => (cb.checked = false));
    updateFilterUi();
    loadStats();
  });

  document.getElementById("statsStartDate").addEventListener("change", loadStats);
  document.getElementById("statsEndDate").addEventListener("change", loadStats);
  document.getElementById("clearPeriodBtn").addEventListener("click", () => {
    document.getElementById("statsStartDate").value = "";
    document.getElementById("statsEndDate").value = "";
    loadStats();
  });
});
</script>
</body>
</html>
"""


LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>로그인 - 설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<main class="login-wrap">
  <div class="login-card">
    <div class="login-icon"><i class="bi bi-shield-lock-fill"></i></div>
    <h1>설비 부품 교체 관리 시스템</h1>
    <p class="text-muted small mb-3">이름과 접속 비밀번호를 입력하세요</p>
    <div id="errorBox" class="alert alert-danger py-2 small d-none">비밀번호가 올바르지 않습니다</div>
    <div id="nameErrorBox" class="alert alert-danger py-2 small d-none">이름을 입력하세요</div>
    <form method="POST">
      <input type="text" name="name" class="form-control mb-2" placeholder="이름" autofocus required>
      <input type="password" name="password" class="form-control mb-3" placeholder="비밀번호" required>
      <button type="submit" class="btn btn-primary w-100">로그인</button>
    </form>
    <p class="text-muted small mt-3 mb-0">최초 비밀번호는 <strong>0000</strong> 입니다. 로그인 후 반드시 변경해주세요.</p>
  </div>
</main>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
  const errorType = new URLSearchParams(window.location.search).get("error");
  if (errorType === "2") {
    document.getElementById("nameErrorBox").classList.remove("d-none");
  } else if (errorType) {
    document.getElementById("errorBox").classList.remove("d-none");
  }
</script>
</body>
</html>
"""


BULK_ADD_PARTS_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>부품 일괄 등록 - 설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <a href="/" class="btn btn-sm btn-outline-light"><i class="bi bi-arrow-left"></i> 대시보드</a>
    <i class="bi bi-stack"></i>
    <h1>부품 일괄 등록 <span class="master-badge">기준 설비: TEAG01호기</span></h1>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span id="clock" class="clock"></span>
  </div>
</header>

<main class="container-fluid py-4">

  <div class="bulk-paste-box mb-3">
    <label class="form-label mb-1">
      엑셀 등에서 <strong>유닛이름, 부품이름, Q-CODE, 부가설명, 금액, 교체주기</strong> 순서로 복사해 아래에 붙여넣으세요 (한 줄에 부품 하나).
    </label>
    <p class="text-muted small mb-2">
      유닛 선택은 "기본 유닛 구성"에 등록된 유닛 이름을 기준으로 표시되며, 유닛 하나에 여러 개를 다중 선택할 수 있습니다(선택한 모든 유닛에 동일한 부품이 등록됩니다).
      교체주기는 "90", "90일", "2년"처럼 입력할 수 있으며 비워두면 기본값(90일)이 적용됩니다.
    </p>
    <textarea id="pasteArea" class="form-control" rows="4" placeholder="로드포트1&#9;오링&#9;Q-1234&#9;내열용&#9;5000&#9;90일&#10;HMI&#9;케이블&#9;Q-5678&#9;연결선&#9;12000&#9;2년"></textarea>
    <button id="applyPasteBtn" class="btn btn-primary btn-sm mt-2">
      <i class="bi bi-clipboard-plus"></i> 붙여넣기 반영
    </button>
  </div>

  <div class="d-flex justify-content-between align-items-center mb-2">
    <div class="form-check">
      <input class="form-check-input" type="checkbox" id="selectAllCheck">
      <label class="form-check-label small text-muted" for="selectAllCheck">전체 선택</label>
    </div>
    <button id="deleteSelectedBtn" class="btn btn-sm btn-outline-danger">
      <i class="bi bi-trash"></i> 선택 삭제
    </button>
  </div>

  <div class="bulk-table-wrap">
    <table class="table bulk-table align-middle mb-0">
      <thead>
        <tr>
          <th style="width:36px"></th>
          <th>원본 유닛명</th>
          <th>유닛 선택 (다중)</th>
          <th>부품이름</th>
          <th>Q-CODE</th>
          <th>부가설명</th>
          <th>금액</th>
          <th>교체주기</th>
          <th>상태</th>
          <th style="width:90px"></th>
        </tr>
      </thead>
      <tbody id="bulkTableBody"></tbody>
    </table>
    <p id="bulkEmptyMsg" class="text-muted text-center py-4 mb-0 d-none">등록 대기 중인 항목이 없습니다.</p>
  </div>

</main>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
let masterUnits = [];
let entries = [];

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s || "";
  return div.innerHTML;
}

function cycleDaysToDisplayValue(cycleDays, cycleUnit) {
  if (cycleUnit === "년") {
    return Math.round((cycleDays / 365) * 100) / 100;
  }
  return cycleDays;
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "요청 처리 중 오류가 발생했습니다");
  }
  return res.status === 204 ? null : res.json();
}

async function loadEntries() {
  const data = await fetchJson("/api/bulk-parts");
  masterUnits = data.master_units;
  entries = data.entries;
  renderTable();
}

function unitMultiselectHtml(entry) {
  const selectedIds = new Set(entry.units.map((u) => u.id));
  const label =
    entry.units.length === 0
      ? "유닛 선택..."
      : entry.units.length === 1
      ? escapeHtml(entry.units[0].name)
      : `${entry.units.length}개 선택`;
  const checkboxes = masterUnits
    .map(
      (u) => `
    <div class="form-check">
      <input class="form-check-input unit-check" type="checkbox" value="${u.id}" id="unitChk-${entry.id}-${u.id}" ${selectedIds.has(u.id) ? "checked" : ""}>
      <label class="form-check-label" for="unitChk-${entry.id}-${u.id}">${escapeHtml(u.name)}</label>
    </div>`
    )
    .join("");
  return `
    <div class="dropdown">
      <button class="btn btn-sm btn-outline-secondary dropdown-toggle w-100 text-truncate" type="button"
        data-bs-toggle="dropdown" data-bs-auto-close="outside">
        ${label}
      </button>
      <div class="dropdown-menu p-2 unit-filter-menu" data-entry-id="${entry.id}">
        ${checkboxes}
      </div>
    </div>`;
}

function updateRowUnitUi(entryId) {
  const row = document.querySelector(`tr[data-entry-id="${entryId}"]`);
  const entry = entries.find((e) => e.id === entryId);
  if (!row || !entry) return;
  const btn = row.querySelector(".dropdown-toggle");
  btn.textContent =
    entry.units.length === 0
      ? "유닛 선택..."
      : entry.units.length === 1
      ? entry.units[0].name
      : `${entry.units.length}개 선택`;
  const registerBtn = row.querySelector(".register-btn");
  if (entry.status !== "registered") {
    registerBtn.disabled = entry.units.length === 0;
  }
}

function renderTable() {
  const tbody = document.getElementById("bulkTableBody");
  const emptyMsg = document.getElementById("bulkEmptyMsg");
  if (entries.length === 0) {
    tbody.innerHTML = "";
    emptyMsg.classList.remove("d-none");
    return;
  }
  emptyMsg.classList.add("d-none");
  tbody.innerHTML = entries
    .map(
      (e) => `
    <tr data-entry-id="${e.id}">
      <td><input type="checkbox" class="form-check-input row-check" data-id="${e.id}"></td>
      <td class="text-muted">${escapeHtml(e.raw_unit_text)}</td>
      <td>${unitMultiselectHtml(e)}</td>
      <td><input type="text" class="form-control form-control-sm field-input" data-id="${e.id}" data-field="part_name" value="${escapeHtml(e.part_name)}"></td>
      <td><input type="text" class="form-control form-control-sm field-input" data-id="${e.id}" data-field="q_code" value="${escapeHtml(e.q_code)}"></td>
      <td><input type="text" class="form-control form-control-sm field-input" data-id="${e.id}" data-field="note" value="${escapeHtml(e.note)}"></td>
      <td><input type="number" class="form-control form-control-sm field-input" data-id="${e.id}" data-field="cost" value="${e.cost || 0}" min="0" step="100"></td>
      <td>
        <div class="input-group input-group-sm bulk-cycle-group">
          <input type="number" class="form-control form-control-sm cycle-field-input" data-id="${e.id}" data-cyclefield="value" value="${cycleDaysToDisplayValue(e.cycle_days || 90, e.cycle_unit || "일")}" min="1" step="1">
          <select class="form-select form-select-sm flex-grow-0 w-auto cycle-field-input" data-id="${e.id}" data-cyclefield="unit">
            <option value="일" ${(e.cycle_unit || "일") === "일" ? "selected" : ""}>일</option>
            <option value="년" ${e.cycle_unit === "년" ? "selected" : ""}>년</option>
          </select>
        </div>
      </td>
      <td>
        ${e.status === "registered"
          ? '<span class="bulk-status-registered"><i class="bi bi-check-circle-fill"></i> 등록됨</span>'
          : '<span class="bulk-status-pending">대기</span>'}
      </td>
      <td>
        <button class="btn btn-sm btn-primary register-btn" data-id="${e.id}" ${e.status === "registered" || e.units.length === 0 ? "disabled" : ""}>
          등록
        </button>
      </td>
    </tr>`
    )
    .join("");

  // "strategy" is not a real data-bs-* option Bootstrap reads (only autoClose,
  // boundary, display, offset, popperConfig, reference are). To actually escape
  // the table wrapper's overflow:hidden clipping, the Popper positioning
  // strategy must be set to "fixed" via the JS popperConfig option instead.
  tbody.querySelectorAll(".dropdown-toggle").forEach((toggleEl) => {
    bootstrap.Dropdown.getOrCreateInstance(toggleEl, {
      popperConfig: (defaultConfig) => ({ ...defaultConfig, strategy: "fixed" }),
    });
  });

  tbody.querySelectorAll(".unit-filter-menu").forEach((menu) => {
    const entryId = parseInt(menu.dataset.entryId, 10);
    const dropdownWrapper = menu.closest(".dropdown");

    menu.querySelectorAll(".unit-check").forEach((cb) => {
      cb.addEventListener("change", async () => {
        // persist immediately, but do NOT reload/re-render here — that would destroy
        // the open dropdown DOM and make it look like it "closed" after one click.
        const selected = Array.from(menu.querySelectorAll(".unit-check:checked")).map((c) =>
          parseInt(c.value, 10)
        );
        await fetchJson(`/api/bulk-parts/${entryId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ unit_ids: selected }),
        });
        const entry = entries.find((e) => e.id === entryId);
        if (entry) {
          entry.units = masterUnits.filter((u) => selected.includes(u.id));
        }
        updateRowUnitUi(entryId);
      });
    });

    // only re-render the full table (to refresh disabled states etc.) once the
    // dropdown is actually closed, i.e. after the user has finished selecting.
    dropdownWrapper.addEventListener("hidden.bs.dropdown", () => {
      renderTable();
    });
  });

  tbody.querySelectorAll(".field-input").forEach((input) => {
    input.addEventListener("change", async () => {
      const id = parseInt(input.dataset.id, 10);
      const value = input.dataset.field === "cost" ? parseFloat(input.value) || 0 : input.value.trim();
      await fetchJson(`/api/bulk-parts/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [input.dataset.field]: value }),
      });
    });
  });

  tbody.querySelectorAll("tr").forEach((row) => {
    const valueInput = row.querySelector('.cycle-field-input[data-cyclefield="value"]');
    const unitSelect = row.querySelector('.cycle-field-input[data-cyclefield="unit"]');
    if (!valueInput || !unitSelect) return;
    const id = parseInt(valueInput.dataset.id, 10);
    const commitCycle = async () => {
      const cycleUnit = unitSelect.value;
      const cycleValue = parseFloat(valueInput.value) || 1;
      const cycleDays = cycleUnit === "년" ? Math.round(cycleValue * 365) : Math.round(cycleValue);
      await fetchJson(`/api/bulk-parts/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cycle_days: cycleDays, cycle_unit: cycleUnit }),
      });
    };
    valueInput.addEventListener("change", commitCycle);
    unitSelect.addEventListener("change", commitCycle);
  });

  tbody.querySelectorAll(".register-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.id, 10);
      try {
        await fetchJson(`/api/bulk-parts/${id}/register`, { method: "POST" });
        await loadEntries();
      } catch (err) {
        alert(err.message);
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadEntries();

  document.getElementById("applyPasteBtn").addEventListener("click", async () => {
    const text = document.getElementById("pasteArea").value;
    if (!text.trim()) return;
    try {
      await fetchJson("/api/bulk-parts/paste", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      document.getElementById("pasteArea").value = "";
      await loadEntries();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("selectAllCheck").addEventListener("change", (e) => {
    document.querySelectorAll(".row-check").forEach((cb) => (cb.checked = e.target.checked));
  });

  document.getElementById("deleteSelectedBtn").addEventListener("click", async () => {
    const ids = Array.from(document.querySelectorAll(".row-check:checked")).map((cb) => cb.dataset.id);
    if (ids.length === 0) {
      alert("삭제할 항목을 선택하세요.");
      return;
    }
    if (!confirm(`선택한 ${ids.length}개 항목을 목록에서 삭제할까요? (이미 등록된 부품 자체는 삭제되지 않습니다)`)) return;
    for (const id of ids) {
      await fetchJson(`/api/bulk-parts/${id}`, { method: "DELETE" });
    }
    document.getElementById("selectAllCheck").checked = false;
    await loadEntries();
  });
});
</script>
</body>
</html>
"""


TRASH_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>휴지통 - 설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <a href="/" class="btn btn-sm btn-outline-light"><i class="bi bi-arrow-left"></i> 대시보드</a>
    <i class="bi bi-trash3-fill"></i>
    <h1>휴지통</h1>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span id="clock" class="clock"></span>
  </div>
</header>

<main class="container-fluid py-4">

  <p class="text-muted small mb-3">
    삭제된 설비/유닛/부품을 복원하거나 영구적으로 삭제할 수 있습니다.
    설비를 복원하면 소속된 유닛과 부품도 함께 복원됩니다. 영구 삭제는 되돌릴 수 없습니다.
  </p>

  <div class="bulk-table-wrap mb-4">
    <div class="p-3 border-bottom"><strong><i class="bi bi-cpu"></i> 삭제된 설비</strong></div>
    <table class="table bulk-table align-middle mb-0">
      <thead>
        <tr><th>이름</th><th>유닛 수</th><th>삭제 일시</th><th style="width:220px"></th></tr>
      </thead>
      <tbody id="trashEquipmentBody"></tbody>
    </table>
    <p id="trashEquipmentEmpty" class="text-muted text-center py-3 mb-0 d-none">삭제된 설비가 없습니다.</p>
  </div>

  <div class="bulk-table-wrap mb-4">
    <div class="p-3 border-bottom"><strong><i class="bi bi-hdd-stack"></i> 삭제된 유닛</strong></div>
    <table class="table bulk-table align-middle mb-0">
      <thead>
        <tr><th>이름</th><th>소속 설비</th><th>삭제 일시</th><th style="width:220px"></th></tr>
      </thead>
      <tbody id="trashUnitBody"></tbody>
    </table>
    <p id="trashUnitEmpty" class="text-muted text-center py-3 mb-0 d-none">삭제된 유닛이 없습니다.</p>
  </div>

  <div class="bulk-table-wrap">
    <div class="p-3 border-bottom"><strong><i class="bi bi-gear"></i> 삭제된 부품</strong></div>
    <table class="table bulk-table align-middle mb-0">
      <thead>
        <tr><th>이름</th><th>소속 유닛</th><th>소속 설비</th><th>삭제 일시</th><th style="width:220px"></th></tr>
      </thead>
      <tbody id="trashPartBody"></tbody>
    </table>
    <p id="trashPartEmpty" class="text-muted text-center py-3 mb-0 d-none">삭제된 부품이 없습니다.</p>
  </div>

</main>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s || "";
  return div.innerHTML;
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "요청 처리 중 오류가 발생했습니다");
  }
  return res.status === 204 ? null : res.json();
}

async function loadTrash() {
  const data = await fetchJson("/api/trash");
  renderEquipments(data.equipments);
  renderUnits(data.units);
  renderParts(data.parts);
}

function actionButtons(type, id, restoreLabel) {
  return `
    <button class="btn btn-sm btn-outline-primary restore-btn" data-type="${type}" data-id="${id}">
      <i class="bi bi-arrow-counterclockwise"></i> ${restoreLabel}
    </button>
    <button class="btn btn-sm btn-outline-danger permanent-delete-btn" data-type="${type}" data-id="${id}">
      <i class="bi bi-trash3"></i> 영구 삭제
    </button>`;
}

function renderEquipments(equipments) {
  const tbody = document.getElementById("trashEquipmentBody");
  const empty = document.getElementById("trashEquipmentEmpty");
  if (equipments.length === 0) {
    tbody.innerHTML = "";
    empty.classList.remove("d-none");
  } else {
    empty.classList.add("d-none");
    tbody.innerHTML = equipments
      .map(
        (e) => `
      <tr>
        <td>${escapeHtml(e.name)}</td>
        <td>${e.unit_count}개</td>
        <td class="text-muted small">${e.deleted_at}</td>
        <td class="d-flex gap-2">${actionButtons("equipment", e.id, "설비 복원")}</td>
      </tr>`
      )
      .join("");
  }
}

function renderUnits(units) {
  const tbody = document.getElementById("trashUnitBody");
  const empty = document.getElementById("trashUnitEmpty");
  if (units.length === 0) {
    tbody.innerHTML = "";
    empty.classList.remove("d-none");
  } else {
    empty.classList.add("d-none");
    tbody.innerHTML = units
      .map(
        (u) => `
      <tr>
        <td>${escapeHtml(u.name)}</td>
        <td class="text-muted">${escapeHtml(u.equipment_name || "-")}</td>
        <td class="text-muted small">${u.deleted_at}</td>
        <td class="d-flex gap-2">${actionButtons("unit", u.id, "유닛 복원")}</td>
      </tr>`
      )
      .join("");
  }
}

function renderParts(parts) {
  const tbody = document.getElementById("trashPartBody");
  const empty = document.getElementById("trashPartEmpty");
  if (parts.length === 0) {
    tbody.innerHTML = "";
    empty.classList.remove("d-none");
  } else {
    empty.classList.add("d-none");
    tbody.innerHTML = parts
      .map(
        (p) => `
      <tr>
        <td>${escapeHtml(p.name)}</td>
        <td class="text-muted">${escapeHtml(p.unit_name || "-")}</td>
        <td class="text-muted">${escapeHtml(p.equipment_name || "-")}</td>
        <td class="text-muted small">${p.deleted_at}</td>
        <td class="d-flex gap-2">${actionButtons("part", p.id, "부품 복원")}</td>
      </tr>`
      )
      .join("");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadTrash();

  document.addEventListener("click", async (e) => {
    const restoreBtn = e.target.closest(".restore-btn");
    if (restoreBtn) {
      const { type, id } = restoreBtn.dataset;
      try {
        await fetchJson(`/api/trash/${type}/${id}/restore`, { method: "POST" });
        await loadTrash();
      } catch (err) {
        alert(err.message);
      }
      return;
    }
    const deleteBtn = e.target.closest(".permanent-delete-btn");
    if (deleteBtn) {
      const { type, id } = deleteBtn.dataset;
      if (!confirm("영구적으로 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")) return;
      try {
        await fetchJson(`/api/trash/${type}/${id}`, { method: "DELETE" });
        await loadTrash();
      } catch (err) {
        alert(err.message);
      }
    }
  });
});
</script>
</body>
</html>
"""


ACTIVITY_LOG_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>변경 이력 - 설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <a href="/" class="btn btn-sm btn-outline-light"><i class="bi bi-arrow-left"></i> 대시보드</a>
    <i class="bi bi-clock-history"></i>
    <h1>변경 이력</h1>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span id="clock" class="clock"></span>
  </div>
</header>

<main class="container-fluid py-4">

  <p class="text-muted small mb-3">설비/유닛/부품에 대한 주요 변경 작업의 이력입니다 (최근 300건). 로그인 시 입력한 이름을 기준으로 기록됩니다.</p>

  <div class="bulk-table-wrap">
    <table class="table bulk-table align-middle mb-0">
      <thead>
        <tr><th>시간</th><th>사용자</th><th>작업</th><th>대상</th><th>상세</th></tr>
      </thead>
      <tbody id="activityLogBody"></tbody>
    </table>
    <p id="activityLogEmpty" class="text-muted text-center py-4 mb-0 d-none">변경 이력이 없습니다.</p>
  </div>

</main>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s || "";
  return div.innerHTML;
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "요청 처리 중 오류가 발생했습니다");
  }
  return res.status === 204 ? null : res.json();
}

const ACTION_LABEL = {
  create: "생성",
  update: "수정",
  delete: "삭제",
  restore: "복원",
  permanent_delete: "영구 삭제",
  replace: "교체 기록",
  backup: "백업",
};

const TARGET_LABEL = {
  equipment: "설비",
  unit: "유닛",
  part: "부품",
  backup: "백업",
};

const ACTION_BADGE = {
  create: "bulk-status-registered",
  update: "text-primary",
  delete: "text-danger",
  restore: "text-success",
  permanent_delete: "text-danger fw-bold",
  replace: "text-primary",
  backup: "text-muted",
};

async function loadActivityLog() {
  const rows = await fetchJson("/api/activity-log");
  const tbody = document.getElementById("activityLogBody");
  const empty = document.getElementById("activityLogEmpty");
  if (rows.length === 0) {
    tbody.innerHTML = "";
    empty.classList.remove("d-none");
    return;
  }
  empty.classList.add("d-none");
  tbody.innerHTML = rows
    .map(
      (r) => `
    <tr>
      <td class="text-muted small">${r.created_at}</td>
      <td>${escapeHtml(r.actor_name || "익명")}</td>
      <td class="${ACTION_BADGE[r.action] || ""}">${ACTION_LABEL[r.action] || r.action}</td>
      <td>${TARGET_LABEL[r.target_type] || r.target_type || "-"}${r.target_name ? " - " + escapeHtml(r.target_name) : ""}</td>
      <td class="text-muted small">${escapeHtml(r.detail || "")}</td>
    </tr>`
    )
    .join("");
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadActivityLog();
});
</script>
</body>
</html>
"""


INVENTORY_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>재고 관리 - 설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <a href="/" class="btn btn-sm btn-outline-light"><i class="bi bi-arrow-left"></i> 대시보드</a>
    <i class="bi bi-boxes"></i>
    <h1>재고 관리</h1>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span id="clock" class="clock"></span>
    <a id="exportInventoryBtn" href="/api/inventory/export" class="btn btn-sm btn-outline-light">
      <i class="bi bi-file-earmark-spreadsheet"></i> 엑셀로 내보내기
    </a>
  </div>
</header>

<main class="container-fluid py-4">

  <div class="d-flex justify-content-between align-items-center mb-3">
    <div class="form-check">
      <input class="form-check-input" type="checkbox" id="lowStockOnlyCheck">
      <label class="form-check-label small text-muted" for="lowStockOnlyCheck">재고 부족(0개)만 보기</label>
    </div>
    <span class="text-muted small">재고는 TEAG01호기 기준으로 전 설비에 동일하게 적용되며, 여기서 바로 수정할 수 있습니다.</span>
  </div>

  <div class="bulk-table-wrap">
    <table class="table bulk-table align-middle mb-0">
      <thead>
        <tr>
          <th>부품이름</th>
          <th>규격</th>
          <th>소속 유닛</th>
          <th style="width:110px">재고 수량</th>
          <th>구매처</th>
          <th>연락처</th>
          <th style="width:100px">리드타임</th>
        </tr>
      </thead>
      <tbody id="inventoryBody"></tbody>
    </table>
    <p id="inventoryEmpty" class="text-muted text-center py-4 mb-0 d-none">등록된 부품이 없습니다.</p>
  </div>

</main>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s || "";
  return div.innerHTML;
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "요청 처리 중 오류가 발생했습니다");
  }
  return res.status === 204 ? null : res.json();
}

let allInventory = [];

async function loadInventory() {
  allInventory = await fetchJson("/api/inventory");
  renderInventory();
}

function updateExportLink() {
  const lowOnly = document.getElementById("lowStockOnlyCheck").checked;
  document.getElementById("exportInventoryBtn").href = `/api/inventory/export${lowOnly ? "?low_only=1" : ""}`;
}

function renderInventory() {
  const lowOnly = document.getElementById("lowStockOnlyCheck").checked;
  updateExportLink();
  const rows = lowOnly ? allInventory.filter((p) => (p.stock_qty || 0) <= 0) : allInventory;
  const tbody = document.getElementById("inventoryBody");
  const empty = document.getElementById("inventoryEmpty");
  if (rows.length === 0) {
    tbody.innerHTML = "";
    empty.classList.remove("d-none");
    empty.textContent = lowOnly ? "재고 부족 부품이 없습니다." : "등록된 부품이 없습니다.";
    return;
  }
  empty.classList.add("d-none");
  tbody.innerHTML = rows
    .map(
      (p) => `
    <tr data-part-id="${p.id}" class="${(p.stock_qty || 0) <= 0 ? "table-danger" : ""}">
      <td>${escapeHtml(p.name)}</td>
      <td class="text-muted">${escapeHtml(p.spec)}</td>
      <td class="text-muted">${escapeHtml(p.unit_name)}</td>
      <td><input type="number" class="form-control form-control-sm stock-qty-input" data-id="${p.id}" value="${p.stock_qty || 0}" min="0" step="1"></td>
      <td class="text-muted">${escapeHtml(p.supplier)}</td>
      <td class="text-muted">${escapeHtml(p.supplier_contact)}</td>
      <td class="text-muted">${p.lead_time_days != null ? p.lead_time_days + "일" : "-"}</td>
    </tr>`
    )
    .join("");

  tbody.querySelectorAll(".stock-qty-input").forEach((input) => {
    input.addEventListener("change", async () => {
      const id = parseInt(input.dataset.id, 10);
      const stock_qty = parseInt(input.value, 10) || 0;
      try {
        await fetchJson(`/api/parts/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stock_qty }),
        });
        const entry = allInventory.find((p) => p.id === id);
        if (entry) entry.stock_qty = stock_qty;
        renderInventory();
      } catch (err) {
        alert(err.message);
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadInventory();

  document.getElementById("lowStockOnlyCheck").addEventListener("change", renderInventory);
});
</script>
</body>
</html>
"""


ROLLOUT_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>횡전개 현황판 - 설비 부품 교체 관리 시스템</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
:root {
  --pri: #4338ca;
  --pri-dark: #362f8c;
  --accent: #6366f1;
  --bg-a: #e5e7eb;
  --bg-b: #f3f4f6;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1e2432;
  --text-muted: #6b7280;
  --radius-lg: 18px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.09);
  --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.14);
}

* { box-sizing: border-box; }

body {
  background: linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard",
    "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  color: var(--text);
}

/* ── 로그인 페이지 ────────────────────────────────────────────── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
  max-width: 380px;
  width: 100%;
  text-align: center;
}
.login-icon {
  font-size: 40px;
  color: var(--pri);
  margin-bottom: 10px;
}
.login-card h1 {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 4px;
}

/* ── 버튼 공통 리스킨 ─────────────────────────────────────────── */
.btn {
  border-radius: var(--radius-sm);
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--pri);
  border-color: var(--pri);
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.35);
}
.btn-primary:hover {
  background: var(--pri-dark);
  border-color: var(--pri-dark);
  box-shadow: 0 4px 14px rgba(67, 56, 202, 0.4);
}
.btn-outline-light {
  border-color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.btn-outline-light:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.6);
  color: #fff;
}
.btn-warning {
  background: #f59e0b;
  border-color: #f59e0b;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4);
}
.btn-warning:hover { background: #d97706; border-color: #d97706; color: #fff; }
.btn-outline-secondary { border-color: var(--border); color: var(--text-muted); }
.btn-outline-secondary:hover { background: #f3f4f6; color: var(--text); }
.btn-outline-primary { color: var(--pri); border-color: var(--pri); }
.btn-outline-primary:hover { background: var(--pri); border-color: var(--pri); }
.btn-outline-danger:hover { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25); }

.form-control:focus, .form-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 0.2rem rgba(99, 102, 241, 0.2);
}

/* ── 상단바 ───────────────────────────────────────────────────── */
.topbar {
  background: linear-gradient(120deg, var(--pri), var(--accent) 130%);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 4px 18px rgba(67, 56, 202, 0.25);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
.topbar i.bi { font-size: 18px; opacity: 0.9; }
.clock { font-size: 12px; opacity: 0.85; font-variant-numeric: tabular-nums; }

/* ── 범례 ─────────────────────────────────────────────────────── */
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 9px 20px;
  box-shadow: var(--shadow-sm);
}
.legend-item { display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 3px currentColor; opacity: 0.9; }
.dot-ok { background: #22c55e; color: rgba(34, 197, 94, 0.18); }
.dot-soon { background: #f59e0b; color: rgba(245, 158, 11, 0.18); }
.dot-overdue { background: #ef4444; color: rgba(239, 68, 68, 0.18); }
.dot-unknown { background: #9ca3af; color: rgba(156, 163, 175, 0.18); }
.legend-edit-btn { font-size: 13px; color: var(--pri); line-height: 1; }
.legend-edit-btn:hover { color: var(--pri); opacity: 0.8; }

/* ── 설비 프레임 / 유닛 도형 프레임 ───────────────────────────── */
.equipment-frame {
  background:
    radial-gradient(circle, rgba(100, 116, 139, 0.14) 1px, transparent 1px),
    linear-gradient(180deg, #fcfcfd, #e9eaed);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid #dcdee2;
  border-radius: var(--radius-lg);
  padding: 30px 22px 22px;
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.12), rgba(245, 158, 11, 0.12));
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: #92400e;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  text-align: center;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 22px;
  background: linear-gradient(120deg, var(--pri), var(--accent));
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 999px;
  box-shadow: 0 3px 10px rgba(67, 56, 202, 0.3);
}

.unit-shape {
  --shape-color: var(--pri);
  border: 3px solid var(--shape-color);
  box-shadow: inset 0 0 0 6px #fff, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 12%, transparent);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 42px;
  display: block;
  line-height: 1.2;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.12));
}
.unit-shape-name {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--shape-color);
}

/* ── 대시보드 그리드 ──────────────────────────────────────────── */
.equipment-canvas.dashboard-canvas {
  max-width: 1300px;
  margin: 10px auto 0;
  min-height: 860px;
}
@media (max-width: 768px) {
  .equipment-canvas.dashboard-canvas { min-height: 1150px; }
}
.equipment-card {
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--pri);
  border-radius: var(--radius-md);
  padding: 20px 10px 14px;
  cursor: pointer;
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}
.equipment-card:hover {
  box-shadow: var(--shadow-lg);
  border-top-color: var(--accent);
  z-index: 5;
}
.equipment-card.edit-mode { cursor: grab; }
.equipment-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.equipment-card .unit-icon { font-size: 30px; }

.align-guide {
  position: absolute;
  background: var(--accent, #f59e0b);
  opacity: 0.9;
  pointer-events: none;
  z-index: 30;
}
.align-guide-v { top: 0; bottom: 0; width: 2px; transform: translateX(-50%); }
.align-guide-h { left: 0; right: 0; height: 2px; transform: translateY(-50%); }

/* ── 캔버스 ───────────────────────────────────────────────────── */
.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

/* ── 유닛/부품 카드 ───────────────────────────────────────────── */
.unit-card {
  --uc: #4338ca;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: var(--surface);
  border: 1.5px solid var(--uc);
  border-radius: var(--radius-md);
  padding: 14px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.18s ease, transform 0.12s ease;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.unit-card:hover {
  box-shadow: var(--shadow-lg);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: var(--shadow-lg);
  z-index: 20;
  transition: none;
}
.unit-icon-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 6px;
  background: #eef0fb;
  background: color-mix(in srgb, var(--uc) 14%, white);
}
.overdue-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(239, 68, 68, 0.4);
}
.soon-badge {
  position: absolute;
  top: -6px;
  left: -8px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  line-height: 17px;
  text-align: center;
  box-shadow: 0 0 0 2px #fff, 0 2px 6px rgba(245, 158, 11, 0.4);
}
.unit-icon { font-size: 22px; display: block; line-height: 1; }
.unit-name { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.3; letter-spacing: -0.01em; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.2);
}
.unit-part-count {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 500;
}
.unit-edit-actions {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: none;
  gap: 4px;
}
.edit-mode .unit-edit-actions { display: flex; }
.unit-edit-actions button {
  border: none;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 7px;
  font-size: 11px;
  padding: 3px 6px;
  transition: background 0.15s;
}
.unit-edit-actions button:hover { background: rgba(15, 23, 42, 0.14); }

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  display: none;
  cursor: nwse-resize;
}
.edit-mode .resize-handle { display: block; }
.resize-handle::before {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 9px;
  height: 9px;
  border-right: 2px solid rgba(67, 56, 202, 0.45);
  border-bottom: 2px solid rgba(67, 56, 202, 0.45);
}

.resize-handle-h {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 30px;
  display: none;
  cursor: ew-resize;
}
.edit-mode .resize-handle-h { display: block; }
.resize-handle-h::before {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: rgba(67, 56, 202, 0.4);
}

.canvas-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

/* ── 부품 목록/배지 ───────────────────────────────────────────── */
.part-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 15px;
  margin-bottom: 10px;
  background: #fafbfe;
  transition: box-shadow 0.15s;
}
.part-card:hover { box-shadow: var(--shadow-sm); }
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: var(--text-muted); }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 7px 0; }

/* ── 메모장 ───────────────────────────────────────────────────── */
.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.notes-view {
  min-height: 60px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
}
.notes-view:empty::before,
.notes-view.is-empty::before {
  content: "등록된 메모가 없습니다. \"편집\" 버튼을 눌러 설비 정보나 부품 구매처 링크를 기록해보세요.";
  color: #9ca3af;
}
.notes-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}
#notesEdit { font-size: 14px; }

/* ── 리치 메모/노트 (엑셀 표 붙여넣기 서식 유지) ─────────────────────── */
.rich-edit {
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.rich-edit:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
}
.notes-view table,
.part-memo-view table,
.rich-edit table {
  border-collapse: collapse;
  margin: 6px 0;
  max-width: 100%;
}
.notes-view td, .notes-view th,
.part-memo-view td, .part-memo-view th,
.rich-edit td, .rich-edit th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 13px;
}
.notes-view th, .part-memo-view th, .rich-edit th { background: #f3f4f6; font-weight: 700; }
.rich-edit table td, .rich-edit table th { cursor: text; }

.table-edit-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  gap: 3px;
  background: #1f2937;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.table-edit-toolbar button {
  background: transparent;
  border: none;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.table-edit-toolbar button:hover { background: rgba(255, 255, 255, 0.18); }
.table-edit-toolbar button[data-action^="del-"] { color: #fca5a5; }

.part-memo-section { border-top: 1px solid var(--border); padding-top: 12px; }
.part-memo-view {
  min-height: 32px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: #333;
  line-height: 1.6;
}
.part-memo-view:empty::before,
.part-memo-view.is-empty::before {
  content: "등록된 메모가 없습니다.";
  color: #9ca3af;
}
.part-memo-view a {
  color: var(--pri);
  word-break: break-all;
  font-weight: 500;
}

/* ── 부품 도면 ────────────────────────────────────────────────── */
.part-drawing-paste {
  margin-top: 8px;
  min-height: 90px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-sm);
  background: #f8f9fc;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  cursor: text;
}
.part-drawing-paste:focus { outline: none; border-color: var(--pri); }
.part-drawing-placeholder { color: #9ca3af; font-size: 13px; text-align: center; }
.part-drawing-preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-sm);
}
.drawing-modal-img { max-width: 100%; max-height: 75vh; }

/* ── 모달 리스킨 ──────────────────────────────────────────────── */
.modal-content { border: none; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.modal-header { border-bottom: 1px solid var(--border); padding: 18px 22px; }
.modal-title { font-weight: 700; letter-spacing: -0.01em; }
.modal-body { padding: 20px 22px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-muted); }

/* ── 아이콘 선택기 ────────────────────────────────────────────── */
.icon-picker {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
  margin-top: 6px;
  padding: 10px;
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 168px;
  overflow-y: auto;
}
.icon-choice {
  width: 34px;
  height: 34px;
  border: 1.5px solid transparent;
  border-radius: 9px;
  background: #fff;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.icon-choice:hover { background: #eef0fb; transform: translateY(-1px); }
.icon-choice.selected {
  border-color: var(--pri);
  background: color-mix(in srgb, var(--pri) 12%, white);
  box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.18);
}

/* ── 대시보드 검색/정렬 툴바 ──────────────────────────────────── */
.dashboard-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  max-width: 1100px;
  margin: 0 auto 16px;
}
.dashboard-toolbar .search-box {
  position: relative;
  flex: 1;
  min-width: 200px;
  max-width: 320px;
}
.dashboard-toolbar .search-box i {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
}
.dashboard-toolbar .search-box input {
  padding-left: 34px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
}
.dashboard-toolbar select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  padding: 6px 14px;
}
.nav-badge {
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  margin-left: 2px;
}

/* ── 전체 교체 현황 목록 ──────────────────────────────────────── */
.alerts-list {
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: #f8f9fd; }
.alert-badge { flex-shrink: 0; min-width: 66px; text-align: center; }
.alert-main { flex: 1; min-width: 0; }
.alert-title { font-size: 14px; font-weight: 600; color: var(--text); }
.alert-sep { color: var(--text-muted); margin: 0 2px; }
.alert-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.alert-chevron { color: var(--text-muted); flex-shrink: 0; }

/* ── 통계 페이지 ───────────────────────────────────────────────── */
.unit-filter-menu {
  max-height: 320px;
  overflow-y: auto;
  min-width: 240px;
}
.unit-filter-menu .form-check { padding-left: 1.6em; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  max-width: 1300px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
}
.stats-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 0 6px;
}
.stats-panel h6 {
  font-weight: 700;
  padding: 0 18px 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f6;
}
.stats-subtitle {
  font-weight: 500;
  font-size: 11px;
  color: var(--text-muted);
}
.stats-list {
  max-height: 380px;
  overflow-y: auto;
}
.stats-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid #f1f2f6;
  cursor: pointer;
  transition: background 0.12s;
}
.stats-row:last-child { border-bottom: none; }
.stats-row:hover { background: #f8f9fd; }
.stats-rank {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--pri) 12%, white);
  color: var(--pri);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 부품 일괄 등록 페이지 ────────────────────────────────────── */
.master-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px 10px;
  margin-left: 8px;
  vertical-align: middle;
}
.bulk-paste-box {
  max-width: 1300px;
  margin: 0 auto 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
}
.bulk-paste-box textarea { font-family: ui-monospace, monospace; font-size: 13px; }
.bulk-table-wrap {
  max-width: 1300px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.bulk-table { margin-bottom: 0; font-size: 13.5px; }
.bulk-table thead th {
  background: #f8f9fd;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.bulk-table td { vertical-align: middle; }
.bulk-table .form-select-sm, .bulk-table .form-control-sm { font-size: 12.5px; }
.bulk-cycle-group { min-width: 105px; }
.bulk-cycle-group input { width: 55px; flex: 0 0 auto; }
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }

/* ══ 사이버틱 테마 ══════════════════════════════════════════════
   html[data-theme="cyber"]가 붙으면 전체 화면이 네온/다크 스타일로 전환된다.
   모든 효과는 정적 CSS(변수 재정의 + 색상 오버라이드)로만 구현되어 있어
   애니메이션/필터 등 렌더링 비용이 발생하는 요소가 없다. */
html[data-theme="cyber"] {
  --pri: #06b6d4;
  --pri-dark: #0891b2;
  --accent: #d946ef;
  --bg-a: #060913;
  --bg-b: #0b1022;
  --surface: #0f1629;
  --border: #1e335c;
  --text: #d7e4f5;
  --text-muted: #7c93b5;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.55), 0 0 12px rgba(6, 182, 212, 0.07);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 20px rgba(6, 182, 212, 0.1);
}
html[data-theme="cyber"] body {
  background:
    radial-gradient(circle at 20% 0%, rgba(6, 182, 212, 0.08), transparent 45%),
    radial-gradient(circle at 80% 100%, rgba(217, 70, 239, 0.06), transparent 45%),
    linear-gradient(180deg, var(--bg-a), var(--bg-b) 320px);
  background-attachment: fixed;
}
html[data-theme="cyber"] .topbar {
  background: linear-gradient(120deg, #0b1428, #101a35 60%, #1a1033);
  border-bottom: 1px solid rgba(6, 182, 212, 0.45);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.5), 0 1px 12px rgba(6, 182, 212, 0.15);
}
html[data-theme="cyber"] .topbar h1 {
  background: linear-gradient(90deg, #22d3ee, #e879f9);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
html[data-theme="cyber"] .equipment-frame {
  background:
    radial-gradient(circle, rgba(6, 182, 212, 0.16) 1px, transparent 1px),
    linear-gradient(180deg, #0c1327, #090e1e);
  background-size: 22px 22px, 100% 100%;
  border: 1px solid var(--border);
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md);
}
html[data-theme="cyber"] .unit-shape {
  box-shadow: inset 0 0 0 6px #0a101f, var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--shape-color) 22%, transparent);
}
html[data-theme="cyber"] .unit-icon-wrap {
  background: #14203c;
  background: color-mix(in srgb, var(--uc) 24%, #0d1428);
}
html[data-theme="cyber"] .overdue-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(239, 68, 68, 0.7); }
html[data-theme="cyber"] .soon-badge { box-shadow: 0 0 0 2px #0f1629, 0 0 8px rgba(245, 158, 11, 0.7); }
html[data-theme="cyber"] .dot-ok { box-shadow: 0 0 0 3px currentColor, 0 0 8px #22c55e; }
html[data-theme="cyber"] .dot-soon { box-shadow: 0 0 0 3px currentColor, 0 0 8px #f59e0b; }
html[data-theme="cyber"] .dot-overdue { box-shadow: 0 0 0 3px currentColor, 0 0 8px #ef4444; }
html[data-theme="cyber"] .part-card { background: #0d1428; }
html[data-theme="cyber"] .badge-ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
html[data-theme="cyber"] .badge-soon { background: rgba(245, 158, 11, 0.16); color: #fbbf24; }
html[data-theme="cyber"] .badge-overdue { background: rgba(239, 68, 68, 0.18); color: #f87171; }
html[data-theme="cyber"] .badge-unknown { background: rgba(148, 163, 184, 0.16); color: #94a3b8; }
html[data-theme="cyber"] .history-row { border-bottom-color: var(--border); }
html[data-theme="cyber"] .notes-view,
html[data-theme="cyber"] .part-memo-view { color: var(--text); }
html[data-theme="cyber"] .notes-view th,
html[data-theme="cyber"] .part-memo-view th,
html[data-theme="cyber"] .rich-edit th { background: #14203c; }
html[data-theme="cyber"] .master-hint {
  background: linear-gradient(120deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.1));
  color: #fbbf24;
}
html[data-theme="cyber"] .btn-outline-secondary { color: #9fb4d6; border-color: #2a4470; }
html[data-theme="cyber"] .btn-outline-secondary:hover { background: #1a2a4d; color: var(--text); border-color: #2a4470; }
html[data-theme="cyber"] .icon-picker { background: #0c1327; }
html[data-theme="cyber"] .icon-choice { background: #14203c; }
html[data-theme="cyber"] .icon-choice:hover { background: #1c2c52; }
html[data-theme="cyber"] .icon-choice.selected { background: color-mix(in srgb, var(--pri) 24%, #0d1428); }
html[data-theme="cyber"] .part-drawing-paste { background: #0c1327; }
html[data-theme="cyber"] .alert-row:hover,
html[data-theme="cyber"] .stats-row:hover { background: #14203c; }
html[data-theme="cyber"] .bulk-table thead th { background: #0c1327; }
html[data-theme="cyber"] .bulk-status-registered { color: #4ade80; }

/* 사이버틱: 부트스트랩 기본 컴포넌트(모달/폼/드롭다운/테이블) 다크화 */
html[data-theme="cyber"] .modal-content { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
html[data-theme="cyber"] .btn-close { filter: invert(1) brightness(1.6); }
html[data-theme="cyber"] .form-control,
html[data-theme="cyber"] .form-select {
  background-color: #0c1327;
  border-color: #2a4470;
  color: var(--text);
}
html[data-theme="cyber"] .form-control:focus,
html[data-theme="cyber"] .form-select:focus {
  background-color: #0c1327;
  color: var(--text);
  border-color: var(--pri);
  box-shadow: 0 0 0 0.2rem rgba(6, 182, 212, 0.25);
}
html[data-theme="cyber"] .form-control::placeholder { color: #5a7196; }
html[data-theme="cyber"] .dropdown-menu { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
html[data-theme="cyber"] .dropdown-item { color: var(--text); }
html[data-theme="cyber"] .dropdown-item:hover { background: #14203c; color: var(--text); }
html[data-theme="cyber"] .table { color: var(--text); border-color: var(--border); --bs-table-bg: transparent; --bs-table-color: var(--text); --bs-table-border-color: var(--border); }
html[data-theme="cyber"] .table-danger { --bs-table-bg: rgba(239, 68, 68, 0.14); --bs-table-color: #fca5a5; }
html[data-theme="cyber"] .text-muted { color: var(--text-muted) !important; }
html[data-theme="cyber"] .form-check-input { background-color: #0c1327; border-color: #2a4470; }
html[data-theme="cyber"] .form-check-input:checked { background-color: var(--pri); border-color: var(--pri); }
html[data-theme="cyber"] .rich-edit:empty::before,
html[data-theme="cyber"] .notes-view:empty::before,
html[data-theme="cyber"] .notes-view.is-empty::before,
html[data-theme="cyber"] .part-memo-view:empty::before,
html[data-theme="cyber"] .part-memo-view.is-empty::before,
html[data-theme="cyber"] .part-drawing-placeholder { color: #5a7196; }

/* ── 횡전개 현황판 ────────────────────────────────────────────── */
.rollout-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  max-width: 1300px;
  margin: 0 auto;
}
.rollout-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 12px;
  display: flex;
  flex-direction: column;
}
.rollout-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.rollout-title { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; word-break: break-word; }
.rollout-card-body { text-align: center; }
.gauge-svg { width: 190px; max-width: 100%; display: block; margin: 0 auto; }
.gauge-track { stroke: var(--border); }
.gauge-pct { font-size: 21px; font-weight: 800; fill: var(--text); }
.rollout-counts {
  display: flex;
  justify-content: center;
  gap: 14px;
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
  flex-wrap: wrap;
}
.rollout-count-done { color: #16a34a; }
.rollout-count-pending { color: #d97706; }
.rollout-data {
  margin-top: 12px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  overflow-x: auto;
  font-size: 13px;
}
.rollout-data table { border-collapse: collapse; margin: 0 auto; }
.rollout-data td, .rollout-data th { border: 1px solid var(--border); padding: 4px 8px; font-size: 12.5px; }
.rollout-data tr:first-child td, .rollout-data th { background: #f3f4f6; font-weight: 700; }
.rollout-data tr td:first-child { background: #f8f9fd; font-weight: 600; }
.rollout-updated { font-size: 11.5px; margin-top: 10px; text-align: right; }

html[data-theme="cyber"] .rollout-count-done { color: #4ade80; }
html[data-theme="cyber"] .rollout-count-pending { color: #fbbf24; }
html[data-theme="cyber"] .rollout-data tr:first-child td,
html[data-theme="cyber"] .rollout-data th { background: #14203c; }
html[data-theme="cyber"] .rollout-data tr td:first-child { background: #0c1327; }

/* 횡전개 데이터 팝업 (게이지 클릭) */
.rollout-card-body { cursor: pointer; border-radius: var(--radius-sm); transition: background 0.12s ease; }
.rollout-card-body:hover { background: color-mix(in srgb, var(--pri) 5%, transparent); }
.rollout-data-modal-table { overflow-x: auto; }
.rollout-data-modal-table table { border-collapse: collapse; margin: 0 auto; }
.rollout-data-modal-table td, .rollout-data-modal-table th {
  border: 1px solid var(--border);
  padding: 4px 8px;
  font-size: 12.5px;
}
.rollout-data-modal-table tr:first-child td,
.rollout-data-modal-table tr:first-child th { background: #f3f4f6; font-weight: 700; }
.rollout-data-modal-table tr td:nth-child(2) { background: #f8f9fd; font-weight: 600; }
.rollout-data-modal-table .row-select-cell { text-align: center; width: 34px; background: transparent !important; }
.rollout-data-modal-table .row-select { cursor: pointer; }
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child td,
html[data-theme="cyber"] .rollout-data-modal-table tr:first-child th { background: #14203c; }
html[data-theme="cyber"] .rollout-data-modal-table tr td:nth-child(2) { background: #0c1327; }
</style>
<script>
// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
</script>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <a href="/" class="btn btn-sm btn-outline-light"><i class="bi bi-arrow-left"></i> 대시보드</a>
    <i class="bi bi-clipboard2-check"></i>
    <h1>횡전개 현황판</h1>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span id="clock" class="clock"></span>
    <button id="addRolloutBtn" class="btn btn-sm btn-outline-light">
      <i class="bi bi-plus-lg"></i> 항목 추가
    </button>
  </div>
</header>

<main class="container-fluid py-4">

  <p class="text-muted small text-center mb-3">
    엑셀에서 [1열 호기 / 2열 CH 이름 / 3열 진행 날짜] 형식의 표를 복사해 붙여넣으면 (1행은 제목 행),
    CH 이름이 있는 행만 집계되어 진행 날짜가 입력된 행은 완료·빈칸은 미진행으로 항목별 게이지에 표시됩니다.
    붙여넣은 원본 데이터는 "데이터 보기"를 눌러야 나타납니다.
  </p>

  <div id="rolloutList" class="rollout-list"></div>
  <p id="rolloutEmpty" class="text-muted text-center py-5 mb-0 d-none">
    등록된 횡전개 항목이 없습니다. 우측 상단 "항목 추가" 버튼으로 시작하세요.
  </p>

</main>

<!-- 항목 추가/편집 모달 -->
<div class="modal fade" id="rolloutEditModal" tabindex="-1">
  <div class="modal-dialog modal-lg">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="rolloutEditTitle">횡전개 항목 추가</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <form id="rolloutEditForm">
          <input type="hidden" id="rolloutEditId">
          <div class="mb-2">
            <label class="form-label">제목</label>
            <input type="text" class="form-control" id="rolloutEditName" placeholder="예: OO 부품 개선 횡전개" required>
          </div>
          <div class="mb-2">
            <label class="form-label">진행 현황 표 (엑셀에서 복사해 붙여넣기)</label>
            <div id="rolloutEditData" class="form-control rich-edit" contenteditable="true" style="min-height: 180px;"
              data-placeholder="엑셀 표를 붙여넣으세요. 1행은 제목 행(1~2열 횡전개 제목, 3열 '진행 날짜'), 2행부터 1열=호기, 2열=CH 이름, 3열=진행 날짜로 인식됩니다. CH 이름이 있는 행만 집계되며, 진행 날짜가 있으면 완료 / 빈칸은 미진행으로 카운트됩니다."></div>
          </div>
          <button type="submit" class="btn btn-primary w-100 mt-2">저장</button>
        </form>
      </div>
    </div>
  </div>
</div>

<!-- 데이터 팝업 (게이지 클릭 시): 행 선택 삭제 + 누적 붙여넣기 -->
<div class="modal fade" id="rolloutDataModal" tabindex="-1">
  <div class="modal-dialog modal-lg">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="rolloutDataTitle">데이터</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <div class="d-flex justify-content-between align-items-center mb-2">
          <span class="text-muted small">행 앞의 체크박스를 선택해 삭제할 수 있습니다.</span>
          <button id="deleteRowsBtn" class="btn btn-sm btn-outline-danger">
            <i class="bi bi-trash3"></i> 선택 행 삭제
          </button>
        </div>
        <div id="rolloutDataTableWrap" class="rollout-data-modal-table"></div>
        <div class="mt-3 pt-3 border-top">
          <label class="form-label">데이터 누적 추가 (엑셀에서 행을 복사해 붙여넣기)</label>
          <div id="rolloutAppendData" class="form-control rich-edit" contenteditable="true" style="min-height: 80px;"
            data-placeholder="추가할 행(1열=호기, 2열=CH 이름, 3열=진행 날짜)을 엑셀에서 복사해 붙여넣으세요. 기존 표 아래에 그대로 누적됩니다. (제목 행이 함께 붙여넣어졌다면 추가 후 선택 삭제로 지우면 됩니다)"></div>
          <button id="appendRowsBtn" class="btn btn-sm btn-primary mt-2">
            <i class="bi bi-plus-lg"></i> 누적 추가
          </button>
        </div>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s || "";
  return div.innerHTML;
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "요청 처리 중 오류가 발생했습니다");
  }
  return res.status === 204 ? null : res.json();
}

// ── 리치 붙여넣기 (엑셀 표 서식 유지) — 다른 페이지와 동일한 화이트리스트 정제 ──
const RICH_ALLOWED_TAGS = {
  TABLE: [], THEAD: [], TBODY: [], TFOOT: [], TR: [], COL: [], COLGROUP: [], CAPTION: [],
  TH: ["colspan", "rowspan"], TD: ["colspan", "rowspan"],
  B: [], STRONG: [], I: [], EM: [], U: [], BR: [], P: [], DIV: [], SPAN: [],
  UL: [], OL: [], LI: [], A: ["href"],
};
const RICH_STRIP_TAGS = new Set([
  "SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "LINK", "META", "SVG",
  "FORM", "IMG", "INPUT", "BUTTON", "TEXTAREA", "SELECT", "VIDEO", "AUDIO", "SOURCE",
]);

function sanitizeRichNode(node) {
  Array.from(node.childNodes).forEach((child) => {
    if (child.nodeType === Node.COMMENT_NODE) {
      child.remove();
      return;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) return;
    const tag = child.tagName;
    if (RICH_STRIP_TAGS.has(tag)) {
      child.remove();
      return;
    }
    const allowed = RICH_ALLOWED_TAGS[tag];
    if (!allowed) {
      sanitizeRichNode(child);
      while (child.firstChild) node.insertBefore(child.firstChild, child);
      child.remove();
      return;
    }
    Array.from(child.attributes).forEach((attr) => {
      if (!allowed.includes(attr.name)) child.removeAttribute(attr.name);
    });
    if (tag === "A") {
      const href = child.getAttribute("href") || "";
      if (!/^https?:\/\//i.test(href)) {
        child.removeAttribute("href");
      } else {
        child.setAttribute("target", "_blank");
        child.setAttribute("rel", "noopener noreferrer");
      }
    }
    sanitizeRichNode(child);
  });
}

function sanitizeRichHtml(rawHtml) {
  const container = document.createElement("div");
  container.innerHTML = rawHtml || "";
  sanitizeRichNode(container);
  return container.innerHTML;
}

function attachRichPasteHandler(el) {
  if (!el || el.dataset.richPasteBound) return;
  el.dataset.richPasteBound = "1";
  el.addEventListener("paste", (e) => {
    e.preventDefault();
    const html = e.clipboardData.getData("text/html");
    const text = e.clipboardData.getData("text/plain");
    if (html) {
      document.execCommand("insertHTML", false, sanitizeRichHtml(html));
    } else if (text) {
      document.execCommand("insertText", false, text);
    }
  });
}

// ── 표를 병합 셀(rowspan/colspan)까지 반영한 2차원 격자로 펼친다.
//    엑셀에서 호기 칸이 세로 병합된 채 복사돼도 열 위치가 어긋나지 않게 하기 위함 ──
function tableToGrid(table) {
  const grid = [];
  Array.from(table.querySelectorAll("tr")).forEach((tr, r) => {
    grid[r] = grid[r] || [];
    let c = 0;
    Array.from(tr.children).forEach((cell) => {
      while (grid[r][c] !== undefined) c++;
      const colspan = parseInt(cell.getAttribute("colspan") || "1", 10) || 1;
      const rowspan = parseInt(cell.getAttribute("rowspan") || "1", 10) || 1;
      const text = cell.textContent.trim();
      for (let dr = 0; dr < rowspan; dr++) {
        for (let dc = 0; dc < colspan; dc++) {
          grid[r + dr] = grid[r + dr] || [];
          grid[r + dr][c + dc] = text;
        }
      }
      c += colspan;
    });
  });
  return grid;
}

// ── 진행 현황 집계 (열 형식 기준):
//    1행 = 제목 행(1~2열 횡전개 제목, 3열 '진행 날짜' 제목)이므로 건너뛰고,
//    2행부터 1열 = 호기, 2열 = CH 이름, 3열 = 진행 날짜로 본다.
//    CH 이름(2열)이 있는 행만 집계 대상이며, 그 행의 진행 날짜(3열)에
//    내용이 있으면 완료, 빈칸이면 미진행으로 센다 ─────────────────────────
function computeProgress(dataHtml) {
  const container = document.createElement("div");
  container.innerHTML = dataHtml || "";
  const table = container.querySelector("table");
  if (!table) return { done: 0, pending: 0, total: 0 };
  const grid = tableToGrid(table);
  let done = 0;
  let pending = 0;
  for (let r = 1; r < grid.length; r++) {
    const row = grid[r] || [];
    const chName = (row[1] || "").trim();
    if (!chName) continue;
    if ((row[2] || "").trim()) done++;
    else pending++;
  }
  return { done, pending, total: done + pending };
}

// 반원형 게이지 (정적 SVG — 애니메이션 없음)
function gaugeHtml(pct) {
  const ARC_LEN = 157.08; // 반지름 50 반원 둘레
  const dash = (Math.max(0, Math.min(100, pct)) / 100) * ARC_LEN;
  const color = pct >= 100 ? "#22c55e" : pct >= 50 ? "#4338ca" : "#f59e0b";
  return `
    <svg viewBox="0 0 120 70" class="gauge-svg" role="img" aria-label="진행률 ${pct}%">
      <path d="M 10 62 A 50 50 0 0 1 110 62" fill="none" class="gauge-track" stroke-width="11" stroke-linecap="round"/>
      <path d="M 10 62 A 50 50 0 0 1 110 62" fill="none" stroke="${color}" stroke-width="11" stroke-linecap="round"
        stroke-dasharray="${dash.toFixed(2)} ${ARC_LEN.toFixed(2)}"/>
      <text x="60" y="56" text-anchor="middle" class="gauge-pct">${pct}%</text>
    </svg>`;
}

let allItems = [];
let rolloutEditModal;
let rolloutDataModal;
let currentDataItemId = null;

async function loadItems() {
  allItems = await fetchJson("/api/rollout");
  renderItems();
}

// ── 데이터 팝업: 게이지 클릭 시 열리며, 셀 직접 수정·행 선택 삭제·누적 붙여넣기를 지원 ──
function renderDataModalTable(item) {
  const wrap = document.getElementById("rolloutDataTableWrap");
  const container = document.createElement("div");
  container.innerHTML = item.data_html || "";
  const table = container.querySelector("table");
  if (!table) {
    wrap.innerHTML = '<p class="text-muted small mb-0">붙여넣은 데이터가 없습니다. 아래에서 행을 추가해보세요.</p>';
    return;
  }
  Array.from(table.querySelectorAll("tr")).forEach((tr, i) => {
    const cell = document.createElement(i === 0 ? "th" : "td");
    cell.className = "row-select-cell";
    if (i > 0) {
      cell.innerHTML = `<input type="checkbox" class="form-check-input row-select" data-row-index="${i}">`;
    }
    tr.insertBefore(cell, tr.firstChild);
  });
  // 셀을 클릭해 날짜를 바로 기입/수정할 수 있게 한다 (체크박스 칸 제외)
  table.querySelectorAll("td, th").forEach((cell) => {
    if (!cell.classList.contains("row-select-cell")) cell.setAttribute("contenteditable", "true");
  });
  wrap.innerHTML = "";
  wrap.appendChild(table);
}

function openDataModal(item) {
  currentDataItemId = item.id;
  document.getElementById("rolloutDataTitle").textContent = `${item.title} — 데이터`;
  document.getElementById("rolloutAppendData").innerHTML = "";
  renderDataModalTable(item);
  rolloutDataModal.show();
}

// ── 팝업 표 직접 편집: 날짜를 기입하면 잠시 후 자동 저장되고 게이지에 즉시 반영 ──
let dataEditSaveTimer = null;
let dataEditDirty = false;

function serializeDataModalTable() {
  const table = document.querySelector("#rolloutDataTableWrap table");
  if (!table) return "";
  const clone = table.cloneNode(true);
  clone.querySelectorAll(".row-select-cell").forEach((c) => c.remove());
  clone.querySelectorAll("[contenteditable]").forEach((c) => c.removeAttribute("contenteditable"));
  const div = document.createElement("div");
  div.appendChild(clone);
  return sanitizeRichHtml(div.innerHTML);
}

async function flushDataEdit() {
  if (!dataEditDirty || currentDataItemId == null) return;
  dataEditDirty = false;
  clearTimeout(dataEditSaveTimer);
  try {
    await saveDataHtml(currentDataItemId, serializeDataModalTable());
  } catch (err) {
    alert(err.message);
  }
}

async function saveDataHtml(itemId, dataHtml) {
  const updated = await fetchJson(`/api/rollout/${itemId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data_html: dataHtml }),
  });
  const idx = allItems.findIndex((x) => x.id === itemId);
  if (idx >= 0) allItems[idx] = updated;
  renderItems();
  return updated;
}

function renderItems() {
  const list = document.getElementById("rolloutList");
  const empty = document.getElementById("rolloutEmpty");
  if (allItems.length === 0) {
    list.innerHTML = "";
    empty.classList.remove("d-none");
    return;
  }
  empty.classList.add("d-none");
  list.innerHTML = allItems
    .map((item) => {
      const p = computeProgress(item.data_html);
      const pct = p.total > 0 ? Math.round((p.done / p.total) * 100) : 0;
      return `
      <div class="rollout-card" data-item-id="${item.id}">
        <div class="rollout-card-head">
          <div class="rollout-title">${escapeHtml(item.title)}</div>
          <div class="d-flex align-items-center gap-1">
            <button class="btn btn-sm btn-outline-secondary toggle-data-btn">
              <i class="bi bi-eye"></i> 데이터 보기
            </button>
            <button class="btn btn-sm btn-outline-secondary edit-item-btn" title="편집"><i class="bi bi-pencil"></i></button>
            <button class="btn btn-sm btn-outline-secondary delete-item-btn" title="삭제"><i class="bi bi-trash3"></i></button>
          </div>
        </div>
        <div class="rollout-card-body">
          ${gaugeHtml(pct)}
          <div class="rollout-counts">
            <span class="rollout-count-done"><i class="bi bi-check-circle-fill"></i> 완료 ${p.done}건</span>
            <span class="rollout-count-pending"><i class="bi bi-circle"></i> 미진행 ${p.pending}건</span>
            <span class="text-muted small">전체 ${p.total}건</span>
          </div>
        </div>
        <div class="rollout-data d-none">${item.data_html || '<p class="text-muted small mb-0">붙여넣은 데이터가 없습니다.</p>'}</div>
        <div class="rollout-updated text-muted">최종 수정: ${item.updated_at || item.created_at || ""}</div>
      </div>`;
    })
    .join("");

  allItems.forEach((item) => {
    const card = list.querySelector(`[data-item-id="${item.id}"]`);
    const gaugeArea = card.querySelector(".rollout-card-body");
    gaugeArea.title = "클릭하면 데이터 팝업이 열립니다";
    gaugeArea.addEventListener("click", () => openDataModal(item));
    const dataDiv = card.querySelector(".rollout-data");
    const toggleBtn = card.querySelector(".toggle-data-btn");
    toggleBtn.addEventListener("click", () => {
      const hidden = dataDiv.classList.toggle("d-none");
      toggleBtn.innerHTML = hidden
        ? '<i class="bi bi-eye"></i> 데이터 보기'
        : '<i class="bi bi-eye-slash"></i> 데이터 숨기기';
    });
    card.querySelector(".edit-item-btn").addEventListener("click", () => openEditModal(item));
    card.querySelector(".delete-item-btn").addEventListener("click", async () => {
      if (!confirm(`"${item.title}" 항목을 삭제할까요?`)) return;
      try {
        await fetchJson(`/api/rollout/${item.id}`, { method: "DELETE" });
        loadItems();
      } catch (err) {
        alert(err.message);
      }
    });
  });
}

function openEditModal(item) {
  document.getElementById("rolloutEditTitle").textContent = item ? "횡전개 항목 편집" : "횡전개 항목 추가";
  document.getElementById("rolloutEditId").value = item ? item.id : "";
  document.getElementById("rolloutEditName").value = item ? item.title : "";
  document.getElementById("rolloutEditData").innerHTML = item ? item.data_html || "" : "";
  rolloutEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  rolloutEditModal = new bootstrap.Modal(document.getElementById("rolloutEditModal"));
  rolloutDataModal = new bootstrap.Modal(document.getElementById("rolloutDataModal"));
  tick();
  setInterval(tick, 1000);
  loadItems();

  attachRichPasteHandler(document.getElementById("rolloutEditData"));
  attachRichPasteHandler(document.getElementById("rolloutAppendData"));
  document.getElementById("addRolloutBtn").addEventListener("click", () => openEditModal(null));

  // 팝업 표의 셀에 날짜를 기입하면 잠시 후 자동 저장 → 게이지 카운트 즉시 반영
  document.getElementById("rolloutDataTableWrap").addEventListener("input", (e) => {
    if (e.target && e.target.classList && e.target.classList.contains("row-select")) return;
    dataEditDirty = true;
    clearTimeout(dataEditSaveTimer);
    dataEditSaveTimer = setTimeout(flushDataEdit, 600);
  });
  // 저장 전에 팝업을 닫아도 수정 내용이 유실되지 않도록 닫힐 때 즉시 저장
  document.getElementById("rolloutDataModal").addEventListener("hide.bs.modal", () => {
    flushDataEdit();
  });

  document.getElementById("deleteRowsBtn").addEventListener("click", async () => {
    await flushDataEdit();
    const item = allItems.find((x) => x.id === currentDataItemId);
    if (!item) return;
    const checked = Array.from(
      document.querySelectorAll("#rolloutDataTableWrap .row-select:checked")
    ).map((cb) => parseInt(cb.dataset.rowIndex, 10));
    if (checked.length === 0) {
      alert("삭제할 행을 먼저 선택하세요.");
      return;
    }
    if (!confirm(`선택한 ${checked.length}개 행을 삭제할까요?`)) return;
    const container = document.createElement("div");
    container.innerHTML = item.data_html || "";
    const table = container.querySelector("table");
    if (!table) return;
    const rows = Array.from(table.querySelectorAll("tr"));
    checked.sort((a, b) => b - a).forEach((i) => {
      if (rows[i]) rows[i].remove();
    });
    if (!table.querySelector("tr")) table.remove();
    try {
      const updated = await saveDataHtml(item.id, container.innerHTML);
      renderDataModalTable(updated);
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("appendRowsBtn").addEventListener("click", async () => {
    await flushDataEdit();
    const item = allItems.find((x) => x.id === currentDataItemId);
    if (!item) return;
    const pastedContainer = document.createElement("div");
    pastedContainer.innerHTML = sanitizeRichHtml(document.getElementById("rolloutAppendData").innerHTML);
    const pastedTable = pastedContainer.querySelector("table");
    if (!pastedTable) {
      alert("추가할 표 데이터를 먼저 붙여넣으세요.");
      return;
    }
    const container = document.createElement("div");
    container.innerHTML = item.data_html || "";
    const table = container.querySelector("table");
    if (!table) {
      container.innerHTML = "";
      container.appendChild(pastedTable);
    } else {
      const target = table.querySelector("tbody") || table;
      Array.from(pastedTable.querySelectorAll("tr")).forEach((tr) => target.appendChild(tr));
    }
    try {
      const updated = await saveDataHtml(item.id, container.innerHTML);
      document.getElementById("rolloutAppendData").innerHTML = "";
      renderDataModalTable(updated);
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("rolloutEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("rolloutEditId").value;
    const payload = {
      title: document.getElementById("rolloutEditName").value.trim(),
      data_html: sanitizeRichHtml(document.getElementById("rolloutEditData").innerHTML),
    };
    try {
      if (id) {
        await fetchJson(`/api/rollout/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await fetchJson("/api/rollout", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      rolloutEditModal.hide();
      loadItems();
    } catch (err) {
      alert(err.message);
    }
  });
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    init_db()
    _conn = get_db()
    app.secret_key = get_config(_conn, "secret_key")
    _conn.close()
    lan_ip = get_lan_ip()
    print("설비 부품 교체 관리 시스템 시작!")
    print(f"  이 컴퓨터에서 접속: http://localhost:5000")
    print(f"  같은 네트워크의 다른 사람 접속: http://{lan_ip}:5000")
    print("  (다른 사람이 접속 안 되면 Windows 방화벽에서 Python 허용 여부를 확인하세요)")
    print(f"  최초 접속 비밀번호: {DEFAULT_PASSWORD} (로그인 후 반드시 변경해주세요)")
    threading.Thread(target=mail_scheduler_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
