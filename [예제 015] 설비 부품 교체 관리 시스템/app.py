from flask import Flask, render_template, request, jsonify, send_file
import sqlite3
import os
import random
import socket
from datetime import date, datetime, timedelta

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "equipment.db")

EQUIPMENT_COUNT = 20
EQUIPMENT_PREFIX = "TEAG"


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


def init_db():
    conn = get_db()
    c = conn.cursor()

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


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/alerts")
def alerts_page():
    return render_template("alerts.html")


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


@app.route("/api/alerts")
def api_alerts():
    return jsonify(get_alert_parts())


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
    lan_ip = get_lan_ip()
    print("설비 부품 교체 관리 시스템 시작!")
    print(f"  이 컴퓨터에서 접속: http://localhost:5000")
    print(f"  같은 네트워크의 다른 사람 접속: http://{lan_ip}:5000")
    print("  (다른 사람이 접속 안 되면 Windows 방화벽에서 Python 허용 여부를 확인하세요)")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
