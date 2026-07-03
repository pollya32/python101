"""Smart Semiconductor Parts Management — AI 기반 반도체 장비 부품 관리 시스템

실행:  python app.py            (기본 포트 8000)
접속:  http://<이 PC의 IP>:8000  — 같은 네트워크의 팀원(~15명)이 브라우저로 접속
DB:    이 폴더의 smart_parts.db (SQLite, 호스트 PC 로컬 파일)

기본 계정: admin / admin1234,  engineer01~09 · viewer01~05 / semi1234
"""

import csv
import functools
import io
import os
import random
import socket
import sys
from datetime import date, datetime, timedelta

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash as _gen_hash

import db as dbm
from analytics import (check_stock_alerts, detect_anomaly, estimate_rul,
                       get_recommendations, mtbf_mttr, prediction_accuracy,
                       update_health, used_days_of, FAILURE_CAUSES)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'smart-parts-2026-secret')
app.config['MAX_USERS'] = 15                      # 공유 사용 인원 제한
API_TOKEN = os.environ.get('SENSOR_API_TOKEN', 'sensor-token-2026')

ROLE_LABEL = {'admin': '관리자', 'engineer': '엔지니어', 'viewer': '조회 전용'}


def hash_pw(pw):
    try:
        return _gen_hash(pw, method='pbkdf2:sha256')
    except Exception:
        return _gen_hash(pw)


def get_db():
    if 'db' not in g:
        g.db = dbm.get_db()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop('db', None)
    if conn:
        conn.close()


# ── 인증 ────────────────────────────────────────────────────────────────────

def login_required(view=None, *, roles=None):
    """roles=None: 로그인만 요구, roles=('admin',): 해당 역할만 허용"""
    def deco(v):
        @functools.wraps(v)
        def wrapped(*a, **kw):
            if not session.get('uid'):
                return redirect(url_for('login', next=request.path))
            if roles and session.get('role') not in roles:
                flash('권한이 없습니다.', 'error')
                return redirect(url_for('dashboard'))
            return v(*a, **kw)
        return wrapped
    return deco(view) if view else deco


def can_edit():
    return session.get('role') in ('admin', 'engineer')


@app.context_processor
def inject_globals():
    unread = 0
    if session.get('uid'):
        unread = get_db().execute(
            "SELECT COUNT(*) FROM alerts WHERE is_read=0").fetchone()[0]
    return {'unread_alerts': unread, 'can_edit': can_edit(),
            'ROLE_LABEL': ROLE_LABEL, 'today': date.today().isoformat()}


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username', '').strip()
        pw = request.form.get('password', '')
        row = get_db().execute(
            "SELECT * FROM users WHERE username=? AND active=1", (u,)).fetchone()
        if row and check_password_hash(row['password'], pw):
            session.clear()
            session['uid'] = row['id']
            session['username'] = row['username']
            session['name'] = row['name']
            session['role'] = row['role']
            return redirect(request.args.get('next') or url_for('dashboard'))
        flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── 대시보드 ────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def dashboard():
    conn = get_db()
    kpi = {
        'equipment_total': conn.execute("SELECT COUNT(*) FROM equipment").fetchone()[0],
        'equipment_running': conn.execute(
            "SELECT COUNT(*) FROM equipment WHERE status='가동중'").fetchone()[0],
        'parts_in_use': conn.execute(
            "SELECT COUNT(*) FROM parts WHERE status='사용중'").fetchone()[0],
        'avg_health': conn.execute(
            "SELECT ROUND(AVG(health_index),1) FROM parts WHERE status='사용중'").fetchone()[0],
        'unread_alerts': conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE is_read=0").fetchone()[0],
        'low_stock': conn.execute(
            "SELECT COUNT(*) FROM inventory WHERE stock_qty < safety_stock").fetchone()[0],
    }
    kpi.update(mtbf_mttr(conn))
    kpi['accuracy'] = prediction_accuracy(conn)

    recs = get_recommendations(conn)[:5]
    alerts = conn.execute(
        "SELECT a.*, p.part_code FROM alerts a LEFT JOIN parts p ON p.id=a.part_id "
        "ORDER BY a.is_read, a.created_at DESC LIMIT 6").fetchall()
    low_stock = conn.execute(
        "SELECT * FROM inventory WHERE stock_qty < safety_stock ORDER BY "
        "(safety_stock - stock_qty) DESC LIMIT 5").fetchall()

    # 열화 트렌드 차트: Health 하위 5개 부품의 최근 60일 이력
    worst = conn.execute(
        "SELECT id, part_code, replacement_limit FROM parts WHERE status='사용중' "
        "ORDER BY health_index ASC LIMIT 5").fetchall()
    since = (date.today() - timedelta(days=60)).isoformat()
    trend = []
    for w in worst:
        rows = conn.execute(
            "SELECT date, health_index FROM health_history WHERE part_id=? AND date>=? "
            "ORDER BY date", (w['id'], since)).fetchall()
        trend.append({'label': w['part_code'],
                      'data': [{'x': r['date'], 'y': r['health_index']} for r in rows]})
    limit_avg = round(sum(w['replacement_limit'] for w in worst) / len(worst), 1) if worst else 20

    cause_rows = conn.execute(
        "SELECT failure_cause, COUNT(*) n FROM replacements WHERE status='Completed' "
        "GROUP BY failure_cause ORDER BY n DESC").fetchall()
    return render_template('dashboard.html', kpi=kpi, recs=recs, alerts=alerts,
                           low_stock=low_stock, trend=trend, limit_avg=limit_avg,
                           causes=[dict(r) for r in cause_rows])


