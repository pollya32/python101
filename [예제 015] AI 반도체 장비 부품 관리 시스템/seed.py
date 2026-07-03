"""데모 데이터 시딩 — 장비/부품/센서/이력/재고/사용자(15명)

최초 실행 시 자동으로 호출되어 현실적인 데모 데이터를 생성한다.
"""

import random
from datetime import date, datetime, timedelta

from werkzeug.security import generate_password_hash as _gen_hash

from db import get_db
from analytics import check_stock_alerts, update_health

random.seed(42)


def hash_pw(pw):
    try:
        return _gen_hash(pw, method='pbkdf2:sha256')
    except Exception:
        return _gen_hash(pw)


EQUIPMENT = [
    ('ETCH-A01', 'Etcher A-01', 'Dry Etcher', 'FAB1-Bay3', '가동중'),
    ('ETCH-A02', 'Etcher A-02', 'Dry Etcher', 'FAB1-Bay3', '가동중'),
    ('CVD-B04',  'CVD B-04',   'PECVD',      'FAB1-Bay5', '가동중'),
    ('LITHO-C02', 'Litho C-02', 'Scanner',    'FAB2-Bay1', 'PM중'),
    ('DIFF-D01', 'Diffusion D-01', 'Furnace', 'FAB2-Bay2', '가동중'),
    ('CMP-E03',  'CMP E-03',   'CMP',        'FAB1-Bay7', '가동중'),
]

# (코드 prefix, 이름, 카테고리, 수명일, 단가, 공급사, 센서 기준값(P/T/RF))
PART_TYPES = [
    ('VP', 'Vacuum Pump',    'Pump',        365, 8500000, 'Edwards',   (0.05, 45, None)),
    ('OR', 'O-Ring Seal Kit', 'Seal',        120, 350000,  'DuPont',    (0.05, 60, None)),
    ('RF', 'RF Generator',   'RF',          400, 12000000, 'AE',        (None, 40, 1500)),
    ('MT', 'RF Matcher',     'RF',          300, 6800000,  'AE',        (None, 38, 1480)),
    ('EL', 'Upper Electrode', 'Electrode',   150, 4200000,  'Lam',       (0.04, 80, 1500)),
    ('SH', 'Shower Head',    'Gas Delivery', 180, 5600000, 'AMAT',      (0.06, 75, None)),
    ('HT', 'Ceramic Heater', 'Thermal',     240, 7300000,  'NGK',       (None, 350, None)),
    ('SV', 'Slit Valve',     'Valve',       200, 1900000,  'VAT',       (0.05, 50, None)),
    ('FL', 'Line Filter',    'Filter',      90,  280000,   'Pall',      (0.07, 30, None)),
    ('CL', 'Chamber Liner',  'Chamber',     160, 3900000,  'AMAT',      (0.05, 90, 1500)),
]

CAUSES_W = [('Wear & Tear', 45), ('Preventive', 25), ('Early Failure', 12),
            ('Process Drift', 12), ('Contamination', 6)]


def _pick_cause():
    r = random.uniform(0, 100)
    acc = 0
    for c, w in CAUSES_W:
        acc += w
        if r <= acc:
            return c
    return 'Wear & Tear'


