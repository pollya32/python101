"""
설비 부품 교체 관리 시스템 — 단일 파일 버전
=====================================================
설치: pip install flask python-pptx
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
import secrets
import socket
from urllib.parse import quote
from datetime import date, datetime, timedelta
from flask import Flask, request, jsonify, send_file, Response, session, redirect, url_for
from pptx import Presentation
from pptx.util import Inches, Pt
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

try:
    _base = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _base = os.getcwd()
DB_PATH = os.path.join(_base, "equipment_data.db")

EQUIPMENT_COUNT = 20
EQUIPMENT_PREFIX = "TEAG"
MASTER_EQUIPMENT_ID = 1  # TEAG01호기: 이 설비에 추가한 부품은 동일한 이름의 유닛을 가진 나머지 설비에도 자동 복제된다
DEFAULT_PASSWORD = "0000"


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
        CREATE TABLE IF NOT EXISTS equipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            icon TEXT DEFAULT '🏭',
            pos_x REAL DEFAULT 50,
            pos_y REAL DEFAULT 50,
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
            icon TEXT DEFAULT '🔩',
            pos_x REAL DEFAULT 50,
            pos_y REAL DEFAULT 50,
            width REAL DEFAULT 130,
            height REAL DEFAULT 110,
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

    c.execute("""
        CREATE TABLE IF NOT EXISTS bulk_part_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_unit_text TEXT,
            unit_id INTEGER,
            part_name TEXT NOT NULL,
            q_code TEXT,
            note TEXT,
            cost REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE SET NULL
        )
    """)
    existing_bulk_cols = {r["name"] for r in c.execute("PRAGMA table_info(bulk_part_entries)").fetchall()}
    if "cost" not in existing_bulk_cols:
        c.execute("ALTER TABLE bulk_part_entries ADD COLUMN cost REAL DEFAULT 0")
        c.execute("UPDATE bulk_part_entries SET cost = 0 WHERE cost IS NULL")

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
                 last_replaced_date=None, note="", memo="", icon="🔩", pos_x=None, pos_y=None,
                 width=130, height=110):
    if pos_x is None or pos_y is None:
        pos_x, pos_y = random.uniform(15, 85), random.uniform(20, 80)
    cur = conn.execute(
        """INSERT INTO parts (unit_id, name, spec, cycle_days, cycle_unit, cost, last_replaced_date, note, memo, icon, pos_x, pos_y, width, height)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (unit_id, name, spec, cycle_days, cycle_unit, cost, last_replaced_date, note, memo, icon, pos_x, pos_y, width, height),
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
    나머지 설비에 일괄 동기화한다. 이름이 같은 부품은 규격/교체주기/비고/메모/아이콘/위치/크기가
    갱신되고, 새 부품은 추가되며, 여기 없는 이름의 부품은 삭제된다(교체 이력도 함께 삭제)."""
    master_unit = conn.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
    master_parts = conn.execute(
        "SELECT * FROM parts WHERE unit_id = ? ORDER BY id", (unit_id,)
    ).fetchall()
    master_names = {p["name"] for p in master_parts}

    target_units = conn.execute(
        "SELECT id FROM units WHERE name = ? AND equipment_id != ?",
        (master_unit["name"], MASTER_EQUIPMENT_ID),
    ).fetchall()

    for t in target_units:
        existing = {
            p["name"]: p
            for p in conn.execute("SELECT * FROM parts WHERE unit_id = ?", (t["id"],)).fetchall()
        }
        for mp in master_parts:
            if mp["name"] in existing:
                ep = existing[mp["name"]]
                conn.execute(
                    """UPDATE parts SET spec = ?, cycle_days = ?, cycle_unit = ?, cost = ?, note = ?, memo = ?, icon = ?,
                       pos_x = ?, pos_y = ?, width = ?, height = ? WHERE id = ?""",
                    (
                        mp["spec"], mp["cycle_days"], mp["cycle_unit"], mp["cost"], mp["note"], mp["memo"], mp["icon"],
                        mp["pos_x"], mp["pos_y"], mp["width"], mp["height"], ep["id"],
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO parts (unit_id, name, spec, cycle_days, cycle_unit, cost, note, memo, icon, pos_x, pos_y, width, height)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        t["id"], mp["name"], mp["spec"], mp["cycle_days"], mp["cycle_unit"], mp["cost"],
                        mp["note"], mp["memo"], mp["icon"], mp["pos_x"], mp["pos_y"], mp["width"], mp["height"],
                    ),
                )
        for name, ep in existing.items():
            if name not in master_names:
                conn.execute("DELETE FROM parts WHERE id = ?", (ep["id"],))

    return len(target_units), len(master_parts)


def unit_with_status(conn, u):
    parts = conn.execute("SELECT * FROM parts WHERE unit_id = ?", (u["id"],)).fetchall()
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
    return d


STATUS_PRIORITY = ["overdue", "soon", "unknown", "ok", "empty"]


def count_overdue_parts(conn, equipment_id):
    rows = conn.execute(
        """SELECT p.cycle_days, p.last_replaced_date
           FROM parts p JOIN units u ON p.unit_id = u.id
           WHERE u.equipment_id = ?""",
        (equipment_id,),
    ).fetchall()
    return sum(
        1 for p in rows
        if part_status(p["cycle_days"], p["last_replaced_date"])["status"] == "overdue"
    )


def equipment_with_status(conn, e):
    units = conn.execute("SELECT * FROM units WHERE equipment_id = ?", (e["id"],)).fetchall()
    statuses = [unit_with_status(conn, u)["overall_status"] for u in units]
    overall = next((s for s in STATUS_PRIORITY if s in statuses), "empty")
    d = dict(e)
    d["unit_count"] = len(units)
    d["overall_status"] = overall
    d["overdue_count"] = count_overdue_parts(conn, e["id"])
    return d


def seed_default_units_for_equipment(conn, equipment_id):
    templates = conn.execute("SELECT * FROM unit_templates ORDER BY id").fetchall()
    for t in templates:
        conn.execute(
            "INSERT INTO units (equipment_id, name, icon, color, pos_x, pos_y, width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (equipment_id, t["name"], t["icon"], t["color"], t["pos_x"], t["pos_y"], t["width"], t["height"]),
        )


def get_alert_parts():
    """모든 설비를 통틀어 교체 필요/임박 상태인 부품 목록 (경과가 급한 순)"""
    conn = get_db()
    rows = conn.execute("""
        SELECT p.*, u.id AS unit_id, u.name AS unit_name,
               e.id AS equipment_id, e.name AS equipment_name, e.icon AS equipment_icon
        FROM parts p
        JOIN units u ON p.unit_id = u.id
        JOIN equipments e ON u.equipment_id = e.id
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


