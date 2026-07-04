"""
반도체 설비 관리 시스템 — 단일 파일 버전
=====================================================
설치: pip install flask flask-login werkzeug
실행: python 반도체설비관리_단일파일.py
접속: http://localhost:5000  (기본 계정: admin / admin1234)

주요 기능
- 설비 마스터 관리 (설비명/종류/위치/상태/정비 주기)
- 예방정비(PM) 일정 자동 계산 및 임박/지연 알림
- 정비 이력 관리 (예방정비 / 고장수리 / 일상점검, 다운타임 기록)
- 대시보드에서 가동 현황 및 PM 임박 설비 한눈에 확인
"""

import sqlite3, os, sys
from datetime import date, datetime, timedelta
from flask import Flask, render_template_string, request, redirect, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash as _gen_hash, check_password_hash

# scrypt 미지원 환경(일부 Windows/Mac) 대비 — pbkdf2:sha256 고정
def generate_password_hash(pw):
    try:
        return _gen_hash(pw, method='pbkdf2:sha256')
    except Exception:
        return _gen_hash(pw)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'semi-eq-2024-secret')
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = '로그인이 필요합니다.'

# Windows/Mac/Linux 모두에서 현재 폴더에 DB 생성
try:
    _base = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _base = os.getcwd()
DB_PATH = os.path.join(_base, 'equipment.db')

EQ_STATUSES = ['가동중', '점검중', '고장', '정지']
MAINT_TYPES = ['예방정비(PM)', '고장수리', '일상점검']


# ── 데이터베이스 ─────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL, name TEXT NOT NULL, role TEXT DEFAULT 'user',
        created_at TEXT DEFAULT (datetime('now','localtime')))''')
    c.execute('''CREATE TABLE IF NOT EXISTS equipment (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL, type TEXT, location TEXT, status TEXT DEFAULT '가동중',
        pm_cycle_days INTEGER DEFAULT 30, last_pm_date TEXT, install_date TEXT, note TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')))''')
    c.execute('''CREATE TABLE IF NOT EXISTS maintenance_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, equipment_id INTEGER NOT NULL,
        type TEXT NOT NULL, work_date TEXT NOT NULL, downtime_minutes INTEGER DEFAULT 0,
        description TEXT, user_id INTEGER,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (equipment_id) REFERENCES equipment(id),
        FOREIGN KEY (user_id) REFERENCES users(id))''')
    try:
        c.execute("INSERT INTO users (username,password,name,role) VALUES (?,?,?,?)",
                  ('admin', generate_password_hash('admin1234'), '관리자', 'admin'))
    except sqlite3.IntegrityError:
        pass
    conn.commit()
    conn.close()


def pm_info(eq):
    """설비 정보(sqlite3.Row)로부터 다음 PM 예정일과 D-day를 계산"""
    base_str = eq['last_pm_date'] or eq['install_date'] or (eq['created_at'] or '')[:10]
    try:
        base_date = datetime.strptime((base_str or '')[:10], '%Y-%m-%d').date()
    except ValueError:
        base_date = date.today()
    cycle = eq['pm_cycle_days'] or 30
    next_pm = base_date + timedelta(days=cycle)
    return next_pm.isoformat(), (next_pm - date.today()).days


class User(UserMixin):
    def __init__(self, id, username, name, role):
        self.id = id; self.username = username; self.name = name; self.role = role


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return User(row['id'], row['username'], row['name'], row['role']) if row else None


# ── 공통 HTML 조각 (Jinja2 블록 상속 없이 조립) ──────────────────────────────

_CSS = """
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
.bs-고장{background:#fee2e2;color:#991b1b}.bs-정지{background:#e5e7eb;color:#374151}
.mt-예방정비\\(PM\\){background:#dbeafe;color:#1e40af}.mt-고장수리{background:#fee2e2;color:#991b1b}
.mt-일상점검{background:#dcfce7;color:#166534}
.ls{background:#fff7ed!important}
.table th{background:#f8f9fa;font-size:12px}.table td{font-size:13px;vertical-align:middle}
@media(max-width:768px){
  #sb{width:56px}
  #sb .br span,#sb nav a span,#sb .ns,#sb .ui span{display:none}
  #sb .br i{margin:0 auto}
  #sb nav a{justify-content:center;padding:12px}
  #mn{margin-left:56px}
}
</style>"""

