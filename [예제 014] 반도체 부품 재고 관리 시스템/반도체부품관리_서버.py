"""
반도체 장비 부품 관리 — 멀티유저 실시간 협업 서버
====================================================
설치:  pip install flask flask-socketio
실행:  python 반도체부품관리_서버.py
접속:  http://localhost:5001  (같은 네트워크라면 http://[내IP]:5001)

여러 사람이 동시에 접속해 셀을 수정하면 실시간으로 반영됩니다.
"""
import os, json, sqlite3, threading
from flask import Flask, send_file, jsonify
from flask_socketio import SocketIO, emit

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, 'parts_collab.db')
HTML_PATH = os.path.join(BASE, '반도체부품관리_엑셀형.html')

app = Flask(__name__, static_folder=BASE, static_url_path='')
app.config['SECRET_KEY'] = 'semi-collab-2025'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading',
                    logger=False, engineio_logger=False)

_lock = threading.Lock()
_state = None          # 현재 스프레드시트 상태 (in-memory)
_users = {}            # sid -> nickname

# ── DB ───────────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS state (
        id INTEGER PRIMARY KEY, data TEXT NOT NULL)''')
    conn.commit(); conn.close()

def db_load():
    global _state
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute('SELECT data FROM state WHERE id=1').fetchone()
    conn.close()
    if row:
        _state = json.loads(row[0])

def db_save(state):
    conn = sqlite3.connect(DB_PATH)
    if conn.execute('SELECT id FROM state WHERE id=1').fetchone():
        conn.execute('UPDATE state SET data=? WHERE id=1', (json.dumps(state),))
    else:
        conn.execute('INSERT INTO state (id, data) VALUES (1, ?)', (json.dumps(state),))
    conn.commit(); conn.close()

# ── STATE MUTATION ────────────────────────────────────────────────────────────

def _calc_inv(state):
    try:
        qi = state['headers'].index('현재수량')
        pi = state['headers'].index('단가(원)')
        vi = state['headers'].index('재고금액')
        for r in state['data']:
            while len(r) <= max(qi, pi, vi):
                r.append('')
            q = int(r[qi] or 0); p = int(r[pi] or 0)
            r[vi] = str(q * p)
    except (ValueError, IndexError):
        pass

def apply_change(state, ch):
    t = ch.get('type')
    if t == 'cell':
        r, c = ch['r'], ch['c']
        while len(state['data']) <= r:
            state['data'].append([''] * len(state['headers']))
        row = state['data'][r]
        while len(row) <= c:
            row.append('')
        row[c] = ch.get('nv', '')
        _calc_inv(state)
    elif t == 'addRow':
        state['data'].insert(ch['r'], ch.get('row', [''] * len(state['headers'])))
    elif t == 'delRow':
        r = ch['r']
        if 0 <= r < len(state['data']):
            state['data'].pop(r)
    elif t == 'addCol':
        c = ch['c']; nm = ch.get('name', '새 항목')
        state['headers'].insert(c, nm)
        state['types'].insert(c, 'text')
        state['widths'].insert(c, 100)
        for row in state['data']:
            row.insert(c, '')
    elif t == 'delCol':
        c = ch['c']
        if 0 <= c < len(state['headers']):
            state['headers'].pop(c); state['types'].pop(c); state['widths'].pop(c)
            for row in state['data']:
                if c < len(row): row.pop(c)
    elif t == 'renameCol':
        c = ch['c']
        if 0 <= c < len(state['headers']):
            state['headers'][c] = ch.get('name', '')
    elif t == 'cellMemo':
        state.setdefault('memos', {})
        key = f"{ch['r']},{ch['c']}"
        txt = ch.get('text', '')
        if txt:
            state['memos'][key] = txt
        elif key in state['memos']:
            del state['memos'][key]
    elif t == 'note_add':
        state.setdefault('notes', [])
        state['notes'].append(ch['note'])
    elif t == 'note_update':
        state.setdefault('notes', [])
        for i, n in enumerate(state['notes']):
            if n['id'] == ch['note']['id']:
                state['notes'][i] = ch['note']; break
    elif t == 'note_delete':
        state['notes'] = [n for n in state.get('notes', []) if n['id'] != ch['id']]
    elif t == 'full_state':
        # Full state replacement (e.g., after CSV import)
        for k in ['headers','types','widths','data','memos','notes']:
            if k in ch: state[k] = ch[k]

# ── BROADCAST USER COUNT ──────────────────────────────────────────────────────

def _broadcast_users():
    socketio.emit('user_count', {'count': len(_users), 'users': list(_users.values())})

# ── ROUTES ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_file(HTML_PATH)

@app.route('/api/state')
def api_state():
    return jsonify(_state or {})

# ── SOCKETIO EVENTS ───────────────────────────────────────────────────────────

from flask_socketio import request as sock_request

@socketio.on('connect')
def on_connect():
    nick = sock_request.args.get('nick', '익명')
    _users[sock_request.sid] = nick
    # Send current state to this client
    if _state:
        emit('server_state', _state)
    _broadcast_users()
    print(f'[접속] {nick} ({sock_request.sid[:8]}…) — 현재 {len(_users)}명 접속 중')

@socketio.on('disconnect')
def on_disconnect():
    nick = _users.pop(sock_request.sid, '?')
    _broadcast_users()
    print(f'[퇴장] {nick} ({sock_request.sid[:8]}…) — 현재 {len(_users)}명 접속 중')

@socketio.on('set_nick')
def on_set_nick(data):
    old = _users.get(sock_request.sid, '익명')
    new = data.get('nick', old)
    _users[sock_request.sid] = new
    _broadcast_users()

@socketio.on('change')
def on_change(ch):
    """클라이언트가 변경사항을 서버에 보냄 → 상태 업데이트 → 다른 클라이언트에 브로드캐스트"""
    with _lock:
        global _state
        if _state is None:
            return
        apply_change(_state, ch)
        db_save(_state)
    # 변경사항을 다른 모든 클라이언트에게 전파 (보낸 클라이언트 제외)
    emit('change', ch, broadcast=True, include_self=False)

@socketio.on('init_state')
def on_init_state(data):
    """클라이언트가 최초 상태를 업로드 (서버에 저장된 상태가 없을 때)"""
    with _lock:
        global _state
        if _state is None:
            _state = data
            db_save(_state)
            print('[초기화] 클라이언트로부터 초기 상태 수신')
        else:
            # 이미 서버 상태가 있으면 클라이언트에 보내줌
            emit('server_state', _state)

@socketio.on('ping_state')
def on_ping():
    """클라이언트가 현재 상태를 요청"""
    if _state:
        emit('server_state', _state)

# ── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    db_load()
    print('=' * 55)
    print(' 반도체 장비 부품 관리 — 멀티유저 협업 서버')
    print('=' * 55)
    print(' 접속 주소 : http://localhost:5001')
    print(' 내부망 공유: http://[내 IP 주소]:5001')
    print(' 여러 PC/브라우저에서 동시 접속하면')
    print(' 셀 수정이 실시간으로 모두에게 반영됩니다.')
    print('=' * 55)
    print(' 종료 : Ctrl+C')
    print('=' * 55)
    socketio.run(app, host='0.0.0.0', port=5001, debug=False)