# ── 부품 관리 ───────────────────────────────────────────────────────────────

def _part_form_ctx(conn):
    return {'equipment': conn.execute("SELECT * FROM equipment ORDER BY code").fetchall(),
            'categories': [r[0] for r in conn.execute(
                "SELECT DISTINCT category FROM parts WHERE category IS NOT NULL "
                "ORDER BY category").fetchall()]}


@app.route('/parts')
@login_required
def parts():
    conn = get_db()
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '')
    category = request.args.get('category', '')
    eq = request.args.get('equipment_id', '')
    sql = ("SELECT p.*, e.name AS equipment_name, e.code AS equipment_code FROM parts p "
           "LEFT JOIN equipment e ON e.id=p.equipment_id WHERE 1=1")
    args = []
    if q:
        sql += " AND (p.part_code LIKE ? OR p.name LIKE ?)"
        args += [f'%{q}%', f'%{q}%']
    if status:
        sql += " AND p.status=?"
        args.append(status)
    if category:
        sql += " AND p.category=?"
        args.append(category)
    if eq:
        sql += " AND p.equipment_id=?"
        args.append(eq)
    sql += " ORDER BY p.health_index ASC, p.part_code"
    rows = conn.execute(sql, args).fetchall()
    items = [{'p': p, 'used': used_days_of(p)} for p in rows]
    ctx = _part_form_ctx(conn)
    return render_template('parts.html', items=items, q=q, status=status,
                           category=category, equipment_id=eq, **ctx)


@app.route('/parts/new', methods=['GET', 'POST'])
@login_required(roles=('admin', 'engineer'))
def part_new():
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        try:
            conn.execute(
                "INSERT INTO parts(part_code,name,category,equipment_id,install_date,"
                "expected_life_days,replacement_limit,unit_cost,supplier,status,note) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (f['part_code'].strip(), f['name'].strip(), f.get('category') or None,
                 f.get('equipment_id') or None, f.get('install_date') or None,
                 int(f.get('expected_life_days') or 180),
                 float(f.get('replacement_limit') or 20),
                 int(f.get('unit_cost') or 0), f.get('supplier') or None,
                 f.get('status') or '사용중', f.get('note') or None))
            conn.commit()
            flash('부품이 등록되었습니다.', 'ok')
            return redirect(url_for('parts'))
        except Exception as e:
            flash(f'등록 실패: {e}', 'error')
    return render_template('part_form.html', part=None, **_part_form_ctx(conn))