_SIDEBAR = """
<div id="sb">
  <div class="br"><i class="bi bi-cpu"></i><span>반도체 설비<br>관리 시스템</span></div>
  <nav>
    <div class="ns">메인</div>
    <a href="/" class="{{ 'ac' if request.endpoint=='dashboard' }}"><i class="bi bi-speedometer2"></i><span>대시보드</span></a>
    <div class="ns">설비</div>
    <a href="/equipment" class="{{ 'ac' if 'equipment' in (request.endpoint or '') }}"><i class="bi bi-tools"></i><span>설비 관리</span></a>
    <a href="/maintenance" class="{{ 'ac' if 'maintenance' in (request.endpoint or '') and request.endpoint!='maintenance_add' }}"><i class="bi bi-clipboard2-pulse"></i><span>정비 이력</span></a>
    <a href="/maintenance/add" class="{{ 'ac' if request.endpoint=='maintenance_add' }}"><i class="bi bi-plus-circle"></i><span>정비 등록</span></a>
    {% if current_user.role=='admin' %}
    <div class="ns">관리</div>
    <a href="/users" class="{{ 'ac' if 'users' in (request.endpoint or '') }}"><i class="bi bi-people"></i><span>사용자 관리</span></a>
    {% endif %}
  </nav>
  <div class="ui">
    <i class="bi bi-person-circle me-1"></i>
    <span>{{ current_user.name }}{% if current_user.role=='admin' %}<span class="badge bg-warning text-dark ms-1" style="font-size:9px">관리자</span>{% endif %}</span>
    <a href="/logout" class="btn btn-sm btn-outline-light d-block mt-2"><span>로그아웃</span></a>
  </div>
</div>"""

_JS = """
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
function tick(){var e=document.getElementById('clk');if(e)e.textContent=new Date().toLocaleString('ko-KR');}
tick();setInterval(tick,1000);
function chkPm(){fetch('/api/pm_alert_count').then(r=>r.json()).then(d=>{
  var e=document.getElementById('lsb');if(!e)return;
  e.innerHTML=d.count>0?`<a href="/equipment" class="badge bg-danger text-decoration-none"><i class="bi bi-exclamation-triangle-fill me-1"></i>PM 임박/지연 ${d.count}건</a>`:'';
}).catch(()=>{});}
{% if current_user.is_authenticated %}chkPm();setInterval(chkPm,30000);{% endif %}
</script>"""


def _page(title, content, scripts=''):
    """공통 레이아웃으로 완전한 HTML 페이지 조립 (Jinja2 블록 상속 없음)"""
    return (
        '<!DOCTYPE html><html lang="ko"><head>'
        '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>' + title + ' - 반도체 설비관리</title>'
        '<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">'
        '<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">'
        + _CSS +
        '</head><body>'
        '{% if current_user.is_authenticated %}'
        + _SIDEBAR +
        '{% endif %}'
        '<div id="mn">'
        '{% if current_user.is_authenticated %}'
        '<div id="tb">'
        '<h1>' + title + '</h1>'
        '<div class="d-flex align-items-center gap-2">'
        '<span id="lsb"></span><small class="text-muted" id="clk"></small>'
        '</div></div>'
        '{% endif %}'
        '<div class="{% if current_user.is_authenticated %}ca{% endif %}">'
        '{% with messages=get_flashed_messages(with_categories=true) %}'
        '{% if messages %}{% for _c,_m in messages %}'
        '<div class="alert alert-{{_c}} alert-dismissible fade show py-2">{{_m}}'
        '<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>'
        '{% endfor %}{% endif %}{% endwith %}'
        + content +
        '</div></div>'
        + _JS + scripts +
        '</body></html>'
    )


# ── 각 페이지 템플릿 ──────────────────────────────────────────────────────────

LOGIN_T = """<!DOCTYPE html><html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>로그인 - 반도체 설비관리</title>
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
  <h1>반도체 설비관리</h1>
  <p>Semiconductor Equipment Management System</p>
</div><div class="lb">
  {% with messages=get_flashed_messages(with_categories=true) %}{% for _c,_m in messages %}
  <div class="alert alert-{{_c}} alert-dismissible fade show py-2">{{_m}}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>
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

DASH_T = _page('대시보드', """
<div class="row g-3 mb-4">
  <div class="col-6 col-lg-3"><div class="card sc h-100"><div class="card-body d-flex align-items-center gap-3">
    <div class="ib" style="background:#dbeafe"><i class="bi bi-tools text-primary"></i></div>
    <div><div class="text-muted" style="font-size:11px">전체 설비 수</div><div class="fw-bold fs-4">{{total_equipment}}</div></div>
  </div></div></div>
  <div class="col-6 col-lg-3"><div class="card sc h-100"><div class="card-body d-flex align-items-center gap-3">
    <div class="ib" style="background:#dcfce7"><i class="bi bi-play-circle text-success"></i></div>
    <div><div class="text-muted" style="font-size:11px">가동중 설비</div><div class="fw-bold fs-4">{{running}}</div></div>
  </div></div></div>
  <div class="col-6 col-lg-3"><div class="card sc h-100"><div class="card-body d-flex align-items-center gap-3">
    <div class="ib" style="background:#fee2e2"><i class="bi bi-exclamation-triangle text-danger"></i></div>
    <div><div class="text-muted" style="font-size:11px">점검/고장 설비</div><div class="fw-bold fs-4 text-danger">{{issue_cnt}}</div></div>
  </div></div></div>
  <div class="col-6 col-lg-3"><div class="card sc h-100"><div class="card-body d-flex align-items-center gap-3">
    <div class="ib" style="background:#fef3c7"><i class="bi bi-calendar-event text-warning"></i></div>
    <div><div class="text-muted" style="font-size:11px">PM 임박/지연</div><div class="fw-bold fs-4">{{pm_due_cnt}}</div></div>
  </div></div></div>
