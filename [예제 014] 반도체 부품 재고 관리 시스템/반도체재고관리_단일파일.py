"""
반도체 장비 부품 재고 관리 시스템 — 단일 파일 버전
=====================================================
설치: pip install flask flask-login werkzeug
실행: python 반도체재고관리_단일파일.py
접속: http://localhost:5000  (기본 계정: admin / admin1234)
"""

import sqlite3, os
from flask import Flask, render_template_string, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'semi-inv-2024-secret')
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = '로그인이 필요합니다.'
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'inventory.db')

# ── 데이터베이스 ─────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db(); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL, name TEXT NOT NULL, role TEXT DEFAULT 'user',
        created_at TEXT DEFAULT (datetime('now','localtime')))''')
    c.execute('''CREATE TABLE IF NOT EXISTS equipment (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL, location TEXT, status TEXT DEFAULT '가동중', note TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')))''')
    c.execute('''CREATE TABLE IF NOT EXISTS parts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL, category TEXT, quantity INTEGER DEFAULT 0,
        min_quantity INTEGER DEFAULT 5, unit TEXT DEFAULT 'EA', unit_price INTEGER DEFAULT 0,
        location TEXT, supplier TEXT, note TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime')))''')
    c.execute('''CREATE TABLE IF NOT EXISTS replacement_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, equipment_id INTEGER, part_id INTEGER,
        quantity INTEGER NOT NULL, reason TEXT, note TEXT, user_id INTEGER,
        replaced_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (equipment_id) REFERENCES equipment(id),
        FOREIGN KEY (part_id) REFERENCES parts(id),
        FOREIGN KEY (user_id) REFERENCES users(id))''')
    try:
        c.execute("INSERT INTO users (username,password,name,role) VALUES (?,?,?,?)",
                  ('admin', generate_password_hash('admin1234'), '관리자', 'admin'))
    except sqlite3.IntegrityError: pass
    conn.commit(); conn.close()

class User(UserMixin):
    def __init__(self, id, username, name, role):
        self.id=id; self.username=username; self.name=name; self.role=role

@login_manager.user_loader
def load_user(user_id):
    conn=get_db(); row=conn.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone(); conn.close()
    return User(row['id'],row['username'],row['name'],row['role']) if row else None

# ── HTML 템플릿 ──────────────────────────────────────────────────────────────

BASE = """<!DOCTYPE html><html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}반도체 부품 재고관리{% endblock %}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
<style>
:root{--sw:220px;--pri:#1a3a5c}
body{background:#f0f2f5;font-family:'Malgun Gothic',sans-serif}
#sb{width:var(--sw);min-height:100vh;background:var(--pri);position:fixed;top:0;left:0;z-index:100;display:flex;flex-direction:column}
#sb .br{padding:18px 14px;background:rgba(0,0,0,.2);color:#fff;font-weight:700;font-size:13px;line-height:1.4}
#sb .br i{font-size:26px;color:#7eb8f7;display:block;margin-bottom:4px}
#sb nav a{display:flex;align-items:center;gap:9px;padding:11px 18px;color:rgba(255,255,255,.75);text-decoration:none;font-size:13px;transition:.15s}
#sb nav a:hover,#sb nav a.ac{background:rgba(255,255,255,.12);color:#fff}
#sb nav a i{font-size:17px;width:20px;text-align:center}
#sb .ns{padding:10px 18px 3px;font-size:10px;color:rgba(255,255,255,.4);text-transform:uppercase}
#sb .ui{margin-top:auto;padding:12px 14px;background:rgba(0,0,0,.2);color:rgba(255,255,255,.8);font-size:12px}
#mn{margin-left:var(--sw)}
#tb{background:#fff;border-bottom:1px solid #dee2e6;padding:10px 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:90}
#tb h1{font-size:17px;font-weight:600;margin:0;color:#1a3a5c}
.ca{padding:20px}
.sc{border-radius:12px;border:none;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.ib{width:46px;height:46px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:20px}
.bs-가동중{background:#d1fae5;color:#065f46}.bs-점검중{background:#fef3c7;color:#92400e}
.bs-고장{background:#fee2e2;color:#991b1b}.bs-대기{background:#e0e7ff;color:#3730a3}
.ls{background:#fff7ed!important}
.table th{background:#f8f9fa;font-size:12px}.table td{font-size:13px;vertical-align:middle}
@media(max-width:768px){
  #sb{width:56px}
  #sb .br span,#sb nav a span,#sb .ns,#sb .ui span{display:none}
  #sb .br i{margin:0 auto}
  #sb nav a{justify-content:center;padding:12px}
  #mn{margin-left:56px}
}
</style>{% block head %}{% endblock %}</head><body>
{% if current_user.is_authenticated %}
<div id="sb">
  <div class="br"><i class="bi bi-cpu"></i><span>반도체 부품<br>재고관리 시스템</span></div>
  <nav>
    <div class="ns">메인</div>
    <a href="/" class="{{ 'ac' if request.endpoint=='dashboard' }}"><i class="bi bi-speedometer2"></i><span>대시보드</span></a>
    <div class="ns">재고</div>
    <a href="/parts" class="{{ 'ac' if 'parts' in (request.endpoint or '') }}"><i class="bi bi-box-seam"></i><span>부품 재고</span></a>
    <a href="/equipment" class="{{ 'ac' if 'equipment' in (request.endpoint or '') }}"><i class="bi bi-tools"></i><span>장비 관리</span></a>
    <a href="/history" class="{{ 'ac' if 'history' in (request.endpoint or '') }}"><i class="bi bi-clock-history"></i><span>교체 이력</span></a>
    <a href="/history/add"><i class="bi bi-plus-circle"></i><span>교체 등록</span></a>
    {% if current_user.role=='admin' %}
    <div class="ns">관리</div>
    <a href="/users" class="{{ 'ac' if 'users' in (request.endpoint or '') }}"><i class="bi bi-people"></i><span>사용자 관리</span></a>
    {% endif %}
  </nav>
  <div class="ui">
    <i class="bi bi-person-circle me-1"></i><span>{{ current_user.name }}
    {% if current_user.role=='admin' %}<span class="badge bg-warning text-dark ms-1" style="font-size:9px">관리자</span>{% endif %}</span>
    <a href="/logout" class="btn btn-sm btn-outline-light d-block mt-2"><span>로그아웃</span></a>
  </div>
</div>
{% endif %}
<div id="mn">
  {% if current_user.is_authenticated %}
  <div id="tb">
    <h1>{% block pt %}{% endblock %}</h1>
    <div class="d-flex align-items-center gap-2">
      <span id="lsb"></span><small class="text-muted" id="clk"></small>
    </div>
  </div>
  {% endif %}
  <div class="{{ 'ca' if current_user.is_authenticated }}">
    {% with messages=get_flashed_messages(with_categories=true) %}{% if messages %}
      {% for c,m in messages %}<div class="alert alert-{{c}} alert-dismissible fade show py-2">{{m}}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>{% endfor %}
    {% endif %}{% endwith %}
    {% block content %}{% endblock %}
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
function tick(){var e=document.getElementById('clk');if(e)e.textContent=new Date().toLocaleString('ko-KR');}
tick();setInterval(tick,1000);
function chkLow(){fetch('/api/low_stock_count').then(r=>r.json()).then(d=>{
  var e=document.getElementById('lsb');if(!e)return;
  e.innerHTML=d.count>0?`<a href="/parts" class="badge bg-danger text-decoration-none"><i class="bi bi-exclamation-triangle-fill me-1"></i>재고부족 ${d.count}건</a>`:'';
}).catch(()=>{});}
{% if current_user.is_authenticated %}chkLow();setInterval(chkLow,30000);{% endif %}
</script>{% block scripts %}{% endblock %}</body></html>"""

