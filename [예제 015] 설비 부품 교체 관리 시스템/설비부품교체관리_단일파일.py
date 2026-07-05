"""
설비 부품 교체 관리 시스템 — 단일 파일 버전
=====================================================
설치: pip install flask
실행: python 설비부품교체관리_단일파일.py
접속: http://localhost:5000

이 파일 하나만 다운로드하면 폴더 구조 없이 바로 실행할 수 있습니다.
(templates/static 폴더가 필요한 app.py 버전과 동일한 기능을 담은 단일 파일본)
"""

import sqlite3
import os
import random
import socket
from datetime import date, datetime, timedelta
from flask import Flask, request, jsonify

app = Flask(__name__)

try:
    _base = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _base = os.getcwd()
DB_PATH = os.path.join(_base, "equipment_data.db")

EQUIPMENT_COUNT = 20
EQUIPMENT_PREFIX = "TEAG"

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


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS equipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    eq_count = c.execute("SELECT COUNT(*) AS n FROM equipments").fetchone()["n"]
    if eq_count == 0:
        for i in range(1, EQUIPMENT_COUNT + 1):
            c.execute(
                "INSERT INTO equipments (id, name) VALUES (?, ?)",
                (i, f"{EQUIPMENT_PREFIX}{i:02d}호기"),
            )

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
            last_replaced_date TEXT,
            note TEXT,
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
            note TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (part_id) REFERENCES parts(id) ON DELETE CASCADE
        )
    """)

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


def equipment_with_status(conn, e):
    units = conn.execute("SELECT * FROM units WHERE equipment_id = ?", (e["id"],)).fetchall()
    statuses = [unit_with_status(conn, u)["overall_status"] for u in units]
    overall = next((s for s in STATUS_PRIORITY if s in statuses), "empty")
    d = dict(e)
    d["unit_count"] = len(units)
    d["overall_status"] = overall
    return d


@app.route("/")
def dashboard():
    return DASHBOARD_HTML


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
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "설비 이름을 입력하세요"}), 400
    conn = get_db()
    equipment = conn.execute("SELECT * FROM equipments WHERE id = ?", (equipment_id,)).fetchone()
    if not equipment:
        conn.close()
        return jsonify({"error": "설비를 찾을 수 없습니다"}), 404
    try:
        conn.execute("UPDATE equipments SET name = ? WHERE id = ?", (name, equipment_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "이미 사용 중인 설비 이름입니다"}), 409
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
    last_replaced_date = data.get("last_replaced_date") or None
    note = (data.get("note") or "").strip()
    icon = (data.get("icon") or "🔩").strip()
    pos_x = data.get("pos_x")
    pos_y = data.get("pos_y")
    if pos_x is None or pos_y is None:
        pos_x, pos_y = random.uniform(15, 85), random.uniform(20, 80)
    width = data.get("width") or 130
    height = data.get("height") or 110

    conn = get_db()
    cur = conn.execute(
        """INSERT INTO parts (unit_id, name, spec, cycle_days, last_replaced_date, note, icon, pos_x, pos_y, width, height)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (unit_id, name, spec, cycle_days, last_replaced_date, note, icon, pos_x, pos_y, width, height),
    )
    part_id = cur.lastrowid
    if last_replaced_date:
        conn.execute(
            "INSERT INTO replacement_history (part_id, replaced_date, note) VALUES (?, ?, ?)",
            (part_id, last_replaced_date, "최초 등록"),
        )
    conn.commit()
    row = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    conn.close()
    return jsonify(serialize_part(row)), 201


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
    note = data.get("note", part["note"])
    icon = (data.get("icon") or part["icon"]).strip()
    pos_x = data.get("pos_x", part["pos_x"])
    pos_y = data.get("pos_y", part["pos_y"])
    width = data.get("width", part["width"])
    height = data.get("height", part["height"])
    conn.execute(
        "UPDATE parts SET name = ?, spec = ?, cycle_days = ?, note = ?, icon = ?, pos_x = ?, pos_y = ?, width = ?, height = ? WHERE id = ?",
        (name, spec, cycle_days, note, icon, pos_x, pos_y, width, height, part_id),
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
    note = (data.get("note") or "").strip()

    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    if not part:
        conn.close()
        return jsonify({"error": "부품을 찾을 수 없습니다"}), 404
    conn.execute(
        "INSERT INTO replacement_history (part_id, replaced_date, note) VALUES (?, ?, ?)",
        (part_id, replaced_date, note),
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
  --pri: #1a3a5c;
  --bg: #eef1f5;
}

body {
  background: var(--bg);
  font-family: "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
}

.topbar {
  background: var(--pri);
  color: #fff;
  padding: 12px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; }
.clock { font-size: 12px; opacity: 0.8; }

.legend {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: #444;
}
.legend-item { display: flex; align-items: center; gap: 6px; }
.dot { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
.dot-ok { background: #22c55e; }
.dot-soon { background: #f59e0b; }
.dot-overdue { background: #ef4444; }
.dot-unknown { background: #9ca3af; }

.equipment-frame {
  background: linear-gradient(180deg, #f8fafc, #e2e8f0);
  border: 1px solid #cbd5e1;
  border-radius: 16px;
  padding: 28px 20px 20px;
  box-shadow: inset 0 0 0 6px #fff, 0 4px 16px rgba(0, 0, 0, 0.08);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 20px;
  background: var(--pri);
  color: #fff;
  font-size: 12px;
  padding: 4px 12px;
  border-radius: 999px;
}

.unit-shape {
  --shape-color: var(--pri);
  border: 4px solid var(--shape-color);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 40px;
  display: block;
  line-height: 1.2;
}
.unit-shape-name {
  font-size: 20px;
  font-weight: 700;
  color: var(--shape-color);
}

.equipment-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 16px;
  max-width: 1100px;
  margin: 0 auto;
}
.equipment-card {
  position: relative;
  background: #fff;
  border: 2px solid var(--pri);
  border-radius: 12px;
  padding: 18px 10px 14px;
  cursor: pointer;
  text-align: center;
  transition: box-shadow 0.15s, transform 0.15s;
}
.equipment-card:hover {
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.15);
  transform: translateY(-2px);
}
.equipment-card .unit-icon { font-size: 32px; }

.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

.unit-card {
  --uc: #1a3a5c;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: #fff;
  border: 2px solid var(--uc);
  border-radius: 12px;
  padding: 12px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.15s;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
}
.unit-card:hover {
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.15);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.22);
  z-index: 20;
  transition: none;
}
.unit-icon { font-size: 28px; display: block; margin-bottom: 6px; }
.unit-name { font-size: 13px; font-weight: 600; color: #1f2937; line-height: 1.3; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0,0,0,.1);
}
.unit-part-count {
  font-size: 11px;
  color: #6b7280;
  margin-top: 4px;
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
  background: rgba(0,0,0,.06);
  border-radius: 6px;
  font-size: 11px;
  padding: 2px 5px;
}
.unit-edit-actions button:hover { background: rgba(0,0,0,.14); }

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
  border-right: 2px solid rgba(0, 0, 0, 0.4);
  border-bottom: 2px solid rgba(0, 0, 0, 0.4);
}

.add-unit-btn { margin-top: 16px; display: block; margin-left: auto; margin-right: auto; }

.part-card {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 10px 14px;
  margin-bottom: 10px;
  background: #fafafa;
}
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: #6b7280; }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 6px 0; }

.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: #fff;
  border: 1px solid #dee2e6;
  border-radius: 12px;
  padding: 16px 18px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
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
  color: #2563eb;
  word-break: break-all;
}
#notesEdit { font-size: 14px; }
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
    <a href="/config" class="btn btn-sm btn-outline-light">
      <i class="bi bi-diagram-3"></i> 기본 유닛 구성
    </a>
    <button id="editModeBtn" class="btn btn-sm btn-outline-light">
      <i class="bi bi-pencil-square"></i> 설비명 편집
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

  <div id="equipmentGrid" class="equipment-grid"></div>

</main>

<!-- 설비명 편집 모달 -->
<div class="modal fade" id="equipmentEditModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">설비명 편집</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <form id="equipmentEditForm">
          <input type="hidden" id="equipmentEditId">
          <div class="mb-2">
            <label class="form-label">설비 이름</label>
            <input type="text" class="form-control" id="equipmentEditName" required>
          </div>
          <button type="submit" class="btn btn-primary w-100 mt-2">저장</button>
        </form>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
let editMode = false;
let equipmentEditModal;

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
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

async function loadEquipments() {
  const equipments = await fetchJson("/api/equipments");
  renderGrid(equipments);
}

function renderGrid(equipments) {
  const grid = document.getElementById("equipmentGrid");
  grid.innerHTML = equipments.map(equipmentCardHtml).join("");
  equipments.forEach((eq) => {
    const card = grid.querySelector(`[data-equipment-id="${eq.id}"]`);
    card.querySelector(".edit-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openEquipmentEditModal(eq);
    });
    card.addEventListener("click", (e) => {
      if (editMode) return;
      if (e.target.closest(".unit-edit-actions")) return;
      window.location.href = `/equipment/${eq.id}`;
    });
  });
}