@app.route('/parts/<int:pid>')
@login_required
def part_detail(pid):
    conn = get_db()
    part = conn.execute(
        "SELECT p.*, e.name AS equipment_name, e.code AS equipment_code FROM parts p "
        "LEFT JOIN equipment e ON e.id=p.equipment_id WHERE p.id=?", (pid,)).fetchone()
    if not part:
        abort(404)
    rul = estimate_rul(conn, part)
    history = conn.execute(
        "SELECT date, health_index FROM health_history WHERE part_id=? ORDER BY date",
        (pid,)).fetchall()
    sensors = conn.execute(
        "SELECT * FROM sensor_readings WHERE part_id=? ORDER BY ts DESC LIMIT 30",
        (pid,)).fetchall()
    anomalies = conn.execute(
        "SELECT * FROM sensor_readings WHERE part_id=? AND is_anomaly=1 "
        "ORDER BY ts DESC LIMIT 10", (pid,)).fetchall()
    repls = conn.execute(
        "SELECT r.*, e.name AS equipment_name FROM replacements r "
        "LEFT JOIN equipment e ON e.id=r.equipment_id WHERE r.part_code=? "
        "ORDER BY r.replaced_at DESC", (part['part_code'],)).fetchall()
    return render_template('part_detail.html', part=part, rul=rul,
                           used=used_days_of(part),
                           health=[{'x': h['date'], 'y': h['health_index']} for h in history],
                           sensors=sensors, anomalies=anomalies, repls=repls)


@app.route('/parts/<int:pid>/edit', methods=['GET', 'POST'])
@login_required(roles=('admin', 'engineer'))
def part_edit(pid):
    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE id=?", (pid,)).fetchone()
    if not part:
        abort(404)
    if request.method == 'POST':
        f = request.form
        try:
            conn.execute(
                "UPDATE parts SET part_code=?,name=?,category=?,equipment_id=?,install_date=?,"
                "expected_life_days=?,replacement_limit=?,unit_cost=?,supplier=?,status=?,note=? "
                "WHERE id=?",
                (f['part_code'].strip(), f['name'].strip(), f.get('category') or None,
                 f.get('equipment_id') or None, f.get('install_date') or None,
                 int(f.get('expected_life_days') or 180),
                 float(f.get('replacement_limit') or 20),
                 int(f.get('unit_cost') or 0), f.get('supplier') or None,
                 f.get('status') or '사용중', f.get('note') or None, pid))
            update_health(conn, pid, create_alert=False)
            conn.commit()
            flash('부품 정보가 수정되었습니다.', 'ok')
            return redirect(url_for('part_detail', pid=pid))
        except Exception as e:
            flash(f'수정 실패: {e}', 'error')
    return render_template('part_form.html', part=part, **_part_form_ctx(conn))


@app.route('/parts/<int:pid>/delete', methods=['POST'])
@login_required(roles=('admin',))
def part_delete(pid):
    conn = get_db()
    conn.execute("DELETE FROM parts WHERE id=?", (pid,))
    conn.commit()
    flash('부품이 삭제되었습니다.', 'ok')
    return redirect(url_for('parts'))


@app.route('/parts/<int:pid>/qr.svg')
@login_required
def part_qr(pid):
    part = get_db().execute("SELECT * FROM parts WHERE id=?", (pid,)).fetchone()
    if not part:
        abort(404)
    payload = f"SPM|{part['part_code']}|{part['name']}"
    try:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(payload, image_factory=qrcode.image.svg.SvgPathImage,
                          box_size=12, border=2)
        buf = io.BytesIO()
        img.save(buf)
        svg = buf.getvalue()
    except Exception:
        svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="180" height="60">'
               b'<text x="10" y="35" font-size="14">' + payload.encode() + b'</text></svg>')
    return Response(svg, mimetype='image/svg+xml')


# ── 장비 관리 ───────────────────────────────────────────────────────────────

@app.route('/equipment')
@login_required
def equipment():
    conn = get_db()
    rows = conn.execute(
        "SELECT e.*, COUNT(p.id) AS part_count, ROUND(AVG(p.health_index),1) AS avg_health "
        "FROM equipment e LEFT JOIN parts p ON p.equipment_id=e.id AND p.status='사용중' "
        "GROUP BY e.id ORDER BY e.code").fetchall()
    stats = {r['id']: mtbf_mttr(get_db(), r['id']) for r in rows}
    return render_template('equipment.html', rows=rows, stats=stats)


@app.route('/equipment/new', methods=['GET', 'POST'])
@app.route('/equipment/<int:eid>/edit', methods=['GET', 'POST'])
@login_required(roles=('admin', 'engineer'))
def equipment_form(eid=None):
    conn = get_db()
    row = conn.execute("SELECT * FROM equipment WHERE id=?", (eid,)).fetchone() if eid else None
    if eid and not row:
        abort(404)
    if request.method == 'POST':
        f = request.form
        vals = (f['code'].strip(), f['name'].strip(), f.get('etype') or None,
                f.get('location') or None, f.get('status') or '가동중',
                f.get('installed_at') or None, f.get('note') or None)
        try:
            if eid:
                conn.execute("UPDATE equipment SET code=?,name=?,etype=?,location=?,"
                             "status=?,installed_at=?,note=? WHERE id=?", vals + (eid,))
            else:
                conn.execute("INSERT INTO equipment(code,name,etype,location,status,"
                             "installed_at,note) VALUES(?,?,?,?,?,?,?)", vals)
            conn.commit()
            flash('저장되었습니다.', 'ok')
            return redirect(url_for('equipment'))
        except Exception as e:
            flash(f'저장 실패: {e}', 'error')
    return render_template('equipment_form.html', row=row)