</div>
<div class="row g-3">
  <div class="col-lg-7"><div class="card sc"><div class="card-header bg-white fw-semibold d-flex justify-content-between align-items-center">
    <span><i class="bi bi-clock-history me-2 text-primary"></i>최근 정비 이력</span>
    <a href="/maintenance" class="btn btn-sm btn-outline-primary">전체보기</a>
  </div><div class="card-body p-0"><div class="table-responsive">
    <table class="table table-hover mb-0"><thead><tr><th>작업일</th><th>설비</th><th>구분</th><th class="text-center">다운타임</th><th>작업자</th></tr></thead>
    <tbody>{% for r in recent %}<tr>
      <td><small>{{r.work_date}}</small></td><td>{{r.eq_name or '-'}}</td>
      <td><span class="badge mt-{{r.type}}">{{r.type}}</span></td>
      <td class="text-center">{{r.downtime_minutes}}분</td><td>{{r.user_name or '-'}}</td>
    </tr>{% else %}<tr><td colspan="5" class="text-center text-muted py-3">정비 이력이 없습니다.</td></tr>{% endfor %}</tbody>
    </table></div></div></div></div>
  <div class="col-lg-5">
    <div class="card sc mb-3"><div class="card-header bg-white fw-semibold">
      <i class="bi bi-exclamation-triangle-fill text-danger me-2"></i>PM 임박/지연 설비</div>
      <ul class="list-group list-group-flush">
        {% for e in pm_list %}<li class="list-group-item d-flex justify-content-between align-items-center py-2">
          <div><div style="font-size:13px;font-weight:600">{{e.name}}</div><small class="text-muted">{{e.code}} · 예정일 {{e.next_pm}}</small></div>
          {% if e.d_day<0 %}<span class="badge bg-danger">지연 {{-e.d_day}}일</span>
          {% else %}<span class="badge bg-warning text-dark">D-{{e.d_day}}</span>{% endif %}
        </li>{% else %}<li class="list-group-item text-center text-muted py-3">임박한 PM 일정 없음</li>{% endfor %}
      </ul>
    </div>
    <div class="card sc"><div class="card-header bg-white fw-semibold"><i class="bi bi-tools me-2 text-success"></i>설비 상태 현황</div>
      <div class="card-body">
        {% for s in eq_status %}<div class="d-flex justify-content-between align-items-center mb-2">
          <span class="badge bs-{{s.status}} px-3 py-2" style="font-size:12px">{{s.status}}</span><strong>{{s.cnt}}대</strong>
        </div>{% else %}<p class="text-muted text-center mb-0">등록된 설비 없음</p>{% endfor %}
      </div>
    </div>
  </div>
</div>
<div class="row g-3 mt-1"><div class="col-12"><div class="card sc"><div class="card-body">
  <div class="d-flex gap-2 flex-wrap">
    <a href="/maintenance/add" class="btn btn-primary"><i class="bi bi-plus-circle me-1"></i>정비 등록</a>
    <a href="/equipment/add" class="btn btn-outline-primary"><i class="bi bi-tools me-1"></i>설비 등록</a>
    <a href="/equipment" class="btn btn-outline-secondary"><i class="bi bi-search me-1"></i>설비 검색</a>
  </div>
</div></div></div></div>
""")

EQ_T = _page('설비 관리', """
<div class="card mb-3"><div class="card-body py-2">
  <form class="row g-2 align-items-center" method="GET">
    <div class="col-auto flex-grow-1"><div class="input-group">
      <span class="input-group-text"><i class="bi bi-search"></i></span>
      <input type="text" name="q" class="form-control" placeholder="설비명, 코드, 위치 검색..." value="{{q}}">
    </div></div>
    <div class="col-auto"><select name="status" class="form-select">
      <option value="">전체 상태</option>
      {% for s in statuses %}<option value="{{s}}" {{'selected' if sel_status==s}}>{{s}}</option>{% endfor %}
    </select></div>
    <div class="col-auto"><select name="type" class="form-select">
      <option value="">전체 종류</option>
      {% for t in types %}<option value="{{t.type}}" {{'selected' if sel_type==t.type}}>{{t.type}}</option>{% endfor %}
    </select></div>
    <div class="col-auto"><button type="submit" class="btn btn-primary">검색</button>
      <a href="/equipment" class="btn btn-outline-secondary ms-1">초기화</a></div>
    <div class="col-auto ms-auto"><a href="/equipment/add" class="btn btn-success"><i class="bi bi-plus-circle me-1"></i>설비 등록</a></div>
  </form>
