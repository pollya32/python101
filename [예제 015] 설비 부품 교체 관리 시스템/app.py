from flask import Flask, render_template, request, jsonify, send_file, Response, session, redirect, url_for
import sqlite3
import os
import csv
import io
import random
import secrets
import socket
from urllib.parse import quote
from datetime import date, datetime, timedelta
from pptx import Presentation
from pptx.util import Inches, Pt
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
DB_PATH = os.path.join(os.path.dirname(__file__), "equipment.db")

EQUIPMENT_COUNT = 20
EQUIPMENT_PREFIX = "TEAG"
MASTER_EQUIPMENT_ID = 1  # TEAG01호기: 이 설비에 추가한 부품은 동일한 이름의 유닛을 가진 나머지 설비에도 자동 복제된다
DEFAULT_PASSWORD = "0000"
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
            drawing_data TEXT,
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
    if "drawing_data" not in existing_part_cols:
        c.execute("ALTER TABLE parts ADD COLUMN drawing_data TEXT")
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
                 last_replaced_date=None, note="", memo="", drawing_data=None, icon="🔩",
                 pos_x=None, pos_y=None, width=130, height=110):
    if pos_x is None or pos_y is None:
        pos_x, pos_y = random.uniform(15, 85), random.uniform(20, 80)
    cur = conn.execute(
        """INSERT INTO parts (unit_id, name, spec, cycle_days, cycle_unit, cost, last_replaced_date, note, memo, drawing_data, icon, pos_x, pos_y, width, height)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (unit_id, name, spec, cycle_days, cycle_unit, cost, last_replaced_date, note, memo, drawing_data, icon, pos_x, pos_y, width, height),
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
    나머지 설비에 일괄 동기화한다. 이름이 같은 부품은 규격/교체주기/비고/메모/도면/아이콘/위치/크기가
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
                    """UPDATE parts SET spec = ?, cycle_days = ?, cycle_unit = ?, cost = ?, note = ?, memo = ?,
                       drawing_data = ?, icon = ?, pos_x = ?, pos_y = ?, width = ?, height = ? WHERE id = ?""",
                    (
                        mp["spec"], mp["cycle_days"], mp["cycle_unit"], mp["cost"], mp["note"], mp["memo"],
                        mp["drawing_data"], mp["icon"], mp["pos_x"], mp["pos_y"], mp["width"], mp["height"], ep["id"],
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO parts (unit_id, name, spec, cycle_days, cycle_unit, cost, note, memo, drawing_data, icon, pos_x, pos_y, width, height)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        t["id"], mp["name"], mp["spec"], mp["cycle_days"], mp["cycle_unit"], mp["cost"],
                        mp["note"], mp["memo"], mp["drawing_data"], mp["icon"], mp["pos_x"], mp["pos_y"], mp["width"], mp["height"],
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
    return render_template("login.html")


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
    return render_template("dashboard.html")


@app.route("/alerts")
def alerts_page():
    return render_template("alerts.html")


@app.route("/search")
def search_page():
    return render_template("search.html")


@app.route("/stats")
def stats_page():
    return render_template("stats.html")


@app.route("/bulk-add-parts")
def bulk_add_parts_page():
    return render_template("bulk_add_parts.html")


@app.route("/equipment/<int:equipment_id>")
def equipment_page(equipment_id):
    conn = get_db()
    equipment = conn.execute("SELECT id FROM equipments WHERE id = ?", (equipment_id,)).fetchone()
    conn.close()
    if not equipment:
        return "설비를 찾을 수 없습니다", 404
    return render_template("equipment.html", equipment_id=equipment_id)


@app.route("/config")
def unit_template_config():
    return render_template("config.html")


@app.route("/unit/<int:unit_id>")
def unit_detail(unit_id):
    conn = get_db()
    unit = conn.execute("SELECT id FROM units WHERE id = ?", (unit_id,)).fetchone()
    conn.close()
    if not unit:
        return "유닛을 찾을 수 없습니다", 404
    return render_template("unit.html", unit_id=unit_id)


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
    drawing_data = data.get("drawing_data") or None
    if drawing_data and len(drawing_data) > MAX_DRAWING_DATA_LEN:
        return jsonify({"error": "도면 이미지 용량이 너무 큽니다 (최대 5MB)"}), 400
    icon = (data.get("icon") or "🔩").strip()
    width = data.get("width") or 130
    height = data.get("height") or 110

    conn = get_db()
    part_id = insert_part(
        conn, unit_id, name, spec=spec, cycle_days=cycle_days, cycle_unit=cycle_unit, cost=cost,
        last_replaced_date=last_replaced_date, note=note, memo=memo, drawing_data=drawing_data, icon=icon,
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
    drawing_data = data.get("drawing_data", part["drawing_data"])
    if drawing_data and len(drawing_data) > MAX_DRAWING_DATA_LEN:
        conn.close()
        return jsonify({"error": "도면 이미지 용량이 너무 큽니다 (최대 5MB)"}), 400
    icon = (data.get("icon") or part["icon"]).strip()
    pos_x = data.get("pos_x", part["pos_x"])
    pos_y = data.get("pos_y", part["pos_y"])
    width = data.get("width", part["width"])
    height = data.get("height", part["height"])
    conn.execute(
        """UPDATE parts SET name = ?, spec = ?, cycle_days = ?, cycle_unit = ?, cost = ?, note = ?, memo = ?,
           drawing_data = ?, icon = ?, pos_x = ?, pos_y = ?, width = ?, height = ? WHERE id = ?""",
        (name, spec, cycle_days, cycle_unit, cost, note, memo, drawing_data, icon, pos_x, pos_y, width, height, part_id),
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