@app.route('/equipment/<int:eid>/delete', methods=['POST'])
@login_required(roles=('admin',))
def equipment_delete(eid):
    conn = get_db()
    conn.execute("DELETE FROM equipment WHERE id=?", (eid,))
    conn.commit()
    flash('장비가 삭제되었습니다.', 'ok')
    return redirect(url_for('equipment'))


# ── 센서 / FDC ─────────────────────────────────────────────────────────────

@app.route('/sensors')
@login_required
def sensors():
    conn = get_db()
    only_anomaly = request.args.get('anomaly') == '1'
    pid = request.args.get('part_id', '')
    sql = ("SELECT s.*, p.part_code, p.name AS part_name FROM sensor_readings s "
           "JOIN parts p ON p.id=s.part_id WHERE 1=1")
    args = []
    if only_anomaly:
        sql += " AND s.is_anomaly=1"
    if pid:
        sql += " AND s.part_id=?"
        args.append(pid)
    sql += " ORDER BY s.ts DESC LIMIT 200"
    rows = conn.execute(sql, args).fetchall()
    parts_list = conn.execute(
        "SELECT id, part_code FROM parts WHERE status='사용중' ORDER BY part_code").fetchall()
    anomaly_count = conn.execute(
        "SELECT COUNT(*) FROM sensor_readings WHERE is_anomaly=1 "
        "AND ts >= datetime('now','localtime','-7 days')").fetchone()[0]
    return render_template('sensors.html', rows=rows, parts_list=parts_list,
                           only_anomaly=only_anomaly, part_id=pid,
                           anomaly_count=anomaly_count, api_token=API_TOKEN)


def _ingest_reading(conn, part, reading, ts=None):
    """센서 1건 수집 → FDC 판정 → 저장 → Health 갱신 → 알람 (공용 로직)"""
    is_a, param, mode = detect_anomaly(conn, part['id'], reading)
    conn.execute(
        "INSERT INTO sensor_readings(part_id,ts,pressure,temperature,rf_power,"
        "is_anomaly,anomaly_param,fault_mode) VALUES(?,COALESCE(?,datetime('now','localtime')),?,?,?,?,?,?)",
        (part['id'], ts, reading.get('pressure'), reading.get('temperature'),
         reading.get('rf_power'), is_a, param, mode))
    if is_a:
        conn.execute(
            "INSERT INTO alerts(atype,severity,part_id,message) VALUES('FDC','critical',?,?)",
            (part['id'], f"[FDC 이상] {part['part_code']} {part['name']} — {mode}"))
    update_health(conn, part['id'])
    return is_a


@app.route('/sensors/simulate', methods=['POST'])
@login_required(roles=('admin', 'engineer'))
def sensors_simulate():
    """사용중 부품 전체에 센서 측정 1사이클을 시뮬레이션한다(데모/교육용)."""
    conn = get_db()
    base_map = {'Pump': (0.05, 45, None), 'Seal': (0.05, 60, None),
                'RF': (None, 40, 1500), 'Electrode': (0.04, 80, 1500),
                'Gas Delivery': (0.06, 75, None), 'Thermal': (None, 350, None),
                'Valve': (0.05, 50, None), 'Filter': (0.07, 30, None),
                'Chamber': (0.05, 90, 1500)}
    parts_rows = conn.execute("SELECT * FROM parts WHERE status='사용중'").fetchall()
    n_anom = 0
    for p in parts_rows:
        bp, bt, br = base_map.get(p['category'], (0.05, 50, None))
        wear = min(1.0, used_days_of(p) / max(1, p['expected_life_days']))
        spike = 5.0 if random.random() < (0.01 + wear * 0.04) else 1.0
        reading = {
            'pressure': round(bp * (1 + wear * 0.15) + random.gauss(0, bp * 0.01) * spike, 4) if bp else None,
            'temperature': round(bt * (1 + wear * 0.075) + random.gauss(0, bt * 0.008) * spike, 2) if bt else None,
            'rf_power': round(br * (1 - wear * 0.045) + random.gauss(0, br * 0.006) * spike, 1) if br else None,
        }
        n_anom += _ingest_reading(conn, p, reading)
    conn.commit()
    flash(f'센서 수집 시뮬레이션 완료 — {len(parts_rows)}건 수집, FDC 이상 {n_anom}건 감지', 'ok')
    return redirect(url_for('sensors'))