LOGIN_T = """<!DOCTYPE html><html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>로그인 - 반도체 부품 재고관리</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
<style>
body{background:linear-gradient(135deg,#1a3a5c,#0d6efd);min-height:100vh;display:flex;align-items:center;justify-content:center}
.lc{width:100%;max-width:400px;border-radius:16px;border:none;box-shadow:0 20px 60px rgba(0,0,0,.3)}
.lh{background:#1a3a5c;border-radius:16px 16px 0 0;padding:30px;text-align:center}
.lh i{font-size:44px;color:#7eb8f7}.lh h1{color:#fff;font-size:19px;margin-top:10px;font-weight:700}
.lh p{color:rgba(255,255,255,.6);font-size:12px;margin:0}.lb{padding:28px}
</style></head><body>
<div class="lc card"><div class="lh">
  <i class="bi bi-cpu-fill"></i>
  <h1>반도체 부품 재고관리</h1>
  <p>Semiconductor Parts Inventory System</p>
</div><div class="lb">
  {% with messages=get_flashed_messages(with_categories=true) %}{% for c,m in messages %}
  <div class="alert alert-{{c}} alert-dismissible fade show py-2">{{m}}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>
  {% endfor %}{% endwith %}
  <form method="POST">
    <div class="mb-3"><label class="form-label fw-semibold">아이디</label>
      <div class="input-group"><span class="input-group-text"><i class="bi bi-person"></i></span>
      <input type="text" name="username" class="form-control" placeholder="아이디" required autofocus></div></div>
    <div class="mb-4"><label class="form-label fw-semibold">비밀번호</label>
      <div class="input-group"><span class="input-group-text"><i class="bi bi-lock"></i></span>
      <input type="password" name="password" class="form-control" placeholder="비밀번호" required></div></div>
    <button type="submit" class="btn btn-primary w-100 py-2 fw-semibold">
      <i class="bi bi-box-arrow-in-right me-2"></i>로그인</button>
  </form>
  <p class="text-center text-muted mt-3 mb-0" style="font-size:11px">계정 문의는 시스템 관리자에게 연락하세요</p>
</div></div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
</body></html>"""

DASH_T = BASE + """{% block pt %}대시보드{% endblock %}{% block content %}
<div class="row g-3 mb-4">
  <div class="col-6 col-lg-3"><div class="card sc h-100"><div class="card-body d-flex align-items-center gap-3">
    <div class="ib" style="background:#dbeafe"><i class="bi bi-box-seam text-primary"></i></div>
    <div><div class="text-muted" style="font-size:11px">전체 부품 종류</div><div class="fw-bold fs-4">{{total_parts}}</div></div>
  </div></div></div>
  <div class="col-6 col-lg-3"><div class="card sc h-100"><div class="card-body d-flex align-items-center gap-3">
    <div class="ib" style="background:#dcfce7"><i class="bi bi-tools text-success"></i></div>
    <div><div class="text-muted" style="font-size:11px">등록 장비 수</div><div class="fw-bold fs-4">{{total_equipment}}</div></div>
  </div></div></div>
  <div class="col-6 col-lg-3"><div class="card sc h-100"><div class="card-body d-flex align-items-center gap-3">
    <div class="ib" style="background:#fee2e2"><i class="bi bi-exclamation-triangle text-danger"></i></div>
    <div><div class="text-muted" style="font-size:11px">재고 부족 품목</div><div class="fw-bold fs-4 text-danger">{{low_stock}}</div></div>
  </div></div></div>
  <div class="col-6 col-lg-3"><div class="card sc h-100"><div class="card-body d-flex align-items-center gap-3">
    <div class="ib" style="background:#fef3c7"><i class="bi bi-clock-history text-warning"></i></div>
    <div><div class="text-muted" style="font-size:11px">오늘 교체 건수</div><div class="fw-bold fs-4">{{today_repl}}</div></div>
  </div></div></div>
</div>
<div class="row g-3">
  <div class="col-lg-7"><div class="card sc"><div class="card-header bg-white fw-semibold d-flex justify-content-between align-items-center">
    <span><i class="bi bi-clock-history me-2 text-primary"></i>최근 교체 이력</span>
    <a href="/history" class="btn btn-sm btn-outline-primary">전체보기</a>
  </div><div class="card-body p-0"><div class="table-responsive">
    <table class="table table-hover mb-0"><thead><tr><th>일시</th><th>장비</th><th>부품</th><th>수량</th><th>작업자</th></tr></thead>
    <tbody>{% for r in recent %}<tr>
      <td><small>{{r.replaced_at[:16]}}</small></td><td>{{r.eq_name or '-'}}</td>
      <td>{{r.part_name or '-'}}</td><td>{{r.quantity}} {{r.unit}}</td><td>{{r.user_name or '-'}}</td>
    </tr>{% else %}<tr><td colspan="5" class="text-center text-muted py-3">교체 이력이 없습니다.</td></tr>{% endfor %}</tbody>
    </table></div></div></div></div>
  <div class="col-lg-5">
    <div class="card sc mb-3"><div class="card-header bg-white fw-semibold">
      <i class="bi bi-exclamation-triangle-fill text-danger me-2"></i>재고 부족 품목</div>
      <ul class="list-group list-group-flush">
        {% for p in low_parts %}<li class="list-group-item d-flex justify-content-between align-items-center py-2">
          <div><div style="font-size:13px;font-weight:600">{{p.name}}</div><small class="text-muted">{{p.code}}</small></div>
          <span class="badge {{'bg-danger' if p.quantity==0 else 'bg-warning text-dark'}}">{{p.quantity}} / {{p.min_quantity}} {{p.unit}}</span>
        </li>{% else %}<li class="list-group-item text-center text-muted py-3">재고 부족 품목 없음</li>{% endfor %}
      </ul>
    </div>
    <div class="card sc"><div class="card-header bg-white fw-semibold"><i class="bi bi-tools me-2 text-success"></i>장비 상태 현황</div>
      <div class="card-body">
        {% for s in eq_status %}<div class="d-flex justify-content-between align-items-center mb-2">
          <span class="badge bs-{{s.status}} px-3 py-2" style="font-size:12px">{{s.status}}</span><strong>{{s.cnt}}대</strong>
        </div>{% else %}<p class="text-muted text-center mb-0">등록된 장비 없음</p>{% endfor %}
      </div>
    </div>
  </div>
</div>
<div class="row g-3 mt-1"><div class="col-12"><div class="card sc"><div class="card-body">
  <div class="d-flex gap-2 flex-wrap">
    <a href="/history/add" class="btn btn-primary"><i class="bi bi-plus-circle me-1"></i>교체 등록</a>
    <a href="/parts/add" class="btn btn-outline-primary"><i class="bi bi-box-seam me-1"></i>부품 등록</a>
    <a href="/equipment/add" class="btn btn-outline-secondary"><i class="bi bi-tools me-1"></i>장비 등록</a>
    <a href="/parts" class="btn btn-outline-secondary"><i class="bi bi-search me-1"></i>부품 검색</a>
  </div>
</div></div></div></div>
{% endblock %}"""