</div></div>
<div class="row g-3">
  {% for eq in equipment %}<div class="col-md-6 col-lg-4"><div class="card h-100 {{'ls' if eq.d_day<=7}}"><div class="card-body">
    <div class="d-flex justify-content-between align-items-start mb-2">
      <div><code class="text-muted" style="font-size:11px">{{eq.code}}</code><h6 class="mb-0 mt-1 fw-bold">{{eq.name}}</h6>
        {% if eq.type %}<span class="badge bg-light text-dark mt-1">{{eq.type}}</span>{% endif %}</div>
      <span class="badge bs-{{eq.status}} px-2 py-1">{{eq.status}}</span>
    </div>
    {% if eq.location %}<p class="text-muted mb-1 mt-2" style="font-size:12px"><i class="bi bi-geo-alt me-1"></i>{{eq.location}}</p>{% endif %}
    <p class="mb-1" style="font-size:12px">
      <i class="bi bi-calendar-event me-1"></i>다음 PM 예정: <strong>{{eq.next_pm}}</strong>
      {% if eq.d_day<0 %}<span class="badge bg-danger ms-1">지연 {{-eq.d_day}}일</span>
      {% elif eq.d_day<=7 %}<span class="badge bg-warning text-dark ms-1">D-{{eq.d_day}}</span>
      {% else %}<span class="badge bg-light text-dark ms-1">D-{{eq.d_day}}</span>{% endif %}
    </p>
    {% if eq.note %}<p class="text-muted mb-2" style="font-size:11px">{{eq.note}}</p>{% endif %}
    <div class="d-flex gap-1 mt-2 flex-wrap">
      <a href="/maintenance/add?equipment_id={{eq.id}}" class="btn btn-sm btn-outline-success"><i class="bi bi-clipboard2-pulse me-1"></i>정비 등록</a>
      <a href="/equipment/{{eq.id}}/edit" class="btn btn-sm btn-outline-primary"><i class="bi bi-pencil me-1"></i>수정</a>
      {% if current_user.role=='admin' %}
      <form method="POST" action="/equipment/{{eq.id}}/delete" onsubmit="return confirm('삭제하시겠습니까?')">
        <button type="submit" class="btn btn-sm btn-outline-danger"><i class="bi bi-trash me-1"></i>삭제</button>
      </form>{% endif %}
    </div>
  </div></div></div>
  {% else %}<div class="col-12"><div class="alert alert-secondary text-center py-4">
    <i class="bi bi-tools fs-2 d-block mb-2"></i>등록된 설비가 없습니다.
    <a href="/equipment/add" class="d-block mt-2">설비 등록하기</a>
  </div></div>{% endfor %}
</div>""")

EQ_FORM_T = _page('{{title}}', """
<div class="row justify-content-center"><div class="col-lg-7"><div class="card">
  <div class="card-header bg-white fw-semibold"><i class="bi bi-tools me-2"></i>{{title}}</div>
  <div class="card-body"><form method="POST"><div class="row g-3">
    <div class="col-md-4"><label class="form-label fw-semibold">설비 코드 <span class="text-danger">*</span></label>
      <input type="text" name="code" class="form-control" value="{{eq.code if eq else ''}}" {{'readonly' if eq}} required placeholder="예: EQ-001"></div>
    <div class="col-md-8"><label class="form-label fw-semibold">설비명 <span class="text-danger">*</span></label>
      <input type="text" name="name" class="form-control" value="{{eq.name if eq else ''}}" required></div>
    <div class="col-md-6"><label class="form-label fw-semibold">설비 종류</label>
      <input type="text" name="type" class="form-control" value="{{eq.type if eq else ''}}" placeholder="예: 노광기, 식각기, 증착기, 세정기"></div>
    <div class="col-md-6"><label class="form-label fw-semibold">설치 위치</label>
      <input type="text" name="location" class="form-control" value="{{eq.location if eq else ''}}" placeholder="예: 1공장 A라인"></div>
    <div class="col-md-4"><label class="form-label fw-semibold">상태</label>
      <select name="status" class="form-select">{% for s in statuses %}
        <option value="{{s}}" {{'selected' if eq and eq.status==s}}>{{s}}</option>{% endfor %}
      </select></div>
    <div class="col-md-4"><label class="form-label fw-semibold">정비 주기(일)</label>
      <input type="number" name="pm_cycle_days" class="form-control" value="{{eq.pm_cycle_days if eq else 30}}" min="1"></div>
    <div class="col-md-4"><label class="form-label fw-semibold">최근 정비일</label>
      <input type="date" name="last_pm_date" class="form-control" value="{{eq.last_pm_date if eq else ''}}"></div>
    <div class="col-md-6"><label class="form-label fw-semibold">설치일</label>
      <input type="date" name="install_date" class="form-control" value="{{eq.install_date if eq else ''}}"></div>
    <div class="col-12"><label class="form-label fw-semibold">비고</label>
      <textarea name="note" class="form-control" rows="2">{{eq.note if eq else ''}}</textarea></div>
  </div><div class="d-flex gap-2 mt-4">
    <button type="submit" class="btn btn-primary"><i class="bi bi-check2 me-1"></i>저장</button>
    <a href="/equipment" class="btn btn-outline-secondary">취소</a>
  </div></form></div>