@app.route('/api/sensor', methods=['POST'])
def api_sensor():
    """설비 데이터 수집 REST API (Data Ingestion)

    POST /api/sensor  Header: X-API-KEY: <token>
    Body: {"part_code": "CH-VP-001", "pressure": 0.051, "temperature": 45.2, "rf_power": 1500}
    """
    if request.headers.get('X-API-KEY') != API_TOKEN:
        return jsonify({'error': 'invalid api key'}), 401
    data = request.get_json(silent=True) or {}
    code = data.get('part_code')
    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE part_code=?", (code,)).fetchone()
    if not part:
        return jsonify({'error': f'unknown part_code: {code}'}), 404
    reading = {k: data.get(k) for k in ('pressure', 'temperature', 'rf_power')}
    is_a = _ingest_reading(conn, part, reading, ts=data.get('ts'))
    conn.commit()
    return jsonify({'ok': True, 'part_code': code, 'anomaly': bool(is_a)})


@app.route('/api/parts/<int:pid>/health')
@login_required
def api_part_health(pid):
    rows = get_db().execute(
        "SELECT date, health_index FROM health_history WHERE part_id=? ORDER BY date",
        (pid,)).fetchall()
    return jsonify([dict(r) for r in rows])


# ── AI 교체 추천 ────────────────────────────────────────────────────────────

@app.route('/recommendations')
@login_required
def recommendations():
    conn = get_db()
    recs = get_recommendations(conn)
    total_saving = sum(r['saving'] for r in recs)
    return render_template('recommendations.html', recs=recs, total_saving=total_saving)


@app.route('/recommendations/<int:pid>/approve', methods=['POST'])
@login_required(roles=('admin', 'engineer'))
def recommendation_approve(pid):
    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE id=?", (pid,)).fetchone()
    if not part:
        abort(404)
    rec_date = request.form.get('recommended_date') or date.today().isoformat()
    r = estimate_rul(conn, part)
    conn.execute(
        "INSERT INTO replacements(part_code,part_name,category,equipment_id,replaced_at,"
        "predicted_life,failure_cause,status,cost,note) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (part['part_code'], part['name'], part['category'], part['equipment_id'],
         rec_date, used_days_of(part) + r['rul'], 'Preventive', 'Scheduled',
         part['unit_cost'], f"AI 추천 승인 (RUL {r['rul']}일, {session.get('name')})"))
    conn.execute("UPDATE parts SET status='교체예정' WHERE id=?", (pid,))
    conn.execute(
        "INSERT INTO alerts(atype,severity,part_id,message) VALUES('RECOMMEND','info',?,?)",
        (pid, f"[교체 예약] {part['part_code']} {part['name']} — {rec_date} 교체 예정 "
              f"(승인: {session.get('name')})"))
    conn.commit()
    flash(f"{part['part_code']} 교체가 {rec_date}로 예약되었습니다.", 'ok')
    return redirect(url_for('recommendations'))


# ── 교체 이력 (Phase 4) ─────────────────────────────────────────────────────

