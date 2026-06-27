from flask import Flask, render_template, request, jsonify
import sqlite3
from datetime import datetime
import os

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'dashboard.db')


def get_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS parts_replacement (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT NOT NULL,
        uploaded_at TEXT NOT NULL,
        seq INTEGER DEFAULT 0,
        model TEXT DEFAULT '',
        part_name TEXT DEFAULT '',
        target_qty INTEGER DEFAULT 0,
        completed_qty INTEGER DEFAULT 0,
        progress_rate REAL DEFAULT 0,
        remarks TEXT DEFAULT ''
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS improvements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT NOT NULL,
        uploaded_at TEXT NOT NULL,
        seq INTEGER DEFAULT 0,
        improvement_item TEXT DEFAULT '',
        target_count INTEGER DEFAULT 0,
        completed_count INTEGER DEFAULT 0,
        progress_rate REAL DEFAULT 0,
        manager TEXT DEFAULT '',
        remarks TEXT DEFAULT ''
    )''')
    conn.commit()
    conn.close()


init_db()


def safe_int(val):
    try:
        return int(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/parts', methods=['POST'])
def save_parts():
    rows = request.json.get('rows', [])
    if not rows:
        return jsonify({'success': False, 'error': '데이터가 없습니다'}), 400

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    batch_id = datetime.now().strftime('%Y%m%d_%H%M%S')

    conn = get_db()
    c = conn.cursor()
    inserted = 0
    for i, row in enumerate(rows):
        target = safe_int(row.get('target_qty', 0))
        completed = safe_int(row.get('completed_qty', 0))
        progress = round(completed / target * 100, 1) if target > 0 else 0
        c.execute(
            'INSERT INTO parts_replacement (batch_id, uploaded_at, seq, model, part_name, target_qty, completed_qty, progress_rate, remarks) VALUES (?,?,?,?,?,?,?,?,?)',
            (batch_id, now, i + 1, str(row.get('model', '')), str(row.get('part_name', '')),
             target, completed, progress, str(row.get('remarks', '')))
        )
        inserted += 1
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'batch_id': batch_id, 'count': inserted})


@app.route('/api/improvements', methods=['POST'])
def save_improvements():
    rows = request.json.get('rows', [])
    if not rows:
        return jsonify({'success': False, 'error': '데이터가 없습니다'}), 400

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    batch_id = datetime.now().strftime('%Y%m%d_%H%M%S')

    conn = get_db()
    c = conn.cursor()
    inserted = 0
    for i, row in enumerate(rows):
        target = safe_int(row.get('target_count', 0))
        completed = safe_int(row.get('completed_count', 0))
        progress = round(completed / target * 100, 1) if target > 0 else 0
        c.execute(
            'INSERT INTO improvements (batch_id, uploaded_at, seq, improvement_item, target_count, completed_count, progress_rate, manager, remarks) VALUES (?,?,?,?,?,?,?,?,?)',
            (batch_id, now, i + 1, str(row.get('improvement_item', '')),
             target, completed, progress, str(row.get('manager', '')), str(row.get('remarks', '')))
        )
        inserted += 1
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'batch_id': batch_id, 'count': inserted})


@app.route('/api/summary')
def get_summary():
    conn = get_db()
    c = conn.cursor()

    c.execute('SELECT batch_id, uploaded_at FROM parts_replacement ORDER BY uploaded_at DESC LIMIT 1')
    row = c.fetchone()
    parts_data, parts_batch = [], None
    if row:
        bid, ts = row['batch_id'], row['uploaded_at']
        c.execute('SELECT * FROM parts_replacement WHERE batch_id=? ORDER BY seq', (bid,))
        parts_data = [dict(r) for r in c.fetchall()]
        parts_batch = {'batch_id': bid, 'uploaded_at': ts}

    c.execute('SELECT batch_id, uploaded_at FROM improvements ORDER BY uploaded_at DESC LIMIT 1')
    row = c.fetchone()
    imp_data, imp_batch = [], None
    if row:
        bid, ts = row['batch_id'], row['uploaded_at']
        c.execute('SELECT * FROM improvements WHERE batch_id=? ORDER BY seq', (bid,))
        imp_data = [dict(r) for r in c.fetchall()]
        imp_batch = {'batch_id': bid, 'uploaded_at': ts}

    def overall(items, tkey, ckey):
        tt = sum(r[tkey] for r in items)
        tc = sum(r[ckey] for r in items)
        return round(tc / tt * 100, 1) if tt > 0 else 0

    conn.close()
    return jsonify({
        'parts': parts_data,
        'improvements': imp_data,
        'parts_overall': overall(parts_data, 'target_qty', 'completed_qty'),
        'improvements_overall': overall(imp_data, 'target_count', 'completed_count'),
        'parts_batch': parts_batch,
        'improvements_batch': imp_batch
    })


@app.route('/api/history/parts')
def parts_history():
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT batch_id, MIN(uploaded_at) as uploaded_at, COUNT(*) as items,
                 SUM(target_qty) as total_target, SUM(completed_qty) as total_completed,
                 ROUND(SUM(completed_qty)*100.0/NULLIF(SUM(target_qty),0),1) as overall
                 FROM parts_replacement GROUP BY batch_id ORDER BY uploaded_at DESC LIMIT 50''')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route('/api/history/improvements')
def improvements_history():
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT batch_id, MIN(uploaded_at) as uploaded_at, COUNT(*) as items,
                 SUM(target_count) as total_target, SUM(completed_count) as total_completed,
                 ROUND(SUM(completed_count)*100.0/NULLIF(SUM(target_count),0),1) as overall
                 FROM improvements GROUP BY batch_id ORDER BY uploaded_at DESC LIMIT 50''')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route('/api/history/parts/<batch_id>')
def parts_batch(batch_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM parts_replacement WHERE batch_id=? ORDER BY seq', (batch_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route('/api/history/improvements/<batch_id>')
def improvements_batch(batch_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM improvements WHERE batch_id=? ORDER BY seq', (batch_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route('/api/trend/parts')
def parts_trend():
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT MIN(uploaded_at) as date,
                 ROUND(SUM(completed_qty)*100.0/NULLIF(SUM(target_qty),0),1) as overall
                 FROM parts_replacement GROUP BY batch_id ORDER BY date ASC LIMIT 20''')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route('/api/trend/improvements')
def improvements_trend():
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT MIN(uploaded_at) as date,
                 ROUND(SUM(completed_count)*100.0/NULLIF(SUM(target_count),0),1) as overall
                 FROM improvements GROUP BY batch_id ORDER BY date ASC LIMIT 20''')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')