</div></div></div>""")

MAINT_T = _page('정비 이력', """
<div class="card mb-3"><div class="card-body py-2">
  <form class="row g-2 align-items-center" method="GET">
    <div class="col-auto flex-grow-1"><div class="input-group">
      <span class="input-group-text"><i class="bi bi-search"></i></span>
      <input type="text" name="q" class="form-control" placeholder="설비명, 작업내용, 작업자 검색..." value="{{q}}">
    </div></div>
    <div class="col-auto"><select name="type" class="form-select">
      <option value="">전체 구분</option>
      {% for t in maint_types %}<option value="{{t}}" {{'selected' if sel_type==t}}>{{t}}</option>{% endfor %}
    </select></div>
    <div class="col-auto"><button type="submit" class="btn btn-primary">검색</button>
      <a href="/maintenance" class="btn btn-outline-secondary ms-1">초기화</a></div>
    <div class="col-auto ms-auto"><a href="/maintenance/add" class="btn btn-success"><i class="bi bi-plus-circle me-1"></i>정비 등록</a></div>
  </form>
</div></div>
<div class="card"><div class="card-header bg-white fw-semibold">
  정비 이력 <span class="badge bg-secondary">총 {{total}}건</span>
</div><div class="card-body p-0"><div class="table-responsive">
  <table class="table table-hover mb-0"><thead>
    <tr><th>작업일</th><th>설비</th><th>구분</th><th class="text-center">다운타임</th><th>작업내용</th><th>작업자</th>
    {% if current_user.role=='admin' %}<th>삭제</th>{% endif %}</tr>
  </thead><tbody>
    {% for r in rows %}<tr>
      <td><small>{{r.work_date}}</small></td><td>{{r.eq_name or '-'}} <small class="text-muted">{{r.eq_code or ''}}</small></td>
      <td><span class="badge mt-{{r.type}}">{{r.type}}</span></td>
      <td class="text-center">{{r.downtime_minutes}}분</td>
      <td><small class="text-muted">{{r.description or ''}}</small></td><td>{{r.user_name or '-'}}</td>
      {% if current_user.role=='admin' %}<td>
        <form method="POST" action="/maintenance/{{r.id}}/delete" onsubmit="return confirm('이 정비 이력을 삭제하시겠습니까?')">
          <button type="submit" class="btn btn-sm btn-outline-danger"><i class="bi bi-trash"></i></button>
        </form></td>{% endif %}
    </tr>{% else %}<tr><td colspan="7" class="text-center text-muted py-4">이력이 없습니다.</td></tr>{% endfor %}
  </tbody></table>
</div></div>
{% if total_pages>1 %}<div class="card-footer bg-white"><nav><ul class="pagination pagination-sm mb-0 justify-content-center">
  {% for pg in range(1,total_pages+1) %}<li class="page-item {{'active' if pg==page}}">
    <a class="page-link" href="?page={{pg}}&q={{q}}&type={{sel_type}}">{{pg}}</a></li>{% endfor %}