PARTS_T = BASE + """{% block pt %}부품 재고 관리{% endblock %}{% block content %}
<div class="card mb-3"><div class="card-body py-2">
  <form class="row g-2 align-items-center" method="GET">
    <div class="col-auto flex-grow-1"><div class="input-group">
      <span class="input-group-text"><i class="bi bi-search"></i></span>
      <input type="text" name="q" class="form-control" placeholder="부품명, 코드, 공급업체 검색..." value="{{q}}">
    </div></div>
    <div class="col-auto"><select name="category" class="form-select">
      <option value="">전체 카테고리</option>
      {% for cat in categories %}<option value="{{cat.category}}" {{'selected' if sel_cat==cat.category}}>{{cat.category}}</option>{% endfor %}
    </select></div>
    <div class="col-auto"><button type="submit" class="btn btn-primary">검색</button>
      <a href="/parts" class="btn btn-outline-secondary ms-1">초기화</a></div>
    <div class="col-auto ms-auto"><a href="/parts/add" class="btn btn-success"><i class="bi bi-plus-circle me-1"></i>부품 등록</a></div>
  </form>
</div></div>
<div class="card"><div class="card-header bg-white fw-semibold">
  부품 목록 <span class="badge bg-secondary">{{parts|length}}개</span>
</div><div class="card-body p-0"><div class="table-responsive">
  <table class="table table-hover mb-0"><thead>
    <tr><th>코드</th><th>부품명</th><th>카테고리</th><th class="text-center">현재재고</th><th class="text-center">최소재고</th><th>위치</th><th>단가</th><th>공급업체</th><th class="text-center">작업</th></tr>
  </thead><tbody>
    {% for p in parts %}<tr class="{{'ls' if p.quantity<=p.min_quantity}}">
      <td><code>{{p.code}}</code></td>
      <td><strong>{{p.name}}</strong>
        {% if p.quantity==0 %}<span class="badge bg-danger ms-1">품절</span>
        {% elif p.quantity<=p.min_quantity %}<span class="badge bg-warning text-dark ms-1">부족</span>{% endif %}
      </td>
      <td><span class="badge bg-light text-dark">{{p.category or '-'}}</span></td>
      <td class="text-center fw-bold {{'text-danger' if p.quantity<=p.min_quantity}}">{{p.quantity}} {{p.unit}}</td>
      <td class="text-center text-muted">{{p.min_quantity}} {{p.unit}}</td>
      <td>{{p.location or '-'}}</td>
      <td>{{'{:,}'.format(p.unit_price)}}원</td>
      <td>{{p.supplier or '-'}}</td>
      <td class="text-center"><div class="d-flex gap-1 justify-content-center">
        <button class="btn btn-sm btn-outline-success" onclick="openAdj({{p.id}},'{{p.name}}','in',{{p.quantity}},'{{p.unit}}')" title="입고"><i class="bi bi-arrow-down-circle"></i></button>
        <button class="btn btn-sm btn-outline-warning" onclick="openAdj({{p.id}},'{{p.name}}','out',{{p.quantity}},'{{p.unit}}')" title="출고"><i class="bi bi-arrow-up-circle"></i></button>
        <a href="/parts/{{p.id}}/edit" class="btn btn-sm btn-outline-primary" title="수정"><i class="bi bi-pencil"></i></a>
        {% if current_user.role=='admin' %}
        <form method="POST" action="/parts/{{p.id}}/delete" onsubmit="return confirm('삭제하시겠습니까?')">
          <button type="submit" class="btn btn-sm btn-outline-danger"><i class="bi bi-trash"></i></button>
        </form>{% endif %}
      </div></td>
    </tr>{% else %}<tr><td colspan="9" class="text-center text-muted py-4">부품이 없습니다.</td></tr>{% endfor %}
  </tbody></table>
</div></div></div>
<div class="modal fade" id="adjModal" tabindex="-1"><div class="modal-dialog modal-sm"><div class="modal-content">
  <div class="modal-header"><h6 class="modal-title fw-bold" id="adjTitle"></h6>
    <button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
  <form method="POST" id="adjForm"><div class="modal-body">
    <input type="hidden" name="action" id="adjAct">
    <label class="form-label fw-semibold">수량 <span id="adjUnit"></span></label>
    <input type="number" name="amount" id="adjAmt" class="form-control" min="1" value="1" required>
    <p class="text-muted mb-0 mt-2" style="font-size:12px">현재 재고: <strong id="adjCur"></strong></p>
  </div><div class="modal-footer">
    <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">취소</button>
    <button type="submit" class="btn btn-primary btn-sm" id="adjBtn">확인</button>
  </div></form>
</div></div></div>
{% endblock %}{% block scripts %}<script>
function openAdj(id,name,action,qty,unit){
  document.getElementById('adjForm').action=`/parts/${id}/adjust`;
  document.getElementById('adjAct').value=action;
  document.getElementById('adjTitle').textContent=(action==='in'?'입고':'출고')+': '+name;
  document.getElementById('adjUnit').textContent=unit;
  document.getElementById('adjCur').textContent=qty+' '+unit;
  document.getElementById('adjBtn').textContent=action==='in'?'입고 처리':'출고 처리';
  document.getElementById('adjBtn').className=`btn btn-sm ${action==='in'?'btn-success':'btn-warning'}`;
  document.getElementById('adjAmt').max=action==='out'?qty:99999;
  new bootstrap.Modal(document.getElementById('adjModal')).show();
}
</script>{% endblock %}"""