@app.route('/history')
@login_required
def history():
    conn = get_db()
    q = request.args.get('q', '').strip()
    cause = request.args.get('cause', '')
    status = request.args.get('status', '')
    sql = ("SELECT r.*, e.name AS equipment_name FROM replacements r "
           "LEFT JOIN equipment e ON e.id=r.equipment_id WHERE 1=1")
    args = []
    if q:
        sql += " AND (r.part_code LIKE ? OR r.part_name LIKE ?)"
        args += [f'%{q}%', f'%{q}%']
    if cause:
        sql += " AND r.failure_cause=?"
        args.append(cause)
    if status:
        sql += " AND r.status=?"
        args.append(status)
    sql += " ORDER BY r.replaced_at DESC"
    rows = conn.execute(sql, args).fetchall()

    if request.args.get('export') == 'csv':
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['부품 ID', '부품명', '카테고리', '장비명', '교체일자', '사용 일수',
                    '고장 원인', '상태', '수리시간(h)', '비용', '작업자'])
        for r in rows:
            w.writerow([r['part_code'], r['part_name'], r['category'], r['equipment_name'],
                        r['replaced_at'], r['used_days'], r['failure_cause'], r['status'],
                        r['repair_hours'], r['cost'], r['performed_by']])
        out = '\ufeff' + buf.getvalue()  # Excel 한글 호환 BOM
        return Response(out, mimetype='text/csv', headers={
            'Content-Disposition': 'attachment; filename=replacement_history.csv'})
    return render_template('history.html', rows=rows, q=q, cause=cause, status=status,
                           causes=FAILURE_CAUSES)


@app.route('/history/new', methods=['GET', 'POST'])
@login_required(roles=('admin', 'engineer'))
def history_new():
    conn = get_db()
    parts_rows = conn.execute(
        "SELECT p.*, e.name AS equipment_name FROM parts p "
        "LEFT JOIN equipment e ON e.id=p.equipment_id "
        "WHERE p.status IN ('사용중','교체예정') ORDER BY p.part_code").fetchall()
    if request.method == 'POST':
        f = request.form
        pid = int(f['part_id'])
        part = conn.execute("SELECT * FROM parts WHERE id=?", (pid,)).fetchone()
        if not part:
            abort(404)
        used = used_days_of(part)
        r = estimate_rul(conn, part)
        conn.execute(
            "INSERT INTO replacements(part_code,part_name,category,equipment_id,replaced_at,"
            "used_days,predicted_life,failure_cause,status,repair_hours,cost,performed_by,note) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (part['part_code'], part['name'], part['category'], part['equipment_id'],
             f.get('replaced_at') or date.today().isoformat(), used, used + r['rul'],
             f.get('failure_cause') or 'Wear & Tear', 'Completed',
             float(f.get('repair_hours') or 0) or None, part['unit_cost'],
             session.get('name'), f.get('note') or None))
        # 예정 건이 있으면 완료 처리
        conn.execute(
            "UPDATE replacements SET status='Completed', used_days=?, performed_by=? "
            "WHERE part_code=? AND status='Scheduled'",
            (used, session.get('name'), part['part_code']))
        if f.get('renew') == '1':
            # 신품 교체: 장착일 리셋, Health 100
            conn.execute("UPDATE parts SET install_date=?, health_index=100, status='사용중' "
                         "WHERE id=?", (date.today().isoformat(), pid))
            conn.execute("DELETE FROM health_history WHERE part_id=?", (pid,))
            conn.execute("DELETE FROM sensor_readings WHERE part_id=?", (pid,))
            update_health(conn, pid, create_alert=False)
            # 재고 차감
            inv = conn.execute("SELECT * FROM inventory WHERE name=?", (part['name'],)).fetchone()
            if inv and inv['stock_qty'] > 0:
                conn.execute("UPDATE inventory SET stock_qty=stock_qty-1, "
                             "updated_at=datetime('now','localtime') WHERE id=?", (inv['id'],))
        else:
            conn.execute("UPDATE parts SET status='교체완료' WHERE id=?", (pid,))
        check_stock_alerts(conn)
        conn.commit()
        flash('교체 이력이 등록되었습니다.', 'ok')
        return redirect(url_for('history'))
    return render_template('history_form.html', parts_list=parts_rows, causes=FAILURE_CAUSES)


# ── 알람 ────────────────────────────────────────────────────────────────────

@app.route('/alerts')
@login_required
def alerts():
    conn = get_db()
    atype = request.args.get('type', '')
    sql = ("SELECT a.*, p.part_code FROM alerts a LEFT JOIN parts p ON p.id=a.part_id "
           "WHERE 1=1")
    args = []
    if atype:
        sql += " AND a.atype=?"
        args.append(atype)
    if request.args.get('unread') == '1':
        sql += " AND a.is_read=0"
    sql += " ORDER BY a.is_read, a.created_at DESC LIMIT 300"
    rows = conn.execute(sql, args).fetchall()
    return render_template('alerts.html', rows=rows, atype=atype,
                           unread=request.args.get('unread') == '1')