</ul></nav></div>{% endif %}
</div>""")

MAINT_FORM_T = _page('정비 등록', """
<div class="row justify-content-center"><div class="col-lg-6"><div class="card">
  <div class="card-header bg-white fw-semibold"><i class="bi bi-clipboard2-pulse me-2"></i>정비 이력 등록</div>
  <div class="card-body"><form method="POST"><div class="row g-3">
    <div class="col-12"><label class="form-label fw-semibold">설비 <span class="text-danger">*</span></label>
      <select name="equipment_id" class="form-select" required>
        <option value="">-- 설비 선택 --</option>
        {% for eq in equipment %}<option value="{{eq.id}}" {{'selected' if pre_eid and pre_eid|string==eq.id|string}}>[{{eq.code}}] {{eq.name}} ({{eq.location or '위치미정'}})</option>{% endfor %}
      </select></div>
    <div class="col-md-6"><label class="form-label fw-semibold">구분 <span class="text-danger">*</span></label>
      <select name="type" class="form-select" required>
        {% for t in maint_types %}<option value="{{t}}">{{t}}</option>{% endfor %}
      </select></div>
    <div class="col-md-6"><label class="form-label fw-semibold">작업일 <span class="text-danger">*</span></label>
      <input type="date" name="work_date" class="form-control" value="{{today}}" required></div>
    <div class="col-md-6"><label class="form-label fw-semibold">다운타임(분)</label>
      <input type="number" name="downtime_minutes" class="form-control" min="0" value="0"></div>
    <div class="col-md-6"><label class="form-label fw-semibold">작업 후 설비 상태</label>
      <select name="new_status" class="form-select">
        {% for s in statuses %}<option value="{{s}}" {{'selected' if s=='가동중'}}>{{s}}</option>{% endfor %}
      </select></div>
    <div class="col-12"><label class="form-label fw-semibold">작업 내용</label>
      <textarea name="description" class="form-control" rows="3" placeholder="예: 필터 교체, 챔버 세정, 파츠 교환 등"></textarea></div>
  </div>
  <p class="text-muted mt-3 mb-0" style="font-size:12px"><i class="bi bi-info-circle me-1"></i>구분을 '예방정비(PM)'로 등록하면 해당 설비의 최근 정비일이 자동으로 갱신되어 다음 PM 예정일이 다시 계산됩니다.</p>
  <div class="d-flex gap-2 mt-4">
    <button type="submit" class="btn btn-primary"><i class="bi bi-check2 me-1"></i>정비 등록</button>
    <a href="/maintenance" class="btn btn-outline-secondary">취소</a>
  </div></form></div>
</div></div></div>
""")

USERS_T = _page('사용자 관리', """
<div class="d-flex justify-content-end mb-3">
  <a href="/users/add" class="btn btn-success"><i class="bi bi-person-plus me-1"></i>사용자 등록</a>
</div>
<div class="card"><div class="card-header bg-white fw-semibold">
  사용자 목록 <span class="badge bg-secondary">{{users|length}}명</span>
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
      <form method="POST" action="/users/{{u.id}}/delete" onsubmit="return confirm('삭제하시겠습니까?')">
        <button type="submit" class="btn btn-sm btn-outline-danger"><i class="bi bi-trash"></i></button>
      </form>{% endif %}
    </div></td>
  </tr>{% endfor %}
</tbody></table></div></div>""")

USER_FORM_T = _page('{{title}}', """
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
</div></div></div>""")


# ── 라우트 ───────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect('/')
    if request.method == 'POST':
        u, pw = request.form['username'], request.form['password']
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
        conn.close()
        if row and check_password_hash(row['password'], pw):
            login_user(User(row['id'], row['username'], row['name'], row['role']))
            return redirect('/')
        flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'danger')
    return render_template_string(LOGIN_T)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/login')


@app.route('/')
@login_required
def dashboard():
    conn = get_db()
    all_eq = conn.execute("SELECT * FROM equipment").fetchall()
    pm_list = []
    for r in all_eq:
        next_pm, d_day = pm_info(r)
        if d_day <= 7:
            d = dict(r); d['next_pm'] = next_pm; d['d_day'] = d_day
            pm_list.append(d)
    pm_list.sort(key=lambda x: x['d_day'])
    d = dict(
        total_equipment=len(all_eq),
        running=sum(1 for r in all_eq if r['status'] == '가동중'),
        issue_cnt=sum(1 for r in all_eq if r['status'] in ('점검중', '고장')),
        pm_due_cnt=len(pm_list),
        recent=conn.execute('''SELECT ml.*, u.name as user_name, e.name as eq_name, e.code as eq_code
            FROM maintenance_log ml LEFT JOIN users u ON ml.user_id=u.id
            LEFT JOIN equipment e ON ml.equipment_id=e.id
            ORDER BY ml.work_date DESC, ml.id DESC LIMIT 10''').fetchall(),
        pm_list=pm_list[:10],
        eq_status=conn.execute("SELECT status,COUNT(*) as cnt FROM equipment GROUP BY status").fetchall(),
    )
    conn.close()
    return render_template_string(DASH_T, **d)


@app.route('/equipment')
@login_required
def equipment_list():
    q = request.args.get('q', ''); status = request.args.get('status', ''); etype = request.args.get('type', '')
    conn = get_db(); sql = "SELECT * FROM equipment WHERE 1=1"; params = []
    if q: sql += " AND (name LIKE ? OR code LIKE ? OR location LIKE ?)"; params += [f'%{q}%'] * 3
    if status: sql += " AND status=?"; params.append(status)
    if etype: sql += " AND type=?"; params.append(etype)
    rows = conn.execute(sql + " ORDER BY code", params).fetchall()
    types = conn.execute("SELECT DISTINCT type FROM equipment WHERE type IS NOT NULL AND type!='' ORDER BY type").fetchall()
    conn.close()
    eqs = []
    for r in rows:
        next_pm, d_day = pm_info(r)
        d = dict(r); d['next_pm'] = next_pm; d['d_day'] = d_day
        eqs.append(d)
    return render_template_string(EQ_T, equipment=eqs, types=types, statuses=EQ_STATUSES, q=q, sel_status=status, sel_type=etype)


@app.route('/equipment/add', methods=['GET', 'POST'])
@login_required
def equipment_add():
    if request.method == 'POST':
        f = request.form; conn = get_db()
        try:
            conn.execute("""INSERT INTO equipment (code,name,type,location,status,pm_cycle_days,last_pm_date,install_date,note)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (f['code'].strip(), f['name'].strip(), f.get('type', '').strip(), f.get('location', '').strip(),
                 f.get('status', '가동중'), int(f.get('pm_cycle_days', 30) or 30),
                 f.get('last_pm_date') or None, f.get('install_date') or None, f.get('note', '').strip()))
            conn.commit(); flash('설비가 등록되었습니다.', 'success'); conn.close(); return redirect('/equipment')
        except sqlite3.IntegrityError:
            flash('이미 존재하는 설비 코드입니다.', 'danger')
        finally:
            conn.close()
    return render_template_string(EQ_FORM_T, eq=None, title='설비 등록', statuses=EQ_STATUSES)