PART_FORM_T = BASE + """{% block pt %}{{title}}{% endblock %}{% block content %}
<div class="row justify-content-center"><div class="col-lg-7"><div class="card">
  <div class="card-header bg-white fw-semibold"><i class="bi bi-box-seam me-2"></i>{{title}}</div>
  <div class="card-body"><form method="POST"><div class="row g-3">
    <div class="col-md-4"><label class="form-label fw-semibold">부품 코드 <span class="text-danger">*</span></label>
      <input type="text" name="code" class="form-control" value="{{part.code if part else ''}}" {{'readonly' if part}} required placeholder="예: PART-001"></div>
    <div class="col-md-8"><label class="form-label fw-semibold">부품명 <span class="text-danger">*</span></label>
      <input type="text" name="name" class="form-control" value="{{part.name if part else ''}}" required></div>
    <div class="col-md-6"><label class="form-label fw-semibold">카테고리</label>
      <input type="text" name="category" class="form-control" value="{{part.category if part else ''}}" placeholder="예: 펌프, 밸브, 센서"></div>
    <div class="col-md-3"><label class="form-label fw-semibold">단위</label>
      <select name="unit" class="form-select">{% for u in ['EA','SET','M','L','KG','BOX'] %}
        <option value="{{u}}" {{'selected' if part and part.unit==u}}>{{u}}</option>{% endfor %}
      </select></div>
    <div class="col-md-3"><label class="form-label fw-semibold">단가(원)</label>
      <input type="number" name="unit_price" class="form-control" value="{{part.unit_price if part else 0}}" min="0"></div>
    <div class="col-md-3"><label class="form-label fw-semibold">현재 재고</label>
      <input type="number" name="quantity" class="form-control" value="{{part.quantity if part else 0}}" min="0"></div>
    <div class="col-md-3"><label class="form-label fw-semibold">최소 재고</label>
      <input type="number" name="min_quantity" class="form-control" value="{{part.min_quantity if part else 5}}" min="0"></div>
    <div class="col-md-6"><label class="form-label fw-semibold">보관 위치</label>
      <input type="text" name="location" class="form-control" value="{{part.location if part else ''}}" placeholder="예: A-01-03"></div>
    <div class="col-md-6"><label class="form-label fw-semibold">공급업체</label>
      <input type="text" name="supplier" class="form-control" value="{{part.supplier if part else ''}}"></div>
    <div class="col-12"><label class="form-label fw-semibold">비고</label>
      <textarea name="note" class="form-control" rows="2">{{part.note if part else ''}}</textarea></div>
  </div><div class="d-flex gap-2 mt-4">
    <button type="submit" class="btn btn-primary"><i class="bi bi-check2 me-1"></i>저장</button>
    <a href="/parts" class="btn btn-outline-secondary">취소</a>
  </div></form></div>
</div></div></div>{% endblock %}"""

EQ_T = BASE + """{% block pt %}장비 관리{% endblock %}{% block content %}
<div class="card mb-3"><div class="card-body py-2">
  <form class="row g-2 align-items-center" method="GET">
    <div class="col-auto flex-grow-1"><div class="input-group">
      <span class="input-group-text"><i class="bi bi-search"></i></span>
      <input type="text" name="q" class="form-control" placeholder="장비명, 코드, 위치 검색..." value="{{q}}">
    </div></div>
    <div class="col-auto"><button type="submit" class="btn btn-primary">검색</button>
      <a href="/equipment" class="btn btn-outline-secondary ms-1">초기화</a></div>
    <div class="col-auto ms-auto"><a href="/equipment/add" class="btn btn-success"><i class="bi bi-plus-circle me-1"></i>장비 등록</a></div>
  </form>
</div></div>
<div class="row g-3">
  {% for eq in equipment %}<div class="col-md-6 col-lg-4"><div class="card h-100">
    <div class="card-body">
      <div class="d-flex justify-content-between align-items-start mb-2">
        <div><code class="text-muted" style="font-size:11px">{{eq.code}}</code><h6 class="mb-0 mt-1 fw-bold">{{eq.name}}</h6></div>
        <span class="badge bs-{{eq.status}} px-2 py-1">{{eq.status}}</span>
      </div>
      {% if eq.location %}<p class="text-muted mb-1" style="font-size:12px"><i class="bi bi-geo-alt me-1"></i>{{eq.location}}</p>{% endif %}
      {% if eq.note %}<p class="text-muted mb-2" style="font-size:11px">{{eq.note}}</p>{% endif %}
      <div class="d-flex gap-1 mt-2">
        <a href="/equipment/{{eq.id}}/edit" class="btn btn-sm btn-outline-primary"><i class="bi bi-pencil me-1"></i>수정</a>
        {% if current_user.role=='admin' %}
        <form method="POST" action="/equipment/{{eq.id}}/delete" onsubmit="return confirm('삭제하시겠습니까?')">
          <button type="submit" class="btn btn-sm btn-outline-danger"><i class="bi bi-trash me-1"></i>삭제</button>
        </form>{% endif %}
      </div>
    </div></div></div>
  {% else %}<div class="col-12"><div class="alert alert-secondary text-center py-4">
    <i class="bi bi-tools fs-2 d-block mb-2"></i>등록된 장비가 없습니다.
    <a href="/equipment/add" class="d-block mt-2">장비 등록하기</a>
  </div></div>{% endfor %}
</div>{% endblock %}"""