@app.route('/alerts/<int:aid>/read', methods=['POST'])
@login_required
def alert_read(aid):
    conn = get_db()
    conn.execute("UPDATE alerts SET is_read=1 WHERE id=?", (aid,))
    conn.commit()
    return redirect(request.referrer or url_for('alerts'))


@app.route('/alerts/read-all', methods=['POST'])
@login_required
def alerts_read_all():
    conn = get_db()
    conn.execute("UPDATE alerts SET is_read=1")
    conn.commit()
    flash('모든 알람을 확인 처리했습니다.', 'ok')
    return redirect(url_for('alerts'))


# ── 재고 (Safety Stock) ────────────────────────────────────────────────────

@app.route('/inventory')
@login_required
def inventory():
    conn = get_db()
    rows = conn.execute("SELECT * FROM inventory ORDER BY "
                        "(stock_qty < safety_stock) DESC, item_code").fetchall()
    # 예상 교체 수요: 30일 내 RUL 도래 부품 수를 품목명 기준으로 집계
    demand = {}
    for r in get_recommendations(conn):
        if r['rul'] <= 30:
            demand[r['part']['name']] = demand.get(r['part']['name'], 0) + 1
    items = []
    for it in rows:
        d = demand.get(it['name'], 0)
        shortage = max(0, it['safety_stock'] - it['stock_qty'])
        suggest = shortage + d
        items.append({'it': it, 'demand': d, 'suggest': suggest})
    return render_template('inventory.html', items=items)


@app.route('/inventory/new', methods=['GET', 'POST'])
@app.route('/inventory/<int:iid>/edit', methods=['GET', 'POST'])
@login_required(roles=('admin', 'engineer'))
def inventory_form(iid=None):
    conn = get_db()
    row = conn.execute("SELECT * FROM inventory WHERE id=?", (iid,)).fetchone() if iid else None
    if iid and not row:
        abort(404)
    if request.method == 'POST':
        f = request.form
        vals = (f['item_code'].strip(), f['name'].strip(), f.get('category') or None,
                int(f.get('stock_qty') or 0), int(f.get('safety_stock') or 2),
                int(f.get('unit_cost') or 0), int(f.get('lead_time_days') or 14),
                f.get('supplier') or None, f.get('note') or None)
        try:
            if iid:
                conn.execute("UPDATE inventory SET item_code=?,name=?,category=?,stock_qty=?,"
                             "safety_stock=?,unit_cost=?,lead_time_days=?,supplier=?,note=?,"
                             "updated_at=datetime('now','localtime') WHERE id=?", vals + (iid,))
            else:
                conn.execute("INSERT INTO inventory(item_code,name,category,stock_qty,"
                             "safety_stock,unit_cost,lead_time_days,supplier,note) "
                             "VALUES(?,?,?,?,?,?,?,?,?)", vals)
            check_stock_alerts(conn)
            conn.commit()
            flash('저장되었습니다.', 'ok')
            return redirect(url_for('inventory'))
        except Exception as e:
            flash(f'저장 실패: {e}', 'error')
    return render_template('inventory_form.html', row=row)


@app.route('/inventory/<int:iid>/order', methods=['POST'])
@login_required(roles=('admin', 'engineer'))
def inventory_order(iid):
    conn = get_db()
    it = conn.execute("SELECT * FROM inventory WHERE id=?", (iid,)).fetchone()
    if not it:
        abort(404)
    qty = int(request.form.get('qty') or 1)
    conn.execute("UPDATE inventory SET stock_qty=stock_qty+?, "
                 "updated_at=datetime('now','localtime') WHERE id=?", (qty, iid))
    conn.execute("INSERT INTO alerts(atype,severity,message) VALUES('STOCK','info',?)",
                 (f"[발주 입고] {it['item_code']} {it['name']} {qty}EA 입고 처리 "
                  f"(처리: {session.get('name')})",))
    conn.commit()
    flash(f"{it['name']} {qty}EA 입고 처리되었습니다.", 'ok')
    return redirect(url_for('inventory'))


@app.route('/inventory/<int:iid>/delete', methods=['POST'])
@login_required(roles=('admin',))
def inventory_delete(iid):
    conn = get_db()
    conn.execute("DELETE FROM inventory WHERE id=?", (iid,))
    conn.commit()
    flash('재고 품목이 삭제되었습니다.', 'ok')
    return redirect(url_for('inventory'))


# ── 분석 (MTBF/MTTR/Visual Analytics) ──────────────────────────────────────