function equipmentCardHtml(eq) {
  return `
    <div class="equipment-card ${editMode ? "edit-mode" : ""}" data-equipment-id="${eq.id}">
      <span class="unit-status-dot dot-${eq.overall_status}"></span>
      <span class="unit-icon">🏭</span>
      <div class="unit-name">${escapeHtml(eq.name)}</div>
      <div class="unit-part-count">${eq.unit_count}개 유닛</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="설비명 편집"><i class="bi bi-pencil"></i></button>
      </div>
    </div>`;
}

function openEquipmentEditModal(eq) {
  document.getElementById("equipmentEditId").value = eq.id;
  document.getElementById("equipmentEditName").value = eq.name;
  equipmentEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  equipmentEditModal = new bootstrap.Modal(document.getElementById("equipmentEditModal"));

  tick();
  setInterval(tick, 1000);
  loadEquipments();

  document.getElementById("editModeBtn").addEventListener("click", (e) => {
    editMode = !editMode;
    e.currentTarget.classList.toggle("btn-outline-light", !editMode);
    e.currentTarget.classList.toggle("btn-warning", editMode);
    loadEquipments();
  });

  document.getElementById("equipmentEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("equipmentEditId").value;
    const payload = {
      name: document.getElementById("equipmentEditName").value.trim(),
    };
    try {
      await fetchJson(`/api/equipments/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
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
  --pri: #1a3a5c;
  --bg: #eef1f5;
}

body {
  background: var(--bg);
  font-family: "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
}

.topbar {
  background: var(--pri);
  color: #fff;
  padding: 12px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; }
.clock { font-size: 12px; opacity: 0.8; }

.legend {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: #444;
}
.legend-item { display: flex; align-items: center; gap: 6px; }
.dot { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
.dot-ok { background: #22c55e; }
.dot-soon { background: #f59e0b; }
.dot-overdue { background: #ef4444; }
.dot-unknown { background: #9ca3af; }

.equipment-frame {
  background: linear-gradient(180deg, #f8fafc, #e2e8f0);
  border: 1px solid #cbd5e1;
  border-radius: 16px;
  padding: 28px 20px 20px;
  box-shadow: inset 0 0 0 6px #fff, 0 4px 16px rgba(0, 0, 0, 0.08);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 20px;
  background: var(--pri);
  color: #fff;
  font-size: 12px;
  padding: 4px 12px;
  border-radius: 999px;
}

.unit-shape {
  --shape-color: var(--pri);
  border: 4px solid var(--shape-color);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 40px;
  display: block;
  line-height: 1.2;
}
.unit-shape-name {
  font-size: 20px;
  font-weight: 700;
  color: var(--shape-color);
}

.equipment-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 16px;
  max-width: 1100px;
  margin: 0 auto;
}
.equipment-card {
  position: relative;
  background: #fff;
  border: 2px solid var(--pri);
  border-radius: 12px;
  padding: 18px 10px 14px;
  cursor: pointer;
  text-align: center;
  transition: box-shadow 0.15s, transform 0.15s;
}
.equipment-card:hover {
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.15);
  transform: translateY(-2px);
}
.equipment-card .unit-icon { font-size: 32px; }

.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

.unit-card {
  --uc: #1a3a5c;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: #fff;
  border: 2px solid var(--uc);
  border-radius: 12px;
  padding: 12px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.15s;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
}
.unit-card:hover {
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.15);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.22);
  z-index: 20;
  transition: none;
}
.unit-icon { font-size: 28px; display: block; margin-bottom: 6px; }
.unit-name { font-size: 13px; font-weight: 600; color: #1f2937; line-height: 1.3; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0,0,0,.1);
}
.unit-part-count {
  font-size: 11px;
  color: #6b7280;
  margin-top: 4px;
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
  background: rgba(0,0,0,.06);
  border-radius: 6px;
  font-size: 11px;
  padding: 2px 5px;
}
.unit-edit-actions button:hover { background: rgba(0,0,0,.14); }

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
  border-right: 2px solid rgba(0, 0, 0, 0.4);
  border-bottom: 2px solid rgba(0, 0, 0, 0.4);
}

.add-unit-btn { margin-top: 16px; display: block; margin-left: auto; margin-right: auto; }

.part-card {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 10px 14px;
  margin-bottom: 10px;
  background: #fafafa;
}
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: #6b7280; }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 6px 0; }

.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: #fff;
  border: 1px solid #dee2e6;
  border-radius: 12px;
  padding: 16px 18px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
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
  color: #2563eb;
  word-break: break-all;
}
#notesEdit { font-size: 14px; }
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
    <button id="addUnitBtn" class="btn btn-sm btn-outline-primary add-unit-btn d-none">
      <i class="bi bi-plus-lg"></i> 유닛 추가
    </button>
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

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
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

    makeDraggable(card, u);
    makeResizable(card, u);
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
      <span class="unit-icon">${u.icon}</span>
      <div class="unit-name">${escapeHtml(u.name)}</div>
      <div class="unit-part-count">${u.part_count}개 부품 등록</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="편집"><i class="bi bi-pencil"></i></button>
        <button class="delete-unit-btn" title="삭제"><i class="bi bi-trash"></i></button>
      </div>
      <div class="resize-handle" title="크기 조절"></div>
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

function openUnitEditModal(unit) {
  document.getElementById("unitEditTitle").textContent = unit ? "유닛 편집" : "유닛 추가";
  document.getElementById("unitEditId").value = unit ? unit.id : "";
  document.getElementById("unitEditName").value = unit ? unit.name : "";
  document.getElementById("unitEditIcon").value = unit ? unit.icon : "⚙️";
  document.getElementById("unitEditColor").value = unit ? unit.color : "#1a3a5c";
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
    loadUnits();
  });

  document.getElementById("addUnitBtn").addEventListener("click", () => openUnitEditModal(null));

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
  --pri: #1a3a5c;
  --bg: #eef1f5;
}

body {
  background: var(--bg);
  font-family: "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
}

.topbar {
  background: var(--pri);
  color: #fff;
  padding: 12px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; }
.clock { font-size: 12px; opacity: 0.8; }

.legend {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: #444;
}
.legend-item { display: flex; align-items: center; gap: 6px; }
.dot { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
.dot-ok { background: #22c55e; }
.dot-soon { background: #f59e0b; }
.dot-overdue { background: #ef4444; }
.dot-unknown { background: #9ca3af; }

.equipment-frame {
  background: linear-gradient(180deg, #f8fafc, #e2e8f0);
  border: 1px solid #cbd5e1;
  border-radius: 16px;
  padding: 28px 20px 20px;
  box-shadow: inset 0 0 0 6px #fff, 0 4px 16px rgba(0, 0, 0, 0.08);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 20px;
  background: var(--pri);
  color: #fff;
  font-size: 12px;
  padding: 4px 12px;
  border-radius: 999px;
}

.unit-shape {
  --shape-color: var(--pri);
  border: 4px solid var(--shape-color);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 40px;
  display: block;
  line-height: 1.2;
}
.unit-shape-name {
  font-size: 20px;
  font-weight: 700;
  color: var(--shape-color);
}

.equipment-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 16px;
  max-width: 1100px;
  margin: 0 auto;
}
.equipment-card {
  position: relative;
  background: #fff;
  border: 2px solid var(--pri);
  border-radius: 12px;
  padding: 18px 10px 14px;
  cursor: pointer;
  text-align: center;
  transition: box-shadow 0.15s, transform 0.15s;
}
.equipment-card:hover {
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.15);
  transform: translateY(-2px);
}
.equipment-card .unit-icon { font-size: 32px; }

.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

.unit-card {
  --uc: #1a3a5c;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: #fff;
  border: 2px solid var(--uc);
  border-radius: 12px;
  padding: 12px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.15s;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
}
.unit-card:hover {
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.15);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.22);
  z-index: 20;
  transition: none;
}
.unit-icon { font-size: 28px; display: block; margin-bottom: 6px; }
.unit-name { font-size: 13px; font-weight: 600; color: #1f2937; line-height: 1.3; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0,0,0,.1);
}
.unit-part-count {
  font-size: 11px;
  color: #6b7280;
  margin-top: 4px;
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
  background: rgba(0,0,0,.06);
  border-radius: 6px;
  font-size: 11px;
  padding: 2px 5px;
}
.unit-edit-actions button:hover { background: rgba(0,0,0,.14); }

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
  border-right: 2px solid rgba(0, 0, 0, 0.4);
  border-bottom: 2px solid rgba(0, 0, 0, 0.4);
}

.add-unit-btn { margin-top: 16px; display: block; margin-left: auto; margin-right: auto; }

.part-card {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 10px 14px;
  margin-bottom: 10px;
  background: #fafafa;
}
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: #6b7280; }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 6px 0; }

.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: #fff;
  border: 1px solid #dee2e6;
  border-radius: 12px;
  padding: 16px 18px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
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
  color: #2563eb;
  word-break: break-all;
}
#notesEdit { font-size: 14px; }
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
    <div class="equipment-label">부품을 클릭해서 교체일 관리 · 편집 모드에서 드래그로 배치/크기 변경</div>
    <div class="unit-shape-header">
      <span class="unit-shape-icon" id="unitIcon">⚙️</span>
      <span class="unit-shape-name" id="unitName">유닛</span>
    </div>
    <div id="partsCanvas" class="equipment-canvas"></div>
    <button id="addPartBtn" class="btn btn-sm btn-outline-primary add-unit-btn d-none">
      <i class="bi bi-plus-lg"></i> 부품 추가
    </button>
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
          <div class="mb-2">
            <label class="form-label">교체일</label>
            <input type="date" class="form-control" id="replaceDate" required>
          </div>
          <div class="mb-2">
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
          <div class="row g-2 mt-1">
            <div class="col-6">
              <label class="form-label">교체 주기(일)</label>
              <input type="number" class="form-control" id="partEditCycle" value="90" min="1" required>
            </div>
            <div class="col-6" id="partEditLastDateWrap">
              <label class="form-label">최초 교체일 (선택)</label>
              <input type="date" class="form-control" id="partEditLastDate">
            </div>
          </div>
          <div class="mb-2 mt-2">
            <label class="form-label">비고</label>
            <input type="text" class="form-control" id="partEditNote">
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

const statusColor = { ok: "#22c55e", soon: "#f59e0b", overdue: "#ef4444", unknown: "#9ca3af" };
const statusBadge = { ok: "badge-ok", soon: "badge-soon", overdue: "badge-overdue", unknown: "badge-unknown" };
const statusLabel = { ok: "정상", soon: "교체 임박", overdue: "교체 필요", unknown: "미기록" };

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
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

async function loadUnitHeader() {
  try {
    const unit = await fetchJson(`/api/units/${UNIT_ID}`);
    document.getElementById("unitPageTitle").textContent = unit.name;
    document.getElementById("unitIcon").textContent = unit.icon;
    document.getElementById("unitName").textContent = unit.name;
    document.getElementById("unitShape").style.setProperty("--shape-color", unit.color);
    document.getElementById("backToEquipmentBtn").href = `/equipment/${unit.equipment_id}`;
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

    makeDraggable(card, p);
    makeResizable(card, p);
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
      <span class="unit-icon">${p.icon}</span>
      <div class="unit-name">${escapeHtml(p.name)}</div>
      <div class="unit-part-count">${statusLabel[p.status]}</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="편집"><i class="bi bi-pencil"></i></button>
        <button class="delete-unit-btn" title="삭제"><i class="bi bi-trash"></i></button>
      </div>
      <div class="resize-handle" title="크기 조절"></div>
    </div>`;
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
      교체 주기: ${p.cycle_days}일 &middot; ${lastText}
      ${dueText ? `<br>${dueText}` : ""}
      ${p.note ? `<br>비고: ${escapeHtml(p.note)}` : ""}
    </div>`;
  partDetailModal.show();
}

function openReplaceModal() {
  document.getElementById("replaceDate").value = new Date().toISOString().slice(0, 10);
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
        <div><strong>${h.replaced_date}</strong> ${h.note ? " - " + escapeHtml(h.note) : ""}</div>
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
  document.getElementById("partEditIcon").value = part ? part.icon : "🔩";
  document.getElementById("partEditCycle").value = part ? part.cycle_days : 90;
  document.getElementById("partEditNote").value = part ? part.note || "" : "";
  document.getElementById("partEditLastDate").value = "";
  document.getElementById("partEditLastDateWrap").classList.toggle("d-none", !!part);
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

  document.getElementById("editModeBtn").addEventListener("click", (e) => {
    editMode = !editMode;
    e.currentTarget.classList.toggle("btn-outline-light", !editMode);
    e.currentTarget.classList.toggle("btn-warning", editMode);
    document.getElementById("addPartBtn").classList.toggle("d-none", !editMode);
    renderPartsCanvas(currentParts);
  });

  document.getElementById("addPartBtn").addEventListener("click", () => openPartEditModal(null));

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
    const payload = {
      name: document.getElementById("partEditName").value.trim(),
      spec: document.getElementById("partEditSpec").value.trim(),
      icon: document.getElementById("partEditIcon").value.trim(),
      cycle_days: parseInt(document.getElementById("partEditCycle").value, 10),
      note: document.getElementById("partEditNote").value.trim(),
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
  --pri: #1a3a5c;
  --bg: #eef1f5;
}

body {
  background: var(--bg);
  font-family: "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
}

.topbar {
  background: var(--pri);
  color: #fff;
  padding: 12px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 90;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}
.topbar h1 { font-size: 18px; font-weight: 700; margin: 0; }
.clock { font-size: 12px; opacity: 0.8; }

.legend {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: #444;
}
.legend-item { display: flex; align-items: center; gap: 6px; }
.dot { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
.dot-ok { background: #22c55e; }
.dot-soon { background: #f59e0b; }
.dot-overdue { background: #ef4444; }
.dot-unknown { background: #9ca3af; }

.equipment-frame {
  background: linear-gradient(180deg, #f8fafc, #e2e8f0);
  border: 1px solid #cbd5e1;
  border-radius: 16px;
  padding: 28px 20px 20px;
  box-shadow: inset 0 0 0 6px #fff, 0 4px 16px rgba(0, 0, 0, 0.08);
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.equipment-label {
  position: absolute;
  top: -14px;
  left: 20px;
  background: var(--pri);
  color: #fff;
  font-size: 12px;
  padding: 4px 12px;
  border-radius: 999px;
}

.unit-shape {
  --shape-color: var(--pri);
  border: 4px solid var(--shape-color);
}
.unit-shape-header {
  text-align: center;
  margin-bottom: 6px;
}
.unit-shape-icon {
  font-size: 40px;
  display: block;
  line-height: 1.2;
}
.unit-shape-name {
  font-size: 20px;
  font-weight: 700;
  color: var(--shape-color);
}

.equipment-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 16px;
  max-width: 1100px;
  margin: 0 auto;
}
.equipment-card {
  position: relative;
  background: #fff;
  border: 2px solid var(--pri);
  border-radius: 12px;
  padding: 18px 10px 14px;
  cursor: pointer;
  text-align: center;
  transition: box-shadow 0.15s, transform 0.15s;
}
.equipment-card:hover {
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.15);
  transform: translateY(-2px);
}
.equipment-card .unit-icon { font-size: 32px; }

.equipment-canvas {
  position: relative;
  width: 100%;
  min-height: 460px;
  margin-top: 10px;
}
@media (max-width: 768px) {
  .equipment-canvas { min-height: 620px; }
}

.unit-card {
  --uc: #1a3a5c;
  position: absolute;
  top: 0;
  left: 0;
  transform: translate(-50%, -50%);
  width: 140px;
  min-height: 110px;
  background: #fff;
  border: 2px solid var(--uc);
  border-radius: 12px;
  padding: 12px 10px 10px;
  cursor: pointer;
  transition: box-shadow 0.15s;
  text-align: center;
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
  overflow: hidden;
}
.unit-card:hover {
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.15);
  z-index: 5;
}
.edit-mode.unit-card { cursor: grab; }
.unit-card.dragging {
  cursor: grabbing;
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.22);
  z-index: 20;
  transition: none;
}
.unit-icon { font-size: 28px; display: block; margin-bottom: 6px; }
.unit-name { font-size: 13px; font-weight: 600; color: #1f2937; line-height: 1.3; }
.unit-status-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0,0,0,.1);
}
.unit-part-count {
  font-size: 11px;
  color: #6b7280;
  margin-top: 4px;
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
  background: rgba(0,0,0,.06);
  border-radius: 6px;
  font-size: 11px;
  padding: 2px 5px;
}
.unit-edit-actions button:hover { background: rgba(0,0,0,.14); }

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
  border-right: 2px solid rgba(0, 0, 0, 0.4);
  border-bottom: 2px solid rgba(0, 0, 0, 0.4);
}

.add-unit-btn { margin-top: 16px; display: block; margin-left: auto; margin-right: auto; }

.part-card {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 10px 14px;
  margin-bottom: 10px;
  background: #fafafa;
}
.part-card .part-title { font-weight: 700; font-size: 14px; }
.part-card .part-spec { font-size: 12px; color: #6b7280; }
.badge-ok { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.badge-overdue { background: #fee2e2; color: #991b1b; }
.badge-unknown { background: #e5e7eb; color: #374151; }

.history-row { font-size: 13px; border-bottom: 1px solid #f1f1f1; padding: 6px 0; }

.notes-section {
  max-width: 1100px;
  margin: 20px auto 0;
  background: #fff;
  border: 1px solid #dee2e6;
  border-radius: 12px;
  padding: 16px 18px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
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
  color: #2563eb;
  word-break: break-all;
}
#notesEdit { font-size: 14px; }
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
    <button id="addUnitBtn" class="btn btn-sm btn-outline-primary add-unit-btn">
      <i class="bi bi-plus-lg"></i> 유닛 추가
    </button>
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
          <button type="submit" class="btn btn-primary w-100 mt-3">저장</button>
        </form>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
let unitEditModal;

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
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

    makeDraggable(card, t);
    makeResizable(card, t);
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
      <span class="unit-icon">${t.icon}</span>
      <div class="unit-name">${escapeHtml(t.name)}</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="편집"><i class="bi bi-pencil"></i></button>
        <button class="delete-unit-btn" title="삭제"><i class="bi bi-trash"></i></button>
      </div>
      <div class="resize-handle" title="크기 조절"></div>
    </div>`;
}

function openUnitEditModal(template) {
  document.getElementById("unitEditTitle").textContent = template ? "유닛 편집" : "유닛 추가";
  document.getElementById("unitEditId").value = template ? template.id : "";
  document.getElementById("unitEditName").value = template ? template.name : "";
  document.getElementById("unitEditIcon").value = template ? template.icon : "⚙️";
  document.getElementById("unitEditColor").value = template ? template.color : "#1a3a5c";
  unitEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  unitEditModal = new bootstrap.Modal(document.getElementById("unitEditModal"));

  tick();
  setInterval(tick, 1000);
  loadTemplates();

  document.getElementById("addUnitBtn").addEventListener("click", () => openUnitEditModal(null));

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


if __name__ == "__main__":
    init_db()
    lan_ip = get_lan_ip()
    print("설비 부품 교체 관리 시스템 시작!")
    print(f"  이 컴퓨터에서 접속: http://localhost:5000")
    print(f"  같은 네트워크의 다른 사람 접속: http://{lan_ip}:5000")
    print("  (다른 사람이 접속 안 되면 Windows 방화벽에서 Python 허용 여부를 확인하세요)")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