EQ_FORM_T = BASE + """{% block pt %}{{title}}{% endblock %}{% block content %}
<div class="row justify-content-center"><div class="col-lg-6"><div class="card">
  <div class="card-header bg-white fw-semibold"><i class="bi bi-tools me-2"></i>{{title}}</div>
  <div class="card-body"><form method="POST"><div class="row g-3">
    <div class="col-md-4"><label class="form-label fw-semibold">장비 코드 <span class="text-danger">*</span></label>
      <input type="text" name="code" class="form-control" value="{{eq.code if eq else ''}}" {{'readonly' if eq}} required placeholder="예: EQ-001"></div>
    <div class="col-md-8"><label class="form-label fw-semibold">장비명 <span class="text-danger">*</span></label>
      <input type="text" name="name" class="form-control" value="{{eq.name if eq else ''}}" required></div>
    <div class="col-md-6"><label class="form-label fw-semibold">설치 위치</label>
      <input type="text" name="location" class="form-control" value="{{eq.location if eq else ''}}" placeholder="예: 1공장 A라인"></div>
    <div class="col-md-6"><label class="form-label fw-semibold">상태</label>
      <select name="status" class="form-select">{% for s in ['가동중','점검중','고장','대기'] %}
        <option value="{{s}}" {{'selected' if eq and eq.status==s}}>{{s}}</option>{% endfor %}
      </select></div>
    <div class="col-12"><label class="form-label fw-semibold">비고</label>
      <textarea name="note" class="form-control" rows="2">{{eq.note if eq else ''}}</textarea></div>
  </div><div class="d-flex gap-2 mt-4">
    <button type="submit" class="btn btn-primary"><i class="bi bi-check2 me-1"></i>저장</button>
    <a href="/equipment" class="btn btn-outline-secondary">취소</a>
  </div></form></div>
</div></div></div>{% endblock %}"""

HIST_T = BASE + """{% block pt %}교체 이력{% endblock %}{% block content %}
<div class="card mb-3"><div class="card-body py-2">
  <form class="row g-2 align-items-center" method="GET">
    <div class="col-auto flex-grow-1"><div class="input-group">
      <span class="input-group-text"><i class="bi bi-search"></i></span>
      <input type="text" name="q" class="form-control" placeholder="장비명, 부품명, 작업자 검색..." value="{{q}}">
    </div></div>
    <div class="col-auto"><button type="submit" class="btn btn-primary">검색</button>
      <a href="/history" class="btn btn-outline-secondary ms-1">초기화</a></div>
    <div class="col-auto ms-auto"><a href="/history/add" class="btn btn-success"><i class="bi bi-plus-circle me-1"></i>교체 등록</a></div>
  </form>
</div></div>
<div class="card"><div class="card-header bg-white fw-semibold">
  교체 이력 <span class="badge bg-secondary">총 {{total}}건</span>
</div><div class="card-body p-0"><div class="table-responsive">
  <table class="table table-hover mb-0"><thead>
    <tr><th>교체 일시</th><th>장비명</th><th>부품명</th><th class="text-center">수량</th><th>교체 사유</th><th>비고</th><th>작업자</th>
    {% if current_user.role=='admin' %}<th>삭제</th>{% endif %}</tr>
  </thead><tbody>
    {% for r in rows %}<tr>
      <td><small>{{r.replaced_at[:16]}}</small></td><td>{{r.eq_name or '-'}}</td><td>{{r.part_name or '-'}}</td>
      <td class="text-center">{{r.quantity}} {{r.unit}}</td><td>{{r.reason or '-'}}</td>
      <td><small class="text-muted">{{r.note or ''}}</small></td><td>{{r.user_name or '-'}}</td>
      {% if current_user.role=='admin' %}<td>
        <form method="POST" action="/history/{{r.id}}/delete" onsubmit="return confirm('삭제 시 재고가 복구됩니다. 계속하시겠습니까?')">
          <button type="submit" class="btn btn-sm btn-outline-danger"><i class="bi bi-trash"></i></button>
        </form></td>{% endif %}
    </tr>{% else %}<tr><td colspan="8" class="text-center text-muted py-4">이력이 없습니다.</td></tr>{% endfor %}
  </tbody></table>
</div></div>
{% if total_pages>1 %}<div class="card-footer bg-white"><nav><ul class="pagination pagination-sm mb-0 justify-content-center">
  {% for p in range(1,total_pages+1) %}<li class="page-item {{'active' if p==page}}">
    <a class="page-link" href="?page={{p}}&q={{q}}">{{p}}</a></li>{% endfor %}
</ul></nav></div>{% endif %}
</div>{% endblock %}"""

HIST_FORM_T = BASE + """{% block pt %}부품 교체 등록{% endblock %}{% block content %}
<div class="row justify-content-center"><div class="col-lg-6"><div class="card">
  <div class="card-header bg-white fw-semibold"><i class="bi bi-clock-history me-2"></i>교체 이력 등록</div>
  <div class="card-body"><form method="POST"><div class="row g-3">
    <div class="col-12"><label class="form-label fw-semibold">장비 <span class="text-danger">*</span></label>
      <select name="equipment_id" class="form-select" required>
        <option value="">-- 장비 선택 --</option>
        {% for eq in equipment %}<option value="{{eq.id}}">[{{eq.code}}] {{eq.name}} ({{eq.location or '위치미정'}})</option>{% endfor %}
      </select></div>
    <div class="col-12"><label class="form-label fw-semibold">부품 <span class="text-danger">*</span></label>
      <select name="part_id" class="form-select" id="ps" required onchange="updStock()">
        <option value="">-- 부품 선택 --</option>
        {% for p in parts %}<option value="{{p.id}}" data-qty="{{p.quantity}}" data-unit="{{p.unit}}" data-min="{{p.min_quantity}}">
          [{{p.code}}] {{p.name}} — 재고: {{p.quantity}} {{p.unit}}</option>{% endfor %}
      </select>
      <div id="si" class="mt-1"></div></div>
    <div class="col-md-4"><label class="form-label fw-semibold">교체 수량 <span class="text-danger">*</span></label>
      <input type="number" name="quantity" id="qi" class="form-control" min="1" value="1" required></div>
    <div class="col-md-8"><label class="form-label fw-semibold">교체 사유</label>
      <input type="text" name="reason" class="form-control" placeholder="예: 정기 교체, 마모, 파손 등"></div>
    <div class="col-12"><label class="form-label fw-semibold">비고</label>
      <textarea name="note" class="form-control" rows="2"></textarea></div>
  </div><div class="d-flex gap-2 mt-4">
    <button type="submit" class="btn btn-primary"><i class="bi bi-check2 me-1"></i>교체 등록</button>
    <a href="/history" class="btn btn-outline-secondary">취소</a>
  </div></form></div>
</div></div></div>
{% endblock %}{% block scripts %}<script>
function updStock(){
  var s=document.getElementById('ps'),o=s.options[s.selectedIndex],i=document.getElementById('si'),q=document.getElementById('qi');
  if(!o.value){i.innerHTML='';return;}
  var qty=parseInt(o.dataset.qty),unit=o.dataset.unit,min=parseInt(o.dataset.min);
  q.max=qty;
  i.innerHTML=qty===0?'<span class="text-danger"><i class="bi bi-exclamation-circle me-1"></i>재고 없음 (품절)</span>':
    qty<=min?`<span class="text-warning"><i class="bi bi-exclamation-triangle me-1"></i>재고 부족: ${qty} ${unit} (최소: ${min})</span>`:
    `<span class="text-success"><i class="bi bi-check-circle me-1"></i>현재 재고: ${qty} ${unit}</span>`;
}
</script>{% endblock %}"""