@app.route('/equipment/<int:eid>/edit', methods=['GET', 'POST'])
@login_required
def equipment_edit(eid):
    conn = get_db(); eq = conn.execute("SELECT * FROM equipment WHERE id=?", (eid,)).fetchone()
    if not eq: conn.close(); flash('존재하지 않는 설비입니다.', 'danger'); return redirect('/equipment')
    if request.method == 'POST':
        f = request.form
        conn.execute("""UPDATE equipment SET name=?,type=?,location=?,status=?,pm_cycle_days=?,last_pm_date=?,install_date=?,note=?
            WHERE id=?""",
            (f['name'].strip(), f.get('type', '').strip(), f.get('location', '').strip(), f.get('status', '가동중'),
             int(f.get('pm_cycle_days', 30) or 30), f.get('last_pm_date') or None, f.get('install_date') or None,
             f.get('note', '').strip(), eid))
        conn.commit(); conn.close(); flash('설비 정보가 수정되었습니다.', 'success'); return redirect('/equipment')
    conn.close()
    return render_template_string(EQ_FORM_T, eq=eq, title='설비 수정', statuses=EQ_STATUSES)


@app.route('/equipment/<int:eid>/delete', methods=['POST'])
@login_required
def equipment_delete(eid):
    if current_user.role != 'admin': flash('관리자만 삭제할 수 있습니다.', 'danger'); return redirect('/equipment')
    conn = get_db(); conn.execute("DELETE FROM equipment WHERE id=?", (eid,)); conn.commit(); conn.close()
    flash('설비가 삭제되었습니다.', 'success'); return redirect('/equipment')


@app.route('/maintenance')
@login_required
def maintenance_list():
    page = int(request.args.get('page', 1)); per = 20; offset = (page - 1) * per
    q = request.args.get('q', ''); mtype = request.args.get('type', ''); conn = get_db()
    base = '''FROM maintenance_log ml LEFT JOIN users u ON ml.user_id=u.id
              LEFT JOIN equipment e ON ml.equipment_id=e.id WHERE 1=1'''
    params = []
    if q: base += " AND (e.name LIKE ? OR ml.description LIKE ? OR u.name LIKE ?)"; params += [f'%{q}%'] * 3
    if mtype: base += " AND ml.type=?"; params.append(mtype)
    total = conn.execute(f"SELECT COUNT(*) as c {base}", params).fetchone()['c']
    rows = conn.execute(
        f"SELECT ml.*,u.name as user_name,e.name as eq_name,e.code as eq_code {base} ORDER BY ml.work_date DESC, ml.id DESC LIMIT ? OFFSET ?",
        params + [per, offset]).fetchall()
    conn.close()
    return render_template_string(MAINT_T, rows=rows, page=page, total_pages=(total + per - 1) // per,
                                   q=q, sel_type=mtype, total=total, maint_types=MAINT_TYPES)


