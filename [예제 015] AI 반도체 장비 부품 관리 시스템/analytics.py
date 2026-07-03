"""분석 엔진 — FDC 이상 감지, Health Index, RUL 예측, MTBF/MTTR, 교체 추천

기획서(Smart Semiconductor Parts Management)의 데이터 흐름을 구현한다.
  01 설비 센싱 → 02 FDC 감지 → 03 AI 분석(RUL) → 04 교체 추천 → 05 이력 관리
"""

from datetime import date, datetime, timedelta

# FDC 분류: 이상 파라미터 → 고장 모드(원인 부품) 매핑
FAULT_MAP = {
    'pressure':    'Pressure Drift — Seal/Valve 마모 의심',
    'temperature': 'Thermal Anomaly — Heater/Chiller 열화 의심',
    'rf_power':    'RF Mismatch — RF Generator/Matcher 점검 필요',
}

FAILURE_CAUSES = ['Wear & Tear', 'Early Failure', 'Preventive', 'Process Drift', 'Contamination']

# Preventive(예방 교체)는 고장이 아니므로 MTBF 계산에서 제외한다.
NON_FAILURE_CAUSES = ('Preventive',)


def _today():
    return date.today()


def used_days_of(part):
    """부품 장착 후 경과 일수"""
    if not part['install_date']:
        return 0
    d = datetime.strptime(part['install_date'][:10], '%Y-%m-%d').date()
    return max(0, (_today() - d).days)


# ── 02. FDC 이상 감지 (3-sigma SPC) ─────────────────────────────────────────

def detect_anomaly(conn, part_id, reading):
    """직전 60개 측정치 대비 3σ를 벗어나면 이상으로 판정하고 고장 모드를 분류한다.

    reading: {'pressure':…, 'temperature':…, 'rf_power':…}
    returns (is_anomaly, param, fault_mode)
    """
    rows = conn.execute(
        "SELECT pressure, temperature, rf_power FROM sensor_readings "
        "WHERE part_id=? ORDER BY id DESC LIMIT 60", (part_id,)).fetchall()
    if len(rows) < 20:               # 통계가 부족하면 판정 보류
        return 0, None, None
    worst = (0, None)                # (편차 배수, 파라미터)
    for param in ('pressure', 'temperature', 'rf_power'):
        vals = [r[param] for r in rows if r[param] is not None]
        x = reading.get(param)
        if x is None or len(vals) < 20:
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        std = var ** 0.5
        if std <= 1e-9:
            continue
        z = abs(x - mean) / std
        if z > 3 and z > worst[0]:
            worst = (z, param)
    if worst[1]:
        return 1, worst[1], FAULT_MAP[worst[1]]
    return 0, None, None


# ── 03. Health Index & RUL 예측 ─────────────────────────────────────────────

def compute_health(conn, part):
    """사용률 기반 열화 + 최근 FDC 이상 페널티로 Health Index(0~100)를 산출한다."""
    used = used_days_of(part)
    life = max(1, part['expected_life_days'])
    base = 100.0 * max(0.0, 1.0 - used / life) ** 0.9   # 완만한 초기 열화 곡선
    recent = conn.execute(
        "SELECT COUNT(*) FROM sensor_readings WHERE part_id=? AND is_anomaly=1 "
        "AND ts >= datetime('now','localtime','-14 days')", (part['id'],)).fetchone()[0]
    penalty = min(30.0, recent * 2.5)                    # 최근 2주 이상 1건당 -2.5
    return round(max(0.0, min(100.0, base - penalty)), 1)


def update_health(conn, part_id, create_alert=True):
    """Health Index를 재계산해 저장하고, 임계 접근 시 Smart Alarm을 생성한다."""
    part = conn.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone()
    if not part:
        return None
    h = compute_health(conn, part)
    today = _today().isoformat()
    conn.execute("UPDATE parts SET health_index=? WHERE id=?", (h, part_id))
    conn.execute(
        "INSERT INTO health_history(part_id, date, health_index) VALUES(?,?,?) "
        "ON CONFLICT(part_id, date) DO UPDATE SET health_index=excluded.health_index",
        (part_id, today, h))
    limit = part['replacement_limit']
    if create_alert and part['status'] == '사용중' and h <= limit + 10:
        sev = 'critical' if h <= limit else 'warning'
        dup = conn.execute(
            "SELECT 1 FROM alerts WHERE part_id=? AND atype='LIFE' AND is_read=0 "
            "AND severity=?", (part_id, sev)).fetchone()
        if not dup:
            conn.execute(
                "INSERT INTO alerts(atype, severity, part_id, message) VALUES(?,?,?,?)",
                ('LIFE', sev, part_id,
                 f"[수명 임계] {part['part_code']} {part['name']} Health {h} "
                 f"(교체 임계치 {limit}) — 교체 준비 필요"))
    return h