USERS_T = BASE + """{% block pt %}사용자 관리{% endblock %}{% block content %}
<div class="d-flex justify-content-end mb-3">
  <a href="/users/add" class="btn btn-success"><i class="bi bi-person-plus me-1"></i>사용자 등록</a>
</div>
<div class="card"><div class="card-header bg-white fw-semibold">
  사용자 목록 <span class="badge bg-secondary">{{users|length}}명</span> <small class="text-muted ms-2">최대 12명</small>
</div><div class="card-body p-0"><table class="table table-hover mb-0"><thead>
  <tr><th>아이디</th><th>이름</th><th>권한</th><th>등록일</th><th>작업</th></tr>
</thead><tbody>
  {% for u in users %}<tr>
    <td><code>{{u.username}}</code></td><td>{{u.name}}</td>
    <td>{% if u.role=='admin' %}<span class="badge bg-warning text-dark">관리자</span>
    {% else %}<span class="badge bg-light text-dark">일반</span>{% endif %}</td>
    <td><small>{{u.created_at[:10]}}</small></td>
    <td><div class="d-flex gap-1">
      <a href="/users/{{u.id}}/edit" class="btn btn-sm btn-outline-primary"><i class="bi bi-pencil"></i></a>
      {% if u.id!=current_user.id %}
      <form method="POST" action="/users/{{u.id}}/delete" onsubmit="return confirm('{{u.name}} 사용자를 삭제하시겠습니까?')">
        <button type="submit" class="btn btn-sm btn-outline-danger"><i class="bi bi-trash"></i></button>
      </form>{% endif %}
    </div></td>
  </tr>{% endfor %}
</tbody></table></div></div>{% endblock %}"""

USER_FORM_T = BASE + """{% block pt %}{{title}}{% endblock %}{% block content %}
<div class="row justify-content-center"><div class="col-lg-5"><div class="card">
  <div class="card-header bg-white fw-semibold"><i class="bi bi-person me-2"></i>{{title}}</div>
  <div class="card-body"><form method="POST"><div class="row g-3">
    <div class="col-12"><label class="form-label fw-semibold">아이디 <span class="text-danger">*</span></label>
      <input type="text" name="username" class="form-control" value="{{user.username if user else ''}}"
             {{'readonly' if user}} required placeholder="로그인 아이디"></div>
    <div class="col-12"><label class="form-label fw-semibold">비밀번호
      {% if user %}<small class="text-muted fw-normal">(변경 시에만 입력)</small>{% else %}<span class="text-danger">*</span>{% endif %}</label>
      <input type="password" name="password" class="form-control" {{'required' if not user}} placeholder="비밀번호"></div>
    <div class="col-12"><label class="form-label fw-semibold">이름 <span class="text-danger">*</span></label>
      <input type="text" name="name" class="form-control" value="{{user.name if user else ''}}" required placeholder="실명"></div>
    <div class="col-12"><label class="form-label fw-semibold">권한</label>
      <select name="role" class="form-select">
        <option value="user" {{'selected' if user and user.role=='user'}}>일반 사용자</option>
        <option value="admin" {{'selected' if user and user.role=='admin'}}>관리자</option>
      </select></div>
  </div><div class="d-flex gap-2 mt-4">
    <button type="submit" class="btn btn-primary"><i class="bi bi-check2 me-1"></i>저장</button>
    <a href="/users" class="btn btn-outline-secondary">취소</a>
  </div></form></div>
</div></div></div>{% endblock %}"""

# ── 라우트 ───────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET','POST'])
def login():
    if current_user.is_authenticated: return redirect('/')
    if request.method == 'POST':
        u, pw = request.form['username'], request.form['password']
        conn = get_db(); row = conn.execute("SELECT * FROM users WHERE username=?",(u,)).fetchone(); conn.close()
        if row and check_password_hash(row['password'], pw):
            login_user(User(row['id'],row['username'],row['name'],row['role'])); return redirect('/')
        flash('아이디 또는 비밀번호가 올바르지 않습니다.','danger')
    return render_template_string(LOGIN_T)

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect('/login')

@app.route('/')
@login_required
def dashboard():
    conn = get_db()
    d = dict(
        total_parts=conn.execute("SELECT COUNT(*) as c FROM parts").fetchone()['c'],
        total_equipment=conn.execute("SELECT COUNT(*) as c FROM equipment").fetchone()['c'],
        low_stock=conn.execute("SELECT COUNT(*) as c FROM parts WHERE quantity<=min_quantity").fetchone()['c'],
        today_repl=conn.execute("SELECT COUNT(*) as c FROM replacement_history WHERE date(replaced_at)=date('now','localtime')").fetchone()['c'],
        recent=conn.execute('''SELECT rh.replaced_at,u.name as user_name,e.name as eq_name,p.name as part_name,rh.quantity,p.unit
            FROM replacement_history rh LEFT JOIN users u ON rh.user_id=u.id LEFT JOIN equipment e ON rh.equipment_id=e.id
            LEFT JOIN parts p ON rh.part_id=p.id ORDER BY rh.replaced_at DESC LIMIT 10''').fetchall(),
        low_parts=conn.execute("SELECT * FROM parts WHERE quantity<=min_quantity ORDER BY quantity ASC LIMIT 10").fetchall(),
        eq_status=conn.execute("SELECT status,COUNT(*) as cnt FROM equipment GROUP BY status").fetchall(),
    )
    conn.close(); return render_template_string(DASH_T, **d)