def seed():
    conn = get_db()
    c = conn.cursor()
    today = date.today()

    # ── 사용자 15명 (admin 1 + engineer 9 + viewer 5) ──
    users = [('admin', 'admin1234', '시스템 관리자', 'admin', 'system-admin@company.com')]
    eng_names = ['김철수', '이영희', '박민준', '최수빈', '정도현', '한지민', '오세훈', '서예진', '남주혁']
    for i, nm in enumerate(eng_names, 1):
        users.append((f'engineer{i:02d}', 'semi1234', nm, 'engineer',
                      f'engineer{i:02d}@company.com'))
    view_names = ['조현우', '윤아름', '장민서', '임태양', '강하늘']
    for i, nm in enumerate(view_names, 1):
        users.append((f'viewer{i:02d}', 'semi1234', nm, 'viewer',
                      f'viewer{i:02d}@company.com'))
    for u, pw, nm, role, em in users:
        c.execute("INSERT OR IGNORE INTO users(username,password,name,role,email) "
                  "VALUES(?,?,?,?,?)", (u, hash_pw(pw), nm, role, em))

    # ── 장비 ──
    for code, name, etype, loc, st in EQUIPMENT:
        inst = (today - timedelta(days=random.randint(400, 1500))).isoformat()
        c.execute("INSERT OR IGNORE INTO equipment(code,name,etype,location,status,installed_at) "
                  "VALUES(?,?,?,?,?,?)", (code, name, etype, loc, st, inst))
    eq_ids = [r['id'] for r in c.execute("SELECT id FROM equipment").fetchall()]

    # ── 부품 (장비당 4~5개) + 예비 부품 ──
    seq = {}
    part_rows = []
    for eq in eq_ids:
        for prefix, name, cat, life, cost, sup, base in random.sample(PART_TYPES, 5):
            seq[prefix] = seq.get(prefix, 0) + 1
            code = f"CH-{prefix}-{seq[prefix]:03d}"
            # 열화 정도를 다양하게: 수명의 5% ~ 95% 사용
            used = int(life * random.uniform(0.05, 0.95))
            inst = (today - timedelta(days=used)).isoformat()
            c.execute(
                "INSERT INTO parts(part_code,name,category,equipment_id,install_date,"
                "expected_life_days,unit_cost,supplier,status) VALUES(?,?,?,?,?,?,?,?, '사용중')",
                (code, name, cat, eq, inst, life, cost, sup))
            part_rows.append((c.lastrowid, life, used, base))
    # 예비 부품 3개
    for prefix, name, cat, life, cost, sup, base in random.sample(PART_TYPES, 3):
        seq[prefix] = seq.get(prefix, 0) + 1
        code = f"CH-{prefix}-{seq[prefix]:03d}"
        c.execute(
            "INSERT INTO parts(part_code,name,category,expected_life_days,unit_cost,"
            "supplier,status) VALUES(?,?,?,?,?,?, '예비')",
            (code, name, cat, life, cost, sup))

    # ── Health 이력 (최근 90일, 일 단위) ──
    for pid, life, used, base in part_rows:
        span = min(90, used)
        for d in range(span, -1, -1):
            day = today - timedelta(days=d)
            age = used - d
            h = 100.0 * max(0.0, 1.0 - age / life) ** 0.9
            h += random.uniform(-1.5, 1.5)
            h = round(max(0.0, min(100.0, h)), 1)
            c.execute("INSERT OR REPLACE INTO health_history(part_id,date,health_index) "
                      "VALUES(?,?,?)", (pid, day.isoformat(), h))

    # ── 센서 데이터 (최근 7일, 4시간 간격) — 열화가 심할수록 드리프트/이상 증가 ──
    for pid, life, used, base in part_rows:
        bp, bt, br = base
        wear = min(1.0, used / life)
        for step in range(7 * 6, 0, -1):
            ts = datetime.now() - timedelta(hours=4 * step)
            drift = wear * 0.15
            anomaly = random.random() < (0.005 + wear * 0.03)
            spike = 5.0 if anomaly else 1.0
            p = round(bp * (1 + drift) + random.gauss(0, bp * 0.01) * spike, 4) if bp else None
            t = round(bt * (1 + drift * 0.5) + random.gauss(0, bt * 0.008) * spike, 2) if bt else None
            rf = round(br * (1 - drift * 0.3) + random.gauss(0, br * 0.006) * spike, 1) if br else None
            c.execute(
                "INSERT INTO sensor_readings(part_id,ts,pressure,temperature,rf_power) "
                "VALUES(?,?,?,?,?)", (pid, ts.strftime('%Y-%m-%d %H:%M:%S'), p, t, rf))

    # ── 교체 이력 (지난 18개월, 42건) ──
    eq_all = c.execute("SELECT id FROM equipment").fetchall()
    hist_seq = 100
    performers = ['김철수', '이영희', '박민준', '정도현', '한지민']
    for _ in range(42):
        prefix, name, cat, life, cost, sup, base = random.choice(PART_TYPES)
        hist_seq += 1
        code = f"CH-{prefix}-{hist_seq}"
        cause = _pick_cause()
        if cause == 'Early Failure':
            used_d = int(life * random.uniform(0.05, 0.35))
        elif cause == 'Preventive':
            used_d = int(life * random.uniform(0.75, 0.95))
        else:
            used_d = int(life * random.uniform(0.7, 1.15))
        pred = int(used_d * random.uniform(0.92, 1.08))     # 예측 오차 ±8%
        when = today - timedelta(days=random.randint(5, 540))
        c.execute(
            "INSERT INTO replacements(part_code,part_name,category,equipment_id,replaced_at,"
            "used_days,predicted_life,failure_cause,status,repair_hours,cost,performed_by) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, name, cat, random.choice(eq_all)['id'], when.isoformat(), used_d, pred,
             cause, 'Completed', round(random.uniform(0.5, 8.0), 1), cost,
             random.choice(performers)))
    # 기획서 예시와 같은 진행중/예정 건
    etch = c.execute("SELECT id FROM equipment WHERE code='ETCH-A01'").fetchone()['id']
    cvd = c.execute("SELECT id FROM equipment WHERE code='CVD-B04'").fetchone()['id']
    litho = c.execute("SELECT id FROM equipment WHERE code='LITHO-C02'").fetchone()['id']
    c.executemany(
        "INSERT INTO replacements(part_code,part_name,category,equipment_id,replaced_at,"
        "used_days,failure_cause,status,repair_hours,cost,performed_by) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [('CH-VP-901', 'Vacuum Pump', 'Pump', etch, (today - timedelta(days=30)).isoformat(),
          142, 'Wear & Tear', 'Completed', 4.5, 8500000, '김철수'),
         ('CH-VP-902', 'Vacuum Pump', 'Pump', cvd, (today - timedelta(days=12)).isoformat(),
          12, 'Early Failure', 'In Use', 3.0, 8500000, '이영희'),
         ('CH-SN-105', 'Shower Head', 'Gas Delivery', litho,
          (today + timedelta(days=14)).isoformat(), None, 'Preventive', 'Scheduled',
          None, 5600000, None)])

    # ── 재고 (Safety Stock) ──
    inv = [
        ('INV-VP-01', 'Vacuum Pump',     'Pump',        2, 2, 8500000, 30, 'Edwards'),
        ('INV-OR-01', 'O-Ring Seal Kit', 'Seal',        1, 5, 350000,  7,  'DuPont'),
        ('INV-RF-01', 'RF Generator',    'RF',          1, 1, 12000000, 45, 'AE'),
        ('INV-MT-01', 'RF Matcher',      'RF',          2, 1, 6800000, 30, 'AE'),
        ('INV-EL-01', 'Upper Electrode', 'Electrode',   3, 2, 4200000, 21, 'Lam'),
        ('INV-SH-01', 'Shower Head',     'Gas Delivery', 1, 2, 5600000, 21, 'AMAT'),
        ('INV-HT-01', 'Ceramic Heater',  'Thermal',     2, 1, 7300000, 28, 'NGK'),
        ('INV-SV-01', 'Slit Valve',      'Valve',       4, 2, 1900000, 14, 'VAT'),
        ('INV-FL-01', 'Line Filter',     'Filter',      3, 6, 280000,  5,  'Pall'),
        ('INV-CL-01', 'Chamber Liner',   'Chamber',     1, 1, 3900000, 21, 'AMAT'),
    ]
    for row in inv:
        c.execute("INSERT OR IGNORE INTO inventory(item_code,name,category,stock_qty,"
                  "safety_stock,unit_cost,lead_time_days,supplier) VALUES(?,?,?,?,?,?,?,?)", row)

    conn.commit()

    # ── FDC 재판정(시딩 데이터에 3σ 룰 적용) + Health/알람 갱신 ──
    for pid, life, used, base in part_rows:
        rows = conn.execute(
            "SELECT id, pressure, temperature, rf_power FROM sensor_readings "
            "WHERE part_id=? ORDER BY id", (pid,)).fetchall()
        if len(rows) < 25:
            continue
        for param in ('pressure', 'temperature', 'rf_power'):
            vals = [r[param] for r in rows if r[param] is not None]
            if len(vals) < 25:
                continue
            mean = sum(vals) / len(vals)
            std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
            if std <= 1e-9:
                continue
            for r in rows:
                x = r[param]
                if x is not None and abs(x - mean) / std > 3:
                    from analytics import FAULT_MAP
                    conn.execute(
                        "UPDATE sensor_readings SET is_anomaly=1, anomaly_param=?, "
                        "fault_mode=? WHERE id=? AND is_anomaly=0",
                        (param, FAULT_MAP[param], r['id']))
        update_health(conn, pid)
    check_stock_alerts(conn)
    conn.commit()
    conn.close()
    print('[seed] 데모 데이터 생성 완료 — 계정: admin/admin1234, engineer01~09 · viewer01~05 (semi1234)')