def estimate_rul(conn, part):
    """잔여 수명(RUL) 예측 — 최근 30일 Health 추세를 선형 회귀로 외삽한다.

    returns {'rul': 일수, 'method': 'trend'|'tbm', 'predicted_date': 'YYYY-MM-DD'}
    """
    limit = part['replacement_limit']
    used = used_days_of(part)
    tbm_rul = max(0, part['expected_life_days'] - used)
    rows = conn.execute(
        "SELECT date, health_index FROM health_history WHERE part_id=? "
        "ORDER BY date DESC LIMIT 30", (part['id'],)).fetchall()
    rows = list(reversed(rows))
    if len(rows) >= 7:
        base = datetime.strptime(rows[0]['date'], '%Y-%m-%d').date()
        xs = [(datetime.strptime(r['date'], '%Y-%m-%d').date() - base).days for r in rows]
        ys = [r['health_index'] for r in rows]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx > 0:
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
            if slope < -0.01:                    # 유의미한 열화 추세
                cur = part['health_index']
                rul = int(max(0.0, (cur - limit) / (-slope)))
                rul = min(rul, tbm_rul + 90)     # 비현실적 외삽 방지
                return {'rul': rul, 'method': 'trend',
                        'predicted_date': (_today() + timedelta(days=rul)).isoformat()}
    return {'rul': tbm_rul, 'method': 'tbm',
            'predicted_date': (_today() + timedelta(days=tbm_rul)).isoformat()}


# ── 04. 지능형 교체 추천 ────────────────────────────────────────────────────

def get_recommendations(conn):
    """사용중 부품 전체를 평가해 교체 추천 목록을 만든다.

    추천 기준: RUL 30일 이내 또는 Health가 임계치+15 이하.
    교체 권장일 = 예측 고장일 - 리드타임 여유(3일).
    TBM 대비 절감 = AI 권장일이 TBM 일자보다 늦은 만큼 잔여 수명을 더 활용.
    """
    recs = []
    parts = conn.execute(
        "SELECT p.*, e.name AS equipment_name, e.code AS equipment_code "
        "FROM parts p LEFT JOIN equipment e ON e.id = p.equipment_id "
        "WHERE p.status = '사용중'").fetchall()
    for p in parts:
        r = estimate_rul(conn, p)
        used = used_days_of(p)
        tbm_remain = max(0, p['expected_life_days'] - used)
        near_limit = p['health_index'] <= p['replacement_limit'] + 15
        if r['rul'] > 30 and not near_limit:
            continue
        urgency = 'critical' if (r['rul'] <= 7 or p['health_index'] <= p['replacement_limit']) \
            else ('warning' if r['rul'] <= 21 or near_limit else 'info')
        rec_date = _today() + timedelta(days=max(0, r['rul'] - 3))
        gained = max(0, r['rul'] - tbm_remain)   # TBM보다 더 쓰는 일수
        saving = int(p['unit_cost'] * gained / max(1, p['expected_life_days']))
        recs.append({
            'part': p, 'rul': r['rul'], 'method': r['method'],
            'predicted_date': r['predicted_date'], 'used_days': used,
            'tbm_remain': tbm_remain, 'urgency': urgency,
            'recommended_date': rec_date.isoformat(),
            'gained_days': gained, 'saving': saving,
        })
    order = {'critical': 0, 'warning': 1, 'info': 2}
    recs.sort(key=lambda x: (order[x['urgency']], x['rul']))
    return recs


# ── 05. 핵심 관리 지표: MTBF / MTTR / 예측 정확도 ──────────────────────────

def mtbf_mttr(conn, equipment_id=None):
    """MTBF = Σ가동시간 / 고장횟수(예방 교체 제외), MTTR = Σ수리시간 / 수리횟수"""
    where, args = "status='Completed'", []
    if equipment_id:
        where += " AND equipment_id=?"
        args.append(equipment_id)
    rows = conn.execute(
        f"SELECT used_days, repair_hours, failure_cause FROM replacements WHERE {where}",
        args).fetchall()
    failures = [r for r in rows if r['failure_cause'] not in NON_FAILURE_CAUSES]
    op_days = sum(r['used_days'] or 0 for r in rows)
    mtbf = round(op_days / len(failures), 1) if failures else None   # 일 단위
    repairs = [r['repair_hours'] for r in rows if r['repair_hours']]
    mttr = round(sum(repairs) / len(repairs), 1) if repairs else None  # 시간 단위
    return {'mtbf_days': mtbf, 'mttr_hours': mttr,
            'failure_count': len(failures), 'repair_count': len(repairs)}


def prediction_accuracy(conn):
    """AI 예측 수명 vs 실제 사용 일수 비교 (완료된 교체 건 기준)"""
    rows = conn.execute(
        "SELECT predicted_life, used_days FROM replacements "
        "WHERE status='Completed' AND predicted_life IS NOT NULL "
        "AND used_days IS NOT NULL AND used_days > 0").fetchall()
    if not rows:
        return None
    errs = [abs(r['predicted_life'] - r['used_days']) / r['used_days'] for r in rows]
    return round(max(0.0, 100.0 * (1 - sum(errs) / len(errs))), 1)


# ── Safety Stock ────────────────────────────────────────────────────────────

def check_stock_alerts(conn):
    """안전 재고 미달 품목에 대해 STOCK 알람을 생성한다."""
    items = conn.execute(
        "SELECT * FROM inventory WHERE stock_qty < safety_stock").fetchall()
    for it in items:
        dup = conn.execute(
            "SELECT 1 FROM alerts WHERE atype='STOCK' AND is_read=0 AND message LIKE ?",
            (f"%{it['item_code']}%",)).fetchone()
        if not dup:
            conn.execute(
                "INSERT INTO alerts(atype, severity, message) VALUES(?,?,?)",
                ('STOCK', 'warning',
                 f"[안전 재고] {it['item_code']} {it['name']} 재고 {it['stock_qty']} < "
                 f"안전재고 {it['safety_stock']} — 발주 필요 (리드타임 {it['lead_time_days']}일)"))
    return len(items)