@app.route('/parts')
@login_required
def parts_list():
    q=request.args.get('q',''); cat=request.args.get('category','')
    conn=get_db(); sql="SELECT * FROM parts WHERE 1=1"; p=[]
    if q: sql+=" AND (name LIKE ? OR code LIKE ? OR supplier LIKE ?)"; p+=[f'%{q}%']*3
    if cat: sql+=" AND category=?"; p.append(cat)
    parts=conn.execute(sql+" ORDER BY category,name",p).fetchall()
    cats=conn.execute("SELECT DISTINCT category FROM parts WHERE category!='' ORDER BY category").fetchall()
    conn.close(); return render_template_string(PARTS_T, parts=parts, categories=cats, q=q, sel_cat=cat)

@app.route('/parts/add', methods=['GET','POST'])
@login_required
def parts_add():
    if request.method=='POST':
        f=request.form; conn=get_db()
        try:
            conn.execute("INSERT INTO parts (code,name,category,quantity,min_quantity,unit,unit_price,location,supplier,note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f['code'].strip(),f['name'].strip(),f.get('category','').strip(),int(f.get('quantity',0)),int(f.get('min_quantity',5)),
                 f.get('unit','EA'),int(f.get('unit_price',0) or 0),f.get('location','').strip(),f.get('supplier','').strip(),f.get('note','').strip()))
            conn.commit(); flash('부품이 등록되었습니다.','success'); conn.close(); return redirect('/parts')
        except sqlite3.IntegrityError: flash('이미 존재하는 부품 코드입니다.','danger')
        finally: conn.close()
    return render_template_string(PART_FORM_T, part=None, title='부품 등록')

@app.route('/parts/<int:pid>/edit', methods=['GET','POST'])
@login_required
def parts_edit(pid):
    conn=get_db(); part=conn.execute("SELECT * FROM parts WHERE id=?",(pid,)).fetchone()
    if not part: conn.close(); flash('존재하지 않는 부품입니다.','danger'); return redirect('/parts')
    if request.method=='POST':
        f=request.form
        conn.execute("UPDATE parts SET name=?,category=?,quantity=?,min_quantity=?,unit=?,unit_price=?,location=?,supplier=?,note=?,updated_at=datetime('now','localtime') WHERE id=?",
            (f['name'].strip(),f.get('category','').strip(),int(f.get('quantity',0)),int(f.get('min_quantity',5)),
             f.get('unit','EA'),int(f.get('unit_price',0) or 0),f.get('location','').strip(),f.get('supplier','').strip(),f.get('note','').strip(),pid))
        conn.commit(); conn.close(); flash('부품 정보가 수정되었습니다.','success'); return redirect('/parts')
    conn.close(); return render_template_string(PART_FORM_T, part=part, title='부품 수정')

@app.route('/parts/<int:pid>/delete', methods=['POST'])
@login_required
def parts_delete(pid):
    if current_user.role!='admin': flash('관리자만 삭제할 수 있습니다.','danger'); return redirect('/parts')
    conn=get_db(); conn.execute("DELETE FROM parts WHERE id=?",(pid,)); conn.commit(); conn.close()
    flash('부품이 삭제되었습니다.','success'); return redirect('/parts')

@app.route('/parts/<int:pid>/adjust', methods=['POST'])
@login_required
def parts_adjust(pid):
    action=request.form.get('action'); amount=int(request.form.get('amount',0))
    conn=get_db(); part=conn.execute("SELECT * FROM parts WHERE id=?",(pid,)).fetchone()
    new_qty=part['quantity']+amount if action=='in' else part['quantity']-amount
    if new_qty<0: conn.close(); flash('재고가 부족합니다.','danger'); return redirect('/parts')
    conn.execute("UPDATE parts SET quantity=?,updated_at=datetime('now','localtime') WHERE id=?",(new_qty,pid))
    conn.commit(); conn.close()
    flash(f"{'입고' if action=='in' else '출고'} 처리되었습니다. (현재 재고: {new_qty})",'success'); return redirect('/parts')

@app.route('/equipment')
@login_required
def equipment_list():
    q=request.args.get('q',''); conn=get_db()
    sql="SELECT * FROM equipment WHERE 1=1"; p=[]
    if q: sql+=" AND (name LIKE ? OR code LIKE ? OR location LIKE ?)"; p+=[f'%{q}%']*3
    eqs=conn.execute(sql+" ORDER BY code",p).fetchall(); conn.close()
    return render_template_string(EQ_T, equipment=eqs, q=q)

@app.route('/equipment/add', methods=['GET','POST'])
@login_required
def equipment_add():
    if request.method=='POST':
        f=request.form; conn=get_db()
        try:
            conn.execute("INSERT INTO equipment (code,name,location,status,note) VALUES (?,?,?,?,?)",
                (f['code'].strip(),f['name'].strip(),f.get('location','').strip(),f.get('status','가동중'),f.get('note','').strip()))
            conn.commit(); flash('장비가 등록되었습니다.','success'); conn.close(); return redirect('/equipment')
        except sqlite3.IntegrityError: flash('이미 존재하는 장비 코드입니다.','danger')
        finally: conn.close()
    return render_template_string(EQ_FORM_T, eq=None, title='장비 등록')

@app.route('/equipment/<int:eid>/edit', methods=['GET','POST'])
@login_required
def equipment_edit(eid):
    conn=get_db(); eq=conn.execute("SELECT * FROM equipment WHERE id=?",(eid,)).fetchone()
    if not eq: conn.close(); flash('존재하지 않는 장비입니다.','danger'); return redirect('/equipment')
    if request.method=='POST':
        f=request.form
        conn.execute("UPDATE equipment SET name=?,location=?,status=?,note=? WHERE id=?",
            (f['name'].strip(),f.get('location','').strip(),f.get('status','가동중'),f.get('note','').strip(),eid))
        conn.commit(); conn.close(); flash('장비 정보가 수정되었습니다.','success'); return redirect('/equipment')
    conn.close(); return render_template_string(EQ_FORM_T, eq=eq, title='장비 수정')

@app.route('/equipment/<int:eid>/delete', methods=['POST'])
@login_required
def equipment_delete(eid):
    if current_user.role!='admin': flash('관리자만 삭제할 수 있습니다.','danger'); return redirect('/equipment')
    conn=get_db(); conn.execute("DELETE FROM equipment WHERE id=?",(eid,)); conn.commit(); conn.close()
    flash('장비가 삭제되었습니다.','success'); return redirect('/equipment')

