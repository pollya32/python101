"""샘플 데이터 초기 등록 스크립트 — 처음 한 번만 실행"""
import sqlite3
import os
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), 'inventory.db')

# app.py의 init_db() 먼저 실행 필요
from app import init_db
init_db()

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# 추가 사용자 (총 12명)
users = [
    ('user01', '1234', '김철수', 'user'),
    ('user02', '1234', '이영희', 'user'),
    ('user03', '1234', '박민준', 'user'),
    ('user04', '1234', '최수진', 'user'),
    ('user05', '1234', '정동현', 'user'),
    ('user06', '1234', '한지은', 'user'),
    ('user07', '1234', '오세훈', 'user'),
    ('user08', '1234', '윤아름', 'user'),
    ('user09', '1234', '강태양', 'user'),
    ('user10', '1234', '신보람', 'user'),
    ('manager', '1234', '공정팀장', 'admin'),
]
for u, pw, name, role in users:
    try:
        c.execute("INSERT INTO users (username,password,name,role) VALUES (?,?,?,?)",
                  (u, generate_password_hash(pw), name, role))
    except sqlite3.IntegrityError:
        pass

# 장비
equipment = [
    ('EQ-001', 'CVD 장비 #1', '1공장 A-라인', '가동중', ''),
    ('EQ-002', 'CVD 장비 #2', '1공장 A-라인', '점검중', '정기 PM 중'),
    ('EQ-003', 'PVD 스퍼터링 #1', '1공장 B-라인', '가동중', ''),
    ('EQ-004', 'PVD 스퍼터링 #2', '2공장 A-라인', '가동중', ''),
    ('EQ-005', 'CMP 장비 #1', '2공장 B-라인', '가동중', ''),
    ('EQ-006', 'CMP 장비 #2', '2공장 B-라인', '고장', '펌프 교체 대기'),
    ('EQ-007', '포토 리소그래피 #1', '3공장 클린룸', '가동중', ''),
    ('EQ-008', '식각(Etch) 장비 #1', '3공장 클린룸', '가동중', ''),
    ('EQ-009', '이온주입 장비 #1', '3공장 D-라인', '대기', ''),
    ('EQ-010', '세정 장비 #1', '1공장 C-라인', '가동중', ''),
]
for code, name, loc, status, note in equipment:
    try:
        c.execute("INSERT INTO equipment (code,name,location,status,note) VALUES (?,?,?,?,?)",
                  (code, name, loc, status, note))
    except sqlite3.IntegrityError:
        pass

# 부품
parts = [
    ('PART-001', 'O-링 (100mm)', '소모품', 50, 10, 'EA', 3500, 'A-01-01', 'K반도체 부품'),
    ('PART-002', 'O-링 (200mm)', '소모품', 30, 10, 'EA', 5500, 'A-01-02', 'K반도체 부품'),
    ('PART-003', 'CVD 히터 블록', '핵심 부품', 5, 2, 'EA', 850000, 'B-02-01', '반도체코리아'),
    ('PART-004', 'RF 매칭 박스', '핵심 부품', 3, 1, 'EA', 2300000, 'B-02-02', '반도체코리아'),
    ('PART-005', '터보 펌프 (300L/s)', '펌프', 2, 1, 'EA', 5500000, 'C-01-01', '진공테크'),
    ('PART-006', '드라이 펌프', '펌프', 4, 2, 'EA', 1800000, 'C-01-02', '진공테크'),
    ('PART-007', '매스플로우 컨트롤러', '제어 부품', 8, 3, 'EA', 680000, 'B-03-01', '에어리퀴드'),
    ('PART-008', '압력 게이지 (바라트론)', '센서', 12, 5, 'EA', 125000, 'D-01-01', '센서텍'),
    ('PART-009', '온도 센서 (열전대)', '센서', 20, 8, 'EA', 45000, 'D-01-02', '센서텍'),
    ('PART-010', 'CMP 패드', '소모품', 15, 5, 'EA', 280000, 'A-02-01', '씨엠피코리아'),
    ('PART-011', 'CMP 슬러리 (Cu)', '소모품', 8, 3, 'L', 95000, 'A-02-02', '씨엠피코리아'),
    ('PART-012', '석영 링 (Quartz Ring)', '소모품', 6, 3, 'EA', 320000, 'A-03-01', '퀄텍'),
    ('PART-013', '포커스 링', '소모품', 4, 2, 'EA', 180000, 'A-03-02', '퀄텍'),
    ('PART-014', 'Gate Valve (DN100)', '밸브', 3, 1, 'EA', 450000, 'C-02-01', '밸브마스터'),
    ('PART-015', 'Butterfly Valve (4인치)', '밸브', 5, 2, 'EA', 230000, 'C-02-02', '밸브마스터'),
    ('PART-016', '냉각수 필터 카트리지', '소모품', 2, 3, 'EA', 85000, 'A-04-01', '필터코리아'),
    ('PART-017', 'ESC (정전척)', '핵심 부품', 1, 1, 'EA', 8500000, 'B-01-01', '반도체코리아'),
    ('PART-018', 'Ceramic 히터', '핵심 부품', 2, 1, 'EA', 3200000, 'B-01-02', '세라믹텍'),
    ('PART-019', '리프트 핀 세트', '소모품', 10, 5, 'SET', 95000, 'A-05-01', '퀄텍'),
    ('PART-020', '진공 게이지 (이온게이지)', '센서', 3, 2, 'EA', 380000, 'D-02-01', '센서텍'),
]
for code, name, cat, qty, min_qty, unit, price, loc, sup in parts:
    try:
        c.execute('''INSERT INTO parts (code,name,category,quantity,min_quantity,unit,unit_price,location,supplier)
                     VALUES (?,?,?,?,?,?,?,?,?)''',
                  (code, name, cat, qty, min_qty, unit, price, loc, sup))
    except sqlite3.IntegrityError:
        pass

conn.commit()
conn.close()
print("샘플 데이터 등록 완료!")
print("\n[로그인 계정]")
print("관리자: admin / admin1234")
print("팀장:   manager / 1234")
print("일반:   user01~user10 / 1234")