@app.route('/maintenance/add', methods=['GET', 'POST'])
@login_required
def maintenance_add():
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        eid = int(f['equipment_id'])
        mtype = f.get('type', MAINT_TYPES[-1])
        work_date = f.get('work_date') or date.today().isoformat()
        downtime = int(f.get('downtime_minutes', 0) or 0)
        new_status = f.get('new_status', '가동중')
        conn.execute("""INSERT INTO maintenance_log (equipment_id,type,work_date,downtime_minutes,description,user_id)
            VALUES (?,?,?,?,?,?)""",
            (eid, mtype, work_date, downtime, f.get('description', '').strip(), current_user.id))
        if mtype == '예방정비(PM)':
            conn.execute("UPDATE equipment SET last_pm_date=?,status=? WHERE id=?", (work_date, new_status, eid))
        else:
            conn.execute("UPDATE equipment SET status=? WHERE id=?", (new_status, eid))
        conn.commit(); flash('정비 이력이 등록되었습니다.', 'success'); conn.close(); return redirect('/maintenance')
    eqs = conn.execute("SELECT * FROM equipment ORDER BY code").fetchall()
    conn.close()
    return render_template_string(MAINT_FORM_T, equipment=eqs, pre_eid=request.args.get('equipment_id', ''),
                                   today=date.today().isoformat(), maint_types=MAINT_TYPES, statuses=EQ_STATUSES)


@app.route('/maintenance/<int:mid>/delete', methods=['POST'])
@login_required
def maintenance_delete(mid):
    if current_user.role != 'admin': flash('관리자만 삭제할 수 있습니다.', 'danger'); return redirect('/maintenance')
    conn = get_db(); conn.execute("DELETE FROM maintenance_log WHERE id=?", (mid,)); conn.commit(); conn.close()
    flash('정비 이력이 삭제되었습니다.', 'success'); return redirect('/maintenance')


@app.route('/users')
@login_required
def users_list():
    if current_user.role != 'admin': flash('관리자만 접근할 수 있습니다.', 'danger'); return redirect('/')
    conn = get_db(); users = conn.execute("SELECT * FROM users ORDER BY role DESC,name").fetchall(); conn.close()
    return render_template_string(USERS_T, users=users)


@app.route('/users/add', methods=['GET', 'POST'])
@login_required
def users_add():
    if current_user.role != 'admin': flash('관리자만 접근할 수 있습니다.', 'danger'); return redirect('/')
    if request.method == 'POST':
        f = request.form; conn = get_db()
        try:
            conn.execute("INSERT INTO users (username,password,name,role) VALUES (?,?,?,?)",
                (f['username'].strip(), generate_password_hash(f['password']), f['name'].strip(), f.get('role', 'user')))
            conn.commit(); flash('사용자가 등록되었습니다.', 'success'); conn.close(); return redirect('/users')
        except sqlite3.IntegrityError:
            flash('이미 존재하는 아이디입니다.', 'danger')
        finally:
            conn.close()
    return render_template_string(USER_FORM_T, user=None, title='사용자 등록')


@app.route('/users/<int:uid>/edit', methods=['GET', 'POST'])
@login_required
def users_edit(uid):
    if current_user.role != 'admin': flash('관리자만 접근할 수 있습니다.', 'danger'); return redirect('/')
    conn = get_db(); user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user: conn.close(); flash('존재하지 않는 사용자입니다.', 'danger'); return redirect('/users')
    if request.method == 'POST':
        f = request.form; pw = f.get('password', '').strip()
        if pw:
            conn.execute("UPDATE users SET name=?,role=?,password=? WHERE id=?",
                (f['name'].strip(), f.get('role', 'user'), generate_password_hash(pw), uid))
        else:
            conn.execute("UPDATE users SET name=?,role=? WHERE id=?", (f['name'].strip(), f.get('role', 'user'), uid))
        conn.commit(); conn.close(); flash('사용자 정보가 수정되었습니다.', 'success'); return redirect('/users')
    conn.close()
    return render_template_string(USER_FORM_T, user=user, title='사용자 수정')


@app.route('/users/<int:uid>/delete', methods=['POST'])
@login_required
def users_delete(uid):
    if current_user.role != 'admin': flash('관리자만 삭제할 수 있습니다.', 'danger'); return redirect('/users')
    if uid == current_user.id: flash('자기 자신은 삭제할 수 없습니다.', 'danger'); return redirect('/users')
    conn = get_db(); conn.execute("DELETE FROM users WHERE id=?", (uid,)); conn.commit(); conn.close()
    flash('사용자가 삭제되었습니다.', 'success'); return redirect('/users')


@app.route('/api/pm_alert_count')
@login_required
def api_pm_alert():
    conn = get_db(); rows = conn.execute("SELECT * FROM equipment").fetchall(); conn.close()
    cnt = sum(1 for r in rows if pm_info(r)[1] <= 7)
    return jsonify({'count': cnt})


# ── 실행 ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    print("=" * 50)
    print(" 반도체 설비 관리 시스템 시작!")
    print("=" * 50)
    print(" 내 PC 접속: http://localhost:5000")
    print(" 기본 계정:  admin / admin1234")
    print(" 종료:       Ctrl+C")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=False)