@app.route('/analytics')
@login_required
def analytics_page():
    conn = get_db()
    overall = mtbf_mttr(conn)
    accuracy = prediction_accuracy(conn)
    eq_rows = conn.execute("SELECT * FROM equipment ORDER BY code").fetchall()
    per_eq = [{'eq': e, **mtbf_mttr(conn, e['id'])} for e in eq_rows]
    causes = [dict(r) for r in conn.execute(
        "SELECT failure_cause, COUNT(*) n FROM replacements WHERE status='Completed' "
        "GROUP BY failure_cause ORDER BY n DESC").fetchall()]
    cats = [dict(r) for r in conn.execute(
        "SELECT category, COUNT(*) n, ROUND(AVG(used_days),0) avg_used "
        "FROM replacements WHERE status='Completed' AND category IS NOT NULL "
        "GROUP BY category ORDER BY n DESC").fetchall()]
    monthly = [dict(r) for r in conn.execute(
        "SELECT substr(replaced_at,1,7) ym, COUNT(*) n FROM replacements "
        "WHERE status='Completed' AND replaced_at >= date('now','-12 months') "
        "GROUP BY ym ORDER BY ym").fetchall()]
    return render_template('analytics.html', overall=overall, accuracy=accuracy,
                           per_eq=per_eq, causes=causes, cats=cats, monthly=monthly)


# ── 사용자 관리 (15명 공유) ─────────────────────────────────────────────────

@app.route('/users')
@login_required(roles=('admin',))
def users():
    rows = get_db().execute("SELECT * FROM users ORDER BY role, username").fetchall()
    return render_template('users.html', rows=rows, max_users=app.config['MAX_USERS'])


@app.route('/users/new', methods=['GET', 'POST'])
@app.route('/users/<int:uid>/edit', methods=['GET', 'POST'])
@login_required(roles=('admin',))
def user_form(uid=None):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() if uid else None
    if uid and not row:
        abort(404)
    if request.method == 'POST':
        f = request.form
        try:
            if uid:
                conn.execute("UPDATE users SET name=?, role=?, email=?, active=? WHERE id=?",
                             (f['name'].strip(), f['role'], f.get('email') or None,
                              1 if f.get('active') else 0, uid))
                if f.get('password'):
                    conn.execute("UPDATE users SET password=? WHERE id=?",
                                 (hash_pw(f['password']), uid))
            else:
                n_active = conn.execute(
                    "SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]
                if n_active >= app.config['MAX_USERS']:
                    flash(f"최대 사용자 수({app.config['MAX_USERS']}명)에 도달했습니다. "
                          "기존 계정을 비활성화한 뒤 추가하세요.", 'error')
                    return redirect(url_for('users'))
                conn.execute("INSERT INTO users(username,password,name,role,email) "
                             "VALUES(?,?,?,?,?)",
                             (f['username'].strip(), hash_pw(f['password'] or 'semi1234'),
                              f['name'].strip(), f['role'], f.get('email') or None))
            conn.commit()
            flash('저장되었습니다.', 'ok')
            return redirect(url_for('users'))
        except Exception as e:
            flash(f'저장 실패: {e}', 'error')
    return render_template('user_form.html', row=row)


@app.route('/users/<int:uid>/delete', methods=['POST'])
@login_required(roles=('admin',))
def user_delete(uid):
    if uid == session.get('uid'):
        flash('본인 계정은 삭제할 수 없습니다.', 'error')
        return redirect(url_for('users'))
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    flash('사용자가 삭제되었습니다.', 'ok')
    return redirect(url_for('users'))


# ── 실행 ────────────────────────────────────────────────────────────────────

def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


if __name__ == '__main__':
    dbm.init_db()
    if dbm.is_empty():
        from seed import seed
        seed()
    port = int(os.environ.get('PORT', sys.argv[1] if len(sys.argv) > 1 else 8000))
    ip = _lan_ip()
    print('=' * 62)
    print('  Smart Semiconductor Parts Management')
    print(f'  이 PC에서 접속   : http://127.0.0.1:{port}')
    print(f'  팀원 공유 주소   : http://{ip}:{port}   (같은 네트워크, ~15명)')
    print(f'  DB 파일          : {dbm.DB_PATH}')
    print('  기본 계정        : admin / admin1234')
    print('=' * 62)
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port, threads=16)
    except ImportError:
        app.run(host='0.0.0.0', port=port, threaded=True)