@app.route('/history')
@login_required
def history_list():
    page=int(request.args.get('page',1)); per=20; offset=(page-1)*per; q=request.args.get('q','')
    conn=get_db()
    base='''FROM replacement_history rh LEFT JOIN users u ON rh.user_id=u.id
            LEFT JOIN equipment e ON rh.equipment_id=e.id LEFT JOIN parts p ON rh.part_id=p.id WHERE 1=1'''
    params=[]
    if q: base+=" AND (e.name LIKE ? OR p.name LIKE ? OR u.name LIKE ?)"; params+=[f'%{q}%']*3
    total=conn.execute(f"SELECT COUNT(*) as c {base}",params).fetchone()['c']
    rows=conn.execute(f"SELECT rh.*,u.name as user_name,e.name as eq_name,p.name as part_name,p.unit {base} ORDER BY rh.replaced_at DESC LIMIT ? OFFSET ?",
                      params+[per,offset]).fetchall()
    conn.close(); return render_template_string(HIST_T, rows=rows, page=page, total_pages=(total+per-1)//per, q=q, total=total)

@app.route('/history/add', methods=['GET','POST'])
@login_required
def history_add():
    conn=get_db()
    if request.method=='POST':
        eid,pid,qty=int(request.form['equipment_id']),int(request.form['part_id']),int(request.form['quantity'])
        part=conn.execute("SELECT * FROM parts WHERE id=?",(pid,)).fetchone()
        if part['quantity']<qty:
            flash(f'재고 부족! 현재 재고: {part["quantity"]} {part["unit"]}','danger')
        else:
            conn.execute("INSERT INTO replacement_history (equipment_id,part_id,quantity,reason,note,user_id) VALUES (?,?,?,?,?,?)",
                (eid,pid,qty,request.form.get('reason','').strip(),request.form.get('note','').strip(),current_user.id))
            conn.execute("UPDATE parts SET quantity=quantity-?,updated_at=datetime('now','localtime') WHERE id=?",(qty,pid))
            conn.commit(); flash('교체 이력이 등록되었습니다.','success'); conn.close(); return redirect('/history')
    eqs=conn.execute("SELECT * FROM equipment ORDER BY code").fetchall()
    parts=conn.execute("SELECT * FROM parts ORDER BY category,name").fetchall()
    conn.close(); return render_template_string(HIST_FORM_T, equipment=eqs, parts=parts)

@app.route('/history/<int:hid>/delete', methods=['POST'])
@login_required
def history_delete(hid):
    if current_user.role!='admin': flash('관리자만 삭제할 수 있습니다.','danger'); return redirect('/history')
    conn=get_db(); row=conn.execute("SELECT * FROM replacement_history WHERE id=?",(hid,)).fetchone()
    if row:
        conn.execute("UPDATE parts SET quantity=quantity+?,updated_at=datetime('now','localtime') WHERE id=?",(row['quantity'],row['part_id']))
        conn.execute("DELETE FROM replacement_history WHERE id=?",(hid,)); conn.commit()
        flash('이력이 삭제되었고 재고가 복구되었습니다.','success')
    conn.close(); return redirect('/history')

@app.route('/users')
@login_required
def users_list():
    if current_user.role!='admin': flash('관리자만 접근할 수 있습니다.','danger'); return redirect('/')
    conn=get_db(); users=conn.execute("SELECT * FROM users ORDER BY role DESC,name").fetchall(); conn.close()
    return render_template_string(USERS_T, users=users)

@app.route('/users/add', methods=['GET','POST'])
@login_required
def users_add():
    if current_user.role!='admin': flash('관리자만 접근할 수 있습니다.','danger'); return redirect('/')
    if request.method=='POST':
        f=request.form; conn=get_db()
        try:
            conn.execute("INSERT INTO users (username,password,name,role) VALUES (?,?,?,?)",
                (f['username'].strip(),generate_password_hash(f['password']),f['name'].strip(),f.get('role','user')))
            conn.commit(); flash('사용자가 등록되었습니다.','success'); conn.close(); return redirect('/users')
        except sqlite3.IntegrityError: flash('이미 존재하는 아이디입니다.','danger')
        finally: conn.close()
    return render_template_string(USER_FORM_T, user=None, title='사용자 등록')

@app.route('/users/<int:uid>/edit', methods=['GET','POST'])
@login_required
def users_edit(uid):
    if current_user.role!='admin': flash('관리자만 접근할 수 있습니다.','danger'); return redirect('/')
    conn=get_db(); user=conn.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
    if not user: conn.close(); flash('존재하지 않는 사용자입니다.','danger'); return redirect('/users')
    if request.method=='POST':
        f=request.form; pw=f.get('password','').strip()
        if pw: conn.execute("UPDATE users SET name=?,role=?,password=? WHERE id=?",(f['name'].strip(),f.get('role','user'),generate_password_hash(pw),uid))
        else: conn.execute("UPDATE users SET name=?,role=? WHERE id=?",(f['name'].strip(),f.get('role','user'),uid))
        conn.commit(); conn.close(); flash('사용자 정보가 수정되었습니다.','success'); return redirect('/users')
    conn.close(); return render_template_string(USER_FORM_T, user=user, title='사용자 수정')

@app.route('/users/<int:uid>/delete', methods=['POST'])
@login_required
def users_delete(uid):
    if current_user.role!='admin': flash('관리자만 삭제할 수 있습니다.','danger'); return redirect('/users')
    if uid==current_user.id: flash('자기 자신은 삭제할 수 없습니다.','danger'); return redirect('/users')
    conn=get_db(); conn.execute("DELETE FROM users WHERE id=?",(uid,)); conn.commit(); conn.close()
    flash('사용자가 삭제되었습니다.','success'); return redirect('/users')

@app.route('/api/low_stock_count')
@login_required
def api_low_stock():
    conn=get_db(); cnt=conn.execute("SELECT COUNT(*) as c FROM parts WHERE quantity<=min_quantity").fetchone()['c']; conn.close()
    return jsonify({'count':cnt})

# ── 실행 ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    print("=" * 50)
    print(" 반도체 부품 재고관리 시스템 시작!")
    print("=" * 50)
    print(" 내 PC 접속: http://localhost:5000")
    print(" 기본 계정:  admin / admin1234")
    print(" 종료:       Ctrl+C")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=False)