def search_parts(query):
    """부품명/규격으로 모든 설비를 통틀어 검색"""
    conn = get_db()
    like = f"%{query}%"
    rows = conn.execute("""
        SELECT p.*, u.id AS unit_id, u.name AS unit_name,
               e.id AS equipment_id, e.name AS equipment_name, e.icon AS equipment_icon
        FROM parts p
        JOIN units u ON p.unit_id = u.id
        JOIN equipments e ON u.equipment_id = e.id
        WHERE p.name LIKE ? OR p.spec LIKE ?
        ORDER BY e.id, u.id, p.id
    """, (like, like)).fetchall()
    conn.close()
    result = []
    for r in rows:
        info = part_status(r["cycle_days"], r["last_replaced_date"])
        d = dict(r)
        d.update(info)
        result.append(d)
    return result


def get_part_spec_stats(conn, unit_names=None):
    """부품명+규격을 기준으로 시스템 전체(선택된 유닛 이름으로 범위 제한 가능)를 집계한다."""
    query = """
        SELECT p.*, u.name AS unit_name
        FROM parts p
        JOIN units u ON p.unit_id = u.id
    """
    params = []
    if unit_names:
        placeholders = ",".join("?" for _ in unit_names)
        query += f" WHERE u.name IN ({placeholders})"
        params = list(unit_names)
    parts = conn.execute(query, params).fetchall()

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
                "min_cycle_days": None,
                "min_cycle_unit": "일",
                "part_ids": [],
            }
            groups[key] = g
        g["total_cost"] += p["cost"] or 0
        g["instance_count"] += 1
        g["part_ids"].append(p["id"])
        if g["min_cycle_days"] is None or p["cycle_days"] < g["min_cycle_days"]:
            g["min_cycle_days"] = p["cycle_days"]
            g["min_cycle_unit"] = p["cycle_unit"]

    result = []
    for g in groups.values():
        part_ids = g.pop("part_ids")
        placeholders = ",".join("?" for _ in part_ids)
        g["usage_count"] = conn.execute(
            f"SELECT COUNT(*) AS n FROM replacement_history WHERE part_id IN ({placeholders})",
            part_ids,
        ).fetchone()["n"]
        result.append(g)
    return result


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
    if session.get("authenticated"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "로그인이 필요합니다"}), 401
    return redirect(url_for("login_page", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        password = request.form.get("password", "")
        conn = get_db()
        password_hash = get_config(conn, "password_hash")
        conn.close()
        if password_hash and check_password_hash(password_hash, password):
            session["authenticated"] = True
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
    return DASHBOARD_HTML


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


@app.route("/equipment/<int:equipment_id>")
def equipment_page(equipment_id):
    conn = get_db()
    equipment = conn.execute("SELECT id FROM equipments WHERE id = ?", (equipment_id,)).fetchone()
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
    unit = conn.execute("SELECT id FROM units WHERE id = ?", (unit_id,)).fetchone()
    conn.close()
    if not unit:
        return "유닛을 찾을 수 없습니다", 404
    return UNIT_HTML.replace("__UNIT_ID__", str(unit_id))


@app.route("/api/equipments")
def list_equipments():
    conn = get_db()
    equipments = conn.execute("SELECT * FROM equipments ORDER BY id").fetchall()
    result = [equipment_with_status(conn, e) for e in equipments]
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
    equipment = conn.execute("SELECT * FROM equipments WHERE id = ?", (equipment_id,)).fetchone()
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
    try:
        conn.execute(
            "UPDATE equipments SET name = ?, icon = ?, pos_x = ?, pos_y = ? WHERE id = ?",
            (name, icon, pos_x, pos_y, equipment_id),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "이미 사용 중인 설비 이름입니다"}), 409
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/equipments/<int:equipment_id>", methods=["DELETE"])
def delete_equipment(equipment_id):
    conn = get_db()
    conn.execute("DELETE FROM equipments WHERE id = ?", (equipment_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/equipments/<int:equipment_id>/units")
def list_units(equipment_id):
    conn = get_db()
    units = conn.execute(
        "SELECT * FROM units WHERE equipment_id = ? ORDER BY id", (equipment_id,)
    ).fetchall()
    result = [unit_with_status(conn, u) for u in units]
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
    conn.close()
    d = dict(unit)
    d["part_count"] = 0
    d["overall_status"] = "empty"
    return jsonify(d), 201


@app.route("/api/units/<int:unit_id>")
def get_unit(unit_id):
    conn = get_db()
    unit = conn.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
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
    conn.execute(
        "UPDATE units SET name = ?, icon = ?, color = ?, pos_x = ?, pos_y = ?, width = ?, height = ? WHERE id = ?",
        (name, icon, color, pos_x, pos_y, width, height, unit_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/units/<int:unit_id>", methods=["DELETE"])
def delete_unit(unit_id):
    conn = get_db()
    conn.execute("DELETE FROM units WHERE id = ?", (unit_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/units/<int:unit_id>/parts")
def list_parts(unit_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM parts WHERE unit_id = ? ORDER BY id", (unit_id,)
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
    memo = (data.get("memo") or "").strip()
    icon = (data.get("icon") or "🔩").strip()
    width = data.get("width") or 130
    height = data.get("height") or 110

    conn = get_db()
    part_id = insert_part(
        conn, unit_id, name, spec=spec, cycle_days=cycle_days, cycle_unit=cycle_unit, cost=cost,
        last_replaced_date=last_replaced_date, note=note, memo=memo, icon=icon,
        pos_x=data.get("pos_x"), pos_y=data.get("pos_y"), width=width, height=height,
    )
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


def parse_bulk_paste_text(text):
    """붙여넣은 텍스트(탭 또는 쉼표 구분)를 (유닛이름, 부품이름, Q-CODE, 부가설명, 금액) 튜플 목록으로 변환."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            cols = line.split(",")
        cols = [c.strip() for c in cols]
        while len(cols) < 5:
            cols.append("")
        unit_text, part_name, q_code, note, cost_text = cols[0], cols[1], cols[2], cols[3], cols[4]
        if not part_name:
            continue
        try:
            cost = float(cost_text) if cost_text else 0
        except ValueError:
            cost = 0
        rows.append((unit_text, part_name, q_code, note, cost))
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
    for unit_text, part_name, q_code, note, cost in parsed:
        cur = conn.execute(
            "INSERT INTO bulk_part_entries (raw_unit_text, part_name, q_code, note, cost) "
            "VALUES (?, ?, ?, ?, ?)",
            (unit_text, part_name, q_code, note, cost),
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
    conn.execute(
        "UPDATE bulk_part_entries SET part_name = ?, q_code = ?, note = ?, cost = ? WHERE id = ?",
        (part_name, q_code, note, cost, entry_id),
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
        unit = conn.execute("SELECT * FROM units WHERE id = ?", (uid,)).fetchone()
        if not unit:
            continue
        part_id = insert_part(
            conn, uid, entry["part_name"], spec=entry["q_code"] or "",
            cost=entry["cost"] or 0, note=entry["note"] or "",
        )
        part_ids.append(part_id)
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
    memo = data.get("memo", part["memo"])
    icon = (data.get("icon") or part["icon"]).strip()
    pos_x = data.get("pos_x", part["pos_x"])
    pos_y = data.get("pos_y", part["pos_y"])
    width = data.get("width", part["width"])
    height = data.get("height", part["height"])
    conn.execute(
        """UPDATE parts SET name = ?, spec = ?, cycle_days = ?, cycle_unit = ?, cost = ?, note = ?, memo = ?, icon = ?,
           pos_x = ?, pos_y = ?, width = ?, height = ? WHERE id = ?""",
        (name, spec, cycle_days, cycle_unit, cost, note, memo, icon, pos_x, pos_y, width, height, part_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    conn.close()
    return jsonify(serialize_part(row))


@app.route("/api/parts/<int:part_id>", methods=["DELETE"])
def delete_part(part_id):
    conn = get_db()
    conn.execute("DELETE FROM parts WHERE id = ?", (part_id,))
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
    conn.execute("DELETE FROM replacement_history WHERE id = ?", (history_id,))
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
    content = data.get("content") or ""
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
    content = data.get("content") or ""
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
    equipments = conn.execute("SELECT id FROM equipments").fetchall()
    template_names = {t["name"] for t in templates}

    for eq in equipments:
        existing = {
            u["name"]: u
            for u in conn.execute(
                "SELECT * FROM units WHERE equipment_id = ?", (eq["id"],)
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
    if not q:
        return jsonify([])
    return jsonify(search_parts(q))


def build_stats_payload(conn, unit_names):
    """부품 규격 기준(금액순/사용량 많은순/교체주기 짧은순)과 유닛 기준(부품수 많은순) 통계를 함께 만든다."""
    spec_rows = get_part_spec_stats(conn, unit_names)
    by_cost = sorted(spec_rows, key=lambda r: r["total_cost"], reverse=True)
    by_usage = sorted(spec_rows, key=lambda r: r["usage_count"], reverse=True)
    by_short_cycle = sorted(
        (r for r in spec_rows if r["min_cycle_days"] is not None), key=lambda r: r["min_cycle_days"]
    )

    unit_query = """
        SELECT u.id AS unit_id, u.name AS unit_name, u.icon AS unit_icon,
               e.id AS equipment_id, e.name AS equipment_name, e.icon AS equipment_icon,
               (SELECT COUNT(*) FROM parts p WHERE p.unit_id = u.id) AS part_count
        FROM units u
        JOIN equipments e ON u.equipment_id = e.id
    """
    params = []
    if unit_names:
        placeholders = ",".join("?" for _ in unit_names)
        unit_query += f" WHERE u.name IN ({placeholders})"
        params = unit_names
    unit_query += " ORDER BY u.id"
    unit_rows = [dict(r) for r in conn.execute(unit_query, params).fetchall()]
    by_part_count = sorted(unit_rows, key=lambda r: r["part_count"], reverse=True)

    all_unit_names = [
        r["name"] for r in conn.execute("SELECT DISTINCT name FROM units ORDER BY name").fetchall()
    ]

    return {
        "by_cost": by_cost,
        "by_usage": by_usage,
        "by_short_cycle": by_short_cycle,
        "by_part_count": by_part_count,
        "unit_names": all_unit_names,
    }


@app.route("/api/stats")
def api_stats():
    unit_names = request.args.getlist("unit_name")
    conn = get_db()
    payload = build_stats_payload(conn, unit_names)
    conn.close()
    return jsonify(payload)


def format_cycle_for_export(days, unit):
    if unit == "년":
        return f"{round(days / 365, 2)}년"
    return f"{days}일"


@app.route("/api/stats/export.csv")
def export_stats_csv():
    unit_names = request.args.getlist("unit_name")
    conn = get_db()
    payload = build_stats_payload(conn, unit_names)
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
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    title_slide = prs.slides.add_slide(prs.slide_layouts[0])
    title_slide.shapes.title.text = "부품/유닛 통계 리포트"
    filter_text = ", ".join(unit_names) if unit_names else "전체 유닛"
    title_slide.placeholders[1].text = (
        f"생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n필터: {filter_text}"
    )

    blank_layout = prs.slide_layouts[6]

    def add_table_slide(title, headers, rows, max_rows=15):
        slide = prs.slides.add_slide(blank_layout)
        title_box = slide.shapes.add_textbox(Inches(0.4), Inches(0.3), Inches(12.5), Inches(0.7))
        title_box.text_frame.text = title
        title_box.text_frame.paragraphs[0].font.size = Pt(28)
        title_box.text_frame.paragraphs[0].font.bold = True

        display_rows = rows[:max_rows]
        n_rows = len(display_rows) + 1
        n_cols = len(headers)
        table = slide.shapes.add_table(
            n_rows, n_cols, Inches(0.4), Inches(1.1), Inches(12.5), Inches(0.4 * n_rows)
        ).table
        for c, h in enumerate(headers):
            table.cell(0, c).text = str(h)
        for r_i, row in enumerate(display_rows, 1):
            for c_i, val in enumerate(row):
                table.cell(r_i, c_i).text = str(val)

    add_table_slide(
        "금액순 (부품 규격 기준)",
        ["순위", "부품명", "규격", "총 금액(원)", "등록 수", "교체 횟수"],
        [
            [i, r["name"], r["spec"], f'{r["total_cost"]:,.0f}', r["instance_count"], r["usage_count"]]
            for i, r in enumerate(payload["by_cost"], 1)
        ],
    )
    add_table_slide(
        "사용량 많은순 (부품 규격 기준)",
        ["순위", "부품명", "규격", "교체 횟수", "등록 수", "총 금액(원)"],
        [
            [i, r["name"], r["spec"], r["usage_count"], r["instance_count"], f'{r["total_cost"]:,.0f}']
            for i, r in enumerate(payload["by_usage"], 1)
        ],
    )
    add_table_slide(
        "교체 주기 짧은순 (부품 규격 기준)",
        ["순위", "부품명", "규격", "교체 주기", "등록 수"],
        [
            [i, r["name"], r["spec"], format_cycle_for_export(r["min_cycle_days"], r["min_cycle_unit"]), r["instance_count"]]
            for i, r in enumerate(payload["by_short_cycle"], 1)
        ],
    )
    add_table_slide(
        "부품수 많은순 (유닛 기준)",
        ["순위", "설비", "유닛", "부품수"],
        [
            [i, r["equipment_name"], r["unit_name"], r["part_count"]]
            for i, r in enumerate(payload["by_part_count"], 1)
        ],
    )

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf


@app.route("/api/stats/export.pptx")
def export_stats_pptx():
    unit_names = request.args.getlist("unit_name")
    conn = get_db()
    payload = build_stats_payload(conn, unit_names)
    conn.close()
    buf = build_stats_pptx(payload, unit_names)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"통계_{timestamp}.pptx",
        mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@app.route("/api/backup")
def download_backup():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        DB_PATH,
        as_attachment=True,
        download_name=f"equipment_backup_{timestamp}.db",
    )


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
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }
</style>
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
    <a href="/api/backup" class="btn btn-sm btn-outline-light">
      <i class="bi bi-download"></i> DB 백업
    </a>
    <button id="addEquipmentBtn" class="btn btn-sm btn-outline-light">
      <i class="bi bi-plus-lg"></i> 설비 추가
    </button>
    <button id="editModeBtn" class="btn btn-sm btn-outline-light">
      <i class="bi bi-pencil-square"></i> 설비 편집
    </button>
    <div class="dropdown">
      <button class="btn btn-sm btn-outline-light dropdown-toggle" type="button" id="accountMenuBtn" data-bs-toggle="dropdown">
        <i class="bi bi-person-circle"></i>
      </button>
      <ul class="dropdown-menu dropdown-menu-end">
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
  grid.innerHTML = equipments.map(equipmentCardHtml).join("");
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
      if (!confirm(`"${eq.name}" 설비를 삭제할까요? 등록된 유닛/부품/이력이 모두 함께 삭제됩니다.`)) return;
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
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }
</style>
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

  <div class="legend mb-3">
    <span class="legend-item"><span class="dot dot-ok"></span> 정상</span>
    <span class="legend-item"><span class="dot dot-soon"></span> 교체 임박</span>
    <span class="legend-item"><span class="dot dot-overdue"></span> 교체 필요</span>
    <span class="legend-item"><span class="dot dot-unknown"></span> 미기록 / 부품 없음</span>
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
    <textarea id="notesEdit" class="form-control d-none" rows="8"
      placeholder="설비 정보, 부품 구매 사이트 URL 등을 자유롭게 기록하세요. (http://, https://로 시작하는 링크는 자동으로 클릭 가능한 링크가 됩니다)"></textarea>
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

<script>const EQUIPMENT_ID = __EQUIPMENT_ID__;</script>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
let editMode = false;
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

async function loadEquipmentHeader() {
  try {
    const equipment = await fetchJson(`/api/equipments/${EQUIPMENT_ID}`);
    document.getElementById("equipmentPageTitle").textContent = equipment.name;
    document.title = `${equipment.name} - 설비 부품 교체 관리 시스템`;
  } catch (err) {
    alert("설비 정보를 불러올 수 없습니다.");
    window.location.href = "/";
  }
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
      if (!confirm(`"${u.name}" 유닛을 삭제할까요? 등록된 부품/이력도 함께 삭제됩니다.`)) return;
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
      <div class="unit-icon-wrap"><span class="unit-icon">${u.icon}</span></div>
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

function linkifyText(text) {
  const escaped = escapeHtml(text);
  return escaped.replace(
    /(https?:\/\/[^\s<]+)/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
  );
}

let lastNotesContent = "";

async function loadNotes() {
  const data = await fetchJson(`/api/equipments/${EQUIPMENT_ID}/notes`);
  lastNotesContent = data.content || "";
  renderNotesView(lastNotesContent);
  document.getElementById("notesEdit").value = lastNotesContent;
  document.getElementById("notesSavedAt").textContent = data.updated_at
    ? `최종 수정: ${data.updated_at}`
    : "";
}

function renderNotesView(content) {
  const view = document.getElementById("notesView");
  if (!content || !content.trim()) {
    view.innerHTML = "";
    view.classList.add("is-empty");
  } else {
    view.classList.remove("is-empty");
    view.innerHTML = linkifyText(content);
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

  tick();
  setInterval(tick, 1000);
  loadEquipmentHeader();
  loadUnits();
  loadNotes();

  document.getElementById("editNotesBtn").addEventListener("click", () => setNotesEditing(true));
  document.getElementById("cancelNotesBtn").addEventListener("click", () => {
    document.getElementById("notesEdit").value = lastNotesContent;
    setNotesEditing(false);
  });
  document.getElementById("saveNotesBtn").addEventListener("click", async () => {
    const content = document.getElementById("notesEdit").value;
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
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }
</style>
</head>
<body>

<header class="topbar">
  <div class="d-flex align-items-center gap-2">
    <a href="/" id="backToEquipmentBtn" class="btn btn-sm btn-outline-light"><i class="bi bi-arrow-left"></i> 설비로</a>
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
    <textarea id="notesEdit" class="form-control d-none" rows="8"
      placeholder="부품 규격, 구매처 URL 등을 자유롭게 기록하세요. (http://, https://로 시작하는 링크는 자동으로 클릭 가능한 링크가 됩니다)"></textarea>
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
        </div>
        <div class="part-memo-section mt-3">
          <div class="small text-muted mb-1"><i class="bi bi-journal-text"></i> 메모</div>
          <div id="partDetailMemo" class="part-memo-view"></div>
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
            <textarea class="form-control" id="partEditMemo" rows="4"
              placeholder="부품 관련 세부 정보를 자유롭게 기록하세요. (http://, https://로 시작하는 링크는 자동으로 클릭 가능한 링크가 됩니다)"></textarea>
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
let partDetailModal, replaceModal, historyModal, partEditModal;
let currentParts = [];
let currentEquipmentId = null;
const MASTER_EQUIPMENT_ID = 1;

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

function linkifyText(text) {
  const escaped = escapeHtml(text);
  return escaped.replace(
    /(https?:\/\/[^\s<]+)/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
  );
}

let lastNotesContent = "";

async function loadNotes() {
  const data = await fetchJson(`/api/units/${UNIT_ID}/notes`);
  lastNotesContent = data.content || "";
  renderNotesView(lastNotesContent);
  document.getElementById("notesEdit").value = lastNotesContent;
  document.getElementById("notesSavedAt").textContent = data.updated_at
    ? `최종 수정: ${data.updated_at}`
    : "";
}

function renderNotesView(content) {
  const view = document.getElementById("notesView");
  if (!content || !content.trim()) {
    view.innerHTML = "";
    view.classList.add("is-empty");
  } else {
    view.classList.remove("is-empty");
    view.innerHTML = linkifyText(content);
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
      if (!confirm(`"${p.name}" 부품을 삭제할까요? 교체 이력도 함께 삭제됩니다.`)) return;
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
  document.getElementById("partDetailBody").innerHTML = `
    <span class="badge ${badge} mb-2">${label}</span>
    ${p.spec ? `<div class="part-spec mb-1">규격: ${escapeHtml(p.spec)}</div>` : ""}
    <div class="small text-muted">
      교체 주기: ${formatCycleDisplay(p.cycle_days, p.cycle_unit)} &middot; 금액: ${formatCost(p.cost)} &middot; ${lastText}
      ${dueText ? `<br>${dueText}` : ""}
      ${p.note ? `<br>비고: ${escapeHtml(p.note)}` : ""}
    </div>`;
  const memoView = document.getElementById("partDetailMemo");
  if (!p.memo || !p.memo.trim()) {
    memoView.innerHTML = "";
    memoView.classList.add("is-empty");
  } else {
    memoView.classList.remove("is-empty");
    memoView.innerHTML = linkifyText(p.memo);
  }
  partDetailModal.show();
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
  document.getElementById("partEditMemo").value = part ? part.memo || "" : "";
  document.getElementById("partEditLastDate").value = "";
  document.getElementById("partEditLastDateWrap").classList.toggle("d-none", !!part);
  renderIconPicker("partIconPicker", "partEditIcon", icon);
  partEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  partDetailModal = new bootstrap.Modal(document.getElementById("partDetailModal"));
  replaceModal = new bootstrap.Modal(document.getElementById("replaceModal"));
  historyModal = new bootstrap.Modal(document.getElementById("historyModal"));
  partEditModal = new bootstrap.Modal(document.getElementById("partEditModal"));

  tick();
  setInterval(tick, 1000);
  loadUnitHeader();
  loadParts();
  loadNotes();

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

  document.getElementById("editNotesBtn").addEventListener("click", () => setNotesEditing(true));
  document.getElementById("cancelNotesBtn").addEventListener("click", () => {
    document.getElementById("notesEdit").value = lastNotesContent;
    setNotesEditing(false);
  });
  document.getElementById("saveNotesBtn").addEventListener("click", async () => {
    const content = document.getElementById("notesEdit").value;
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

  document.getElementById("partDetailReplaceBtn").addEventListener("click", () => {
    partDetailModal.hide();
    openReplaceModal();
  });
  document.getElementById("partDetailHistoryBtn").addEventListener("click", () => {
    partDetailModal.hide();
    openHistoryModal();
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
      memo: document.getElementById("partEditMemo").value,
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
      "- 이름이 같은 부품은 규격/교체주기/비고/메모/아이콘/위치/크기가 이 구성대로 갱신됩니다.\n" +
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
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }
</style>
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
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }
</style>
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
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }
</style>
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
  </div>

  <div id="searchResults" class="alerts-list mt-3"></div>
  <p id="searchHintMsg" class="text-muted text-center py-4">부품명 또는 규격을 입력하면 모든 설비에서 찾아드립니다.</p>

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

async function runSearch() {
  const q = document.getElementById("partSearchInput").value.trim();
  const list = document.getElementById("searchResults");
  const hint = document.getElementById("searchHintMsg");

  if (!q) {
    list.innerHTML = "";
    hint.textContent = "부품명 또는 규격을 입력하면 모든 설비에서 찾아드립니다.";
    hint.classList.remove("d-none");
    return;
  }

  const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const parts = await res.json();

  if (parts.length === 0) {
    list.innerHTML = "";
    hint.textContent = "검색 결과가 없습니다.";
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

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  document.getElementById("partSearchInput").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 200);
  });

  const q = new URLSearchParams(window.location.search).get("q");
  if (q) {
    document.getElementById("partSearchInput").value = q;
    runSearch();
  }
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
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }
</style>
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
    <a id="exportCsvBtn" href="/api/stats/export.csv" class="btn btn-sm btn-outline-secondary">
      <i class="bi bi-file-earmark-spreadsheet"></i> CSV 다운로드
    </a>
    <a id="exportPptxBtn" href="/api/stats/export.pptx" class="btn btn-sm btn-outline-secondary">
      <i class="bi bi-file-earmark-slides"></i> PPT 다운로드
    </a>
  </div>

  <div class="stats-grid">
    <div class="stats-panel">
      <h6><i class="bi bi-cash-coin"></i> 금액순 <span class="stats-subtitle">(부품 규격 기준)</span></h6>
      <div id="statsCost" class="stats-list"></div>
    </div>
    <div class="stats-panel">
      <h6><i class="bi bi-arrow-repeat"></i> 사용량 많은순 <span class="stats-subtitle">(부품 규격 기준)</span></h6>
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

async function loadStats() {
  const params = new URLSearchParams();
  selectedUnitNames.forEach((name) => params.append("unit_name", name));
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
  const params = new URLSearchParams();
  selectedUnitNames.forEach((name) => params.append("unit_name", name));
  const qs = params.toString();
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
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }
</style>
</head>
<body>

<main class="login-wrap">
  <div class="login-card">
    <div class="login-icon"><i class="bi bi-shield-lock-fill"></i></div>
    <h1>설비 부품 교체 관리 시스템</h1>
    <p class="text-muted small mb-3">접속 비밀번호를 입력하세요</p>
    <div id="errorBox" class="alert alert-danger py-2 small d-none">비밀번호가 올바르지 않습니다</div>
    <form method="POST">
      <input type="password" name="password" class="form-control mb-3" placeholder="비밀번호" autofocus required>
      <button type="submit" class="btn btn-primary w-100">로그인</button>
    </form>
    <p class="text-muted small mt-3 mb-0">최초 비밀번호는 <strong>0000</strong> 입니다. 로그인 후 반드시 변경해주세요.</p>
  </div>
</main>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
  if (new URLSearchParams(window.location.search).get("error")) {
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
.bulk-status-pending { color: var(--text-muted); }
.bulk-status-registered { color: #16a34a; font-weight: 700; }
</style>
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
      엑셀 등에서 <strong>유닛이름, 부품이름, Q-CODE, 부가설명, 금액</strong> 순서로 복사해 아래에 붙여넣으세요 (한 줄에 부품 하나).
    </label>
    <p class="text-muted small mb-2">
      유닛 선택은 "기본 유닛 구성"에 등록된 유닛 이름을 기준으로 표시되며, 유닛 하나에 여러 개를 다중 선택할 수 있습니다(선택한 모든 유닛에 동일한 부품이 등록됩니다).
    </p>
    <textarea id="pasteArea" class="form-control" rows="4" placeholder="로드포트1&#9;오링&#9;Q-1234&#9;내열용&#9;5000&#10;HMI&#9;케이블&#9;Q-5678&#9;연결선&#9;12000"></textarea>
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
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
