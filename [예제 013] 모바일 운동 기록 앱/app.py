from flask import Flask, render_template, request, jsonify
import sqlite3
import os
from datetime import datetime, date

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "workout.db")

DEFAULT_EXERCISES = [
    ("벤치프레스", "가슴"),
    ("스쿼트", "하체"),
    ("데드리프트", "등/하체"),
    ("풀업", "등"),
    ("오버헤드프레스", "어깨"),
    ("바벨로우", "등"),
    ("렛풀다운", "등"),
    ("레그프레스", "하체"),
    ("덤벨컬", "이두"),
    ("트라이셉스 익스텐션", "삼두"),
    ("플랭크", "코어"),
    ("런닝", "유산소"),
    ("사이클", "유산소"),
    ("점핑잭", "유산소"),
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS workout_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_id INTEGER NOT NULL,
            workout_date TEXT NOT NULL,
            set_number INTEGER NOT NULL,
            reps INTEGER,
            weight REAL,
            duration_sec INTEGER,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (exercise_id) REFERENCES exercises(id)
        )
    """)
    for name, category in DEFAULT_EXERCISES:
        c.execute(
            "INSERT OR IGNORE INTO exercises (name, category) VALUES (?, ?)",
            (name, category),
        )
    conn.commit()
    conn.close()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/exercises")
def get_exercises():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, category FROM exercises ORDER BY category, name"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/exercises", methods=["POST"])
def add_exercise():
    data = request.get_json()
    name = data.get("name", "").strip()
    category = data.get("category", "기타").strip()
    if not name:
        return jsonify({"error": "이름을 입력하세요"}), 400
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO exercises (name, category) VALUES (?, ?)", (name, category)
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, name, category FROM exercises WHERE name = ?", (name,)
        ).fetchone()
        conn.close()
        return jsonify(dict(row)), 201
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "이미 존재하는 운동입니다"}), 409


@app.route("/api/sets/<workout_date>")
def get_sets(workout_date):
    conn = get_db()
    rows = conn.execute(
        """
        SELECT ws.id, ws.exercise_id, e.name AS exercise_name, e.category,
               ws.set_number, ws.reps, ws.weight, ws.duration_sec, ws.note
        FROM workout_sets ws
        JOIN exercises e ON ws.exercise_id = e.id
        WHERE ws.workout_date = ?
        ORDER BY ws.exercise_id, ws.set_number
        """,
        (workout_date,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/sets", methods=["POST"])
def add_set():
    data = request.get_json()
    exercise_id = data.get("exercise_id")
    workout_date = data.get("workout_date", str(date.today()))
    reps = data.get("reps")
    weight = data.get("weight")
    duration_sec = data.get("duration_sec")
    note = data.get("note", "")

    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(MAX(set_number), 0) + 1 AS next_set FROM workout_sets WHERE exercise_id = ? AND workout_date = ?",
        (exercise_id, workout_date),
    ).fetchone()
    set_number = row["next_set"]

    conn.execute(
        """
        INSERT INTO workout_sets (exercise_id, workout_date, set_number, reps, weight, duration_sec, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (exercise_id, workout_date, set_number, reps, weight, duration_sec, note),
    )
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute(
        """
        SELECT ws.id, ws.exercise_id, e.name AS exercise_name, e.category,
               ws.set_number, ws.reps, ws.weight, ws.duration_sec, ws.note
        FROM workout_sets ws JOIN exercises e ON ws.exercise_id = e.id
        WHERE ws.id = ?
        """,
        (new_id,),
    ).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


@app.route("/api/sets/<int:set_id>", methods=["DELETE"])
def delete_set(set_id):
    conn = get_db()
    conn.execute("DELETE FROM workout_sets WHERE id = ?", (set_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/history")
def get_history():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT ws.workout_date,
               COUNT(DISTINCT ws.exercise_id) AS exercise_count,
               COUNT(ws.id) AS total_sets,
               GROUP_CONCAT(DISTINCT e.name) AS exercises
        FROM workout_sets ws
        JOIN exercises e ON ws.exercise_id = e.id
        GROUP BY ws.workout_date
        ORDER BY ws.workout_date DESC
        LIMIT 30
        """
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/personal_records/<int:exercise_id>")
def get_personal_records(exercise_id):
    conn = get_db()
    row = conn.execute(
        """
        SELECT MAX(weight) AS max_weight, MAX(reps) AS max_reps
        FROM workout_sets
        WHERE exercise_id = ? AND weight IS NOT NULL
        """,
        (exercise_id,),
    ).fetchone()
    conn.close()
    return jsonify(dict(row) if row else {})


if __name__ == "__main__":
    init_db()
    print("운동 기록 앱 시작! → http://localhost:5000")
    print("모바일: 같은 Wi-Fi에서 http://<내 IP>:5000 으로 접속")
    app.run(host="0.0.0.0", port=5000, debug=True)
