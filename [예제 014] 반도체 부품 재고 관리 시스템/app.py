import sqlite3
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'semiconductor-inventory-secret-2024')

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = '로그인이 필요합니다.'

DB_PATH = os.path.join(os.path.dirname(__file__), 'inventory.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        name TEXT NOT NULL,
        role TEXT DEFAULT 'user',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS equipment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        location TEXT,
        status TEXT DEFAULT '가동중',
        note TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS parts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        category TEXT,
        quantity INTEGER DEFAULT 0,
        min_quantity INTEGER DEFAULT 5,
        unit TEXT DEFAULT 'EA',
        unit_price INTEGER DEFAULT 0,
        location TEXT,
        supplier TEXT,
        note TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS replacement_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipment_id INTEGER,
        part_id INTEGER,
        quantity INTEGER NOT NULL,
        reason TEXT,
        note TEXT,
        user_id INTEGER,
        replaced_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (equipment_id) REFERENCES equipment(id),
        FOREIGN KEY (part_id) REFERENCES parts(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')

    # 기본 관리자 계정
    try:
        c.execute("INSERT INTO users (username, password, name, role) VALUES (?, ?, ?, ?)",
                  ('admin', generate_password_hash('admin1234'), '관리자', 'admin'))
    except sqlite3.IntegrityError:
        pass

    conn.commit()
    conn.close()


class User(UserMixin):
    def __init__(self, id, username, name, role):
        self.id = id
        self.username = username
        self.name = name
        self.role = role


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if row:
        return User(row['id'], row['username'], row['name'], row['role'])
    return None


# ─── 인증 ────────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if row and check_password_hash(row['password'], password):
            user = User(row['id'], row['username'], row['name'], row['role'])
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ─── 대시보드 ─────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def dashboard():
    conn = get_db()
    total_parts = conn.execute("SELECT COUNT(*) as c FROM parts").fetchone()['c']
    total_equipment = conn.execute("SELECT COUNT(*) as c FROM equipment").fetchone()['c']
    low_stock = conn.execute("SELECT COUNT(*) as c FROM parts WHERE quantity <= min_quantity").fetchone()['c']
    today_replacements = conn.execute(
        "SELECT COUNT(*) as c FROM replacement_history WHERE date(replaced_at)=date('now','localtime')"
    ).fetchone()['c']

    recent = conn.execute('''
        SELECT rh.replaced_at, u.name as user_name, e.name as eq_name,
               p.name as part_name, rh.quantity, p.unit
        FROM replacement_history rh
        LEFT JOIN users u ON rh.user_id=u.id
        LEFT JOIN equipment e ON rh.equipment_id=e.id
        LEFT JOIN parts p ON rh.part_id=p.id
        ORDER BY rh.replaced_at DESC LIMIT 10
    ''').fetchall()

    low_parts = conn.execute(
        "SELECT * FROM parts WHERE quantity <= min_quantity ORDER BY quantity ASC LIMIT 10"
    ).fetchall()

    equipment_status = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM equipment GROUP BY status"
    ).fetchall()

    conn.close()
    return render_template('dashboard.html',
                           total_parts=total_parts,
                           total_equipment=total_equipment,
                           low_stock=low_stock,
                           today_replacements=today_replacements,
                           recent=recent,
                           low_parts=low_parts,
                           equipment_status=equipment_status)


# ─── 부품 관리 ────────────────────────────────────────────────────────────────

@app.route('/parts')
@login_required
def parts_list():
    q = request.args.get('q', '')
    category = request.args.get('category', '')
    conn = get_db()
    query = "SELECT * FROM parts WHERE 1=1"
    params = []
    if q:
        query += " AND (name LIKE ? OR code LIKE ? OR supplier LIKE ?)"
        params += [f'%{q}%', f'%{q}%', f'%{q}%']
    if category:
        query += " AND category=?"
        params.append(category)
    query += " ORDER BY category, name"
    parts = conn.execute(query, params).fetchall()
    categories = conn.execute("SELECT DISTINCT category FROM parts WHERE category!='' ORDER BY category").fetchall()
    conn.close()
    return render_template('parts.html', parts=parts, categories=categories, q=q, selected_category=category)


@app.route('/parts/add', methods=['GET', 'POST'])
@login_required
def parts_add():
    if request.method == 'POST':
        code = request.form['code'].strip()
        name = request.form['name'].strip()
        category = request.form.get('category', '').strip()
        quantity = int(request.form.get('quantity', 0))
        min_quantity = int(request.form.get('min_quantity', 5))
        unit = request.form.get('unit', 'EA').strip()
        unit_price = int(request.form.get('unit_price', 0) or 0)
        location = request.form.get('location', '').strip()
        supplier = request.form.get('supplier', '').strip()
        note = request.form.get('note', '').strip()
        conn = get_db()
        try:
            conn.execute('''INSERT INTO parts (code,name,category,quantity,min_quantity,unit,unit_price,location,supplier,note)
                            VALUES (?,?,?,?,?,?,?,?,?,?)''',
                         (code, name, category, quantity, min_quantity, unit, unit_price, location, supplier, note))
            conn.commit()
            flash('부품이 등록되었습니다.', 'success')
            return redirect(url_for('parts_list'))
        except sqlite3.IntegrityError:
            flash('이미 존재하는 부품 코드입니다.', 'danger')
        finally:
            conn.close()
    return render_template('parts_form.html', part=None, title='부품 등록')


@app.route('/parts/<int:part_id>/edit', methods=['GET', 'POST'])
@login_required
def parts_edit(part_id):
    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone()
    if not part:
        conn.close()
        flash('존재하지 않는 부품입니다.', 'danger')
        return redirect(url_for('parts_list'))
    if request.method == 'POST':
        name = request.form['name'].strip()
        category = request.form.get('category', '').strip()
        quantity = int(request.form.get('quantity', 0))
        min_quantity = int(request.form.get('min_quantity', 5))
        unit = request.form.get('unit', 'EA').strip()
        unit_price = int(request.form.get('unit_price', 0) or 0)
        location = request.form.get('location', '').strip()
        supplier = request.form.get('supplier', '').strip()
        note = request.form.get('note', '').strip()
        conn.execute('''UPDATE parts SET name=?,category=?,quantity=?,min_quantity=?,unit=?,
                        unit_price=?,location=?,supplier=?,note=?,
                        updated_at=datetime('now','localtime') WHERE id=?''',
                     (name, category, quantity, min_quantity, unit, unit_price, location, supplier, note, part_id))
        conn.commit()
        conn.close()
        flash('부품 정보가 수정되었습니다.', 'success')
        return redirect(url_for('parts_list'))
    conn.close()
    return render_template('parts_form.html', part=part, title='부품 수정')


@app.route('/parts/<int:part_id>/delete', methods=['POST'])
@login_required
def parts_delete(part_id):
    if current_user.role != 'admin':
        flash('관리자만 삭제할 수 있습니다.', 'danger')
        return redirect(url_for('parts_list'))
    conn = get_db()
    conn.execute("DELETE FROM parts WHERE id=?", (part_id,))
    conn.commit()
    conn.close()
    flash('부품이 삭제되었습니다.', 'success')
    return redirect(url_for('parts_list'))


@app.route('/parts/<int:part_id>/adjust', methods=['POST'])
@login_required
def parts_adjust(part_id):
    action = request.form.get('action')
    amount = int(request.form.get('amount', 0))
    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone()
    if not part:
        conn.close()
        return jsonify({'error': '부품 없음'}), 404
    new_qty = part['quantity'] + amount if action == 'in' else part['quantity'] - amount
    if new_qty < 0:
        conn.close()
        flash('재고가 부족합니다.', 'danger')
        return redirect(url_for('parts_list'))
    conn.execute("UPDATE parts SET quantity=?, updated_at=datetime('now','localtime') WHERE id=?",
                 (new_qty, part_id))
    conn.commit()
    conn.close()
    flash(f"{'입고' if action == 'in' else '출고'} 처리되었습니다. (현재 재고: {new_qty})", 'success')
    return redirect(url_for('parts_list'))


# ─── 장비 관리 ────────────────────────────────────────────────────────────────

@app.route('/equipment')
@login_required
def equipment_list():
    q = request.args.get('q', '')
    conn = get_db()
    query = "SELECT * FROM equipment WHERE 1=1"
    params = []
    if q:
        query += " AND (name LIKE ? OR code LIKE ? OR location LIKE ?)"
        params += [f'%{q}%', f'%{q}%', f'%{q}%']
    query += " ORDER BY code"
    equipment = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('equipment.html', equipment=equipment, q=q)


@app.route('/equipment/add', methods=['GET', 'POST'])
@login_required
def equipment_add():
    if request.method == 'POST':
        code = request.form['code'].strip()
        name = request.form['name'].strip()
        location = request.form.get('location', '').strip()
        status = request.form.get('status', '가동중')
        note = request.form.get('note', '').strip()
        conn = get_db()
        try:
            conn.execute("INSERT INTO equipment (code,name,location,status,note) VALUES (?,?,?,?,?)",
                         (code, name, location, status, note))
            conn.commit()
            flash('장비가 등록되었습니다.', 'success')
            return redirect(url_for('equipment_list'))
        except sqlite3.IntegrityError:
            flash('이미 존재하는 장비 코드입니다.', 'danger')
        finally:
            conn.close()
    return render_template('equipment_form.html', eq=None, title='장비 등록')


@app.route('/equipment/<int:eq_id>/edit', methods=['GET', 'POST'])
@login_required
def equipment_edit(eq_id):
    conn = get_db()
    eq = conn.execute("SELECT * FROM equipment WHERE id=?", (eq_id,)).fetchone()
    if not eq:
        conn.close()
        flash('존재하지 않는 장비입니다.', 'danger')
        return redirect(url_for('equipment_list'))
    if request.method == 'POST':
        name = request.form['name'].strip()
        location = request.form.get('location', '').strip()
        status = request.form.get('status', '가동중')
        note = request.form.get('note', '').strip()
        conn.execute("UPDATE equipment SET name=?,location=?,status=?,note=? WHERE id=?",
                     (name, location, status, note, eq_id))
        conn.commit()
        conn.close()
        flash('장비 정보가 수정되었습니다.', 'success')
        return redirect(url_for('equipment_list'))
    conn.close()
    return render_template('equipment_form.html', eq=eq, title='장비 수정')


@app.route('/equipment/<int:eq_id>/delete', methods=['POST'])
@login_required
def equipment_delete(eq_id):
    if current_user.role != 'admin':
        flash('관리자만 삭제할 수 있습니다.', 'danger')
        return redirect(url_for('equipment_list'))
    conn = get_db()
    conn.execute("DELETE FROM equipment WHERE id=?", (eq_id,))
    conn.commit()
    conn.close()
    flash('장비가 삭제되었습니다.', 'success')
    return redirect(url_for('equipment_list'))


# ─── 교체 이력 ────────────────────────────────────────────────────────────────

@app.route('/history')
@login_required
def history_list():
    page = int(request.args.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    q = request.args.get('q', '')
    conn = get_db()
    base = '''FROM replacement_history rh
              LEFT JOIN users u ON rh.user_id=u.id
              LEFT JOIN equipment e ON rh.equipment_id=e.id
              LEFT JOIN parts p ON rh.part_id=p.id
              WHERE 1=1'''
    params = []
    if q:
        base += " AND (e.name LIKE ? OR p.name LIKE ? OR u.name LIKE ?)"
        params += [f'%{q}%', f'%{q}%', f'%{q}%']
    total = conn.execute(f"SELECT COUNT(*) as c {base}", params).fetchone()['c']
    rows = conn.execute(
        f"SELECT rh.*, u.name as user_name, e.name as eq_name, p.name as part_name, p.unit {base} ORDER BY rh.replaced_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset]
    ).fetchall()
    conn.close()
    total_pages = (total + per_page - 1) // per_page
    return render_template('history.html', rows=rows, page=page, total_pages=total_pages, q=q, total=total)


@app.route('/history/add', methods=['GET', 'POST'])
@login_required
def history_add():
    conn = get_db()
    if request.method == 'POST':
        equipment_id = int(request.form['equipment_id'])
        part_id = int(request.form['part_id'])
        quantity = int(request.form['quantity'])
        reason = request.form.get('reason', '').strip()
        note = request.form.get('note', '').strip()

        part = conn.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone()
        if part['quantity'] < quantity:
            flash(f'재고 부족! 현재 재고: {part["quantity"]} {part["unit"]}', 'danger')
        else:
            conn.execute('''INSERT INTO replacement_history (equipment_id,part_id,quantity,reason,note,user_id)
                            VALUES (?,?,?,?,?,?)''',
                         (equipment_id, part_id, quantity, reason, note, current_user.id))
            conn.execute("UPDATE parts SET quantity=quantity-?, updated_at=datetime('now','localtime') WHERE id=?",
                         (quantity, part_id))
            conn.commit()
            flash('교체 이력이 등록되었습니다.', 'success')
            conn.close()
            return redirect(url_for('history_list'))

    equipment = conn.execute("SELECT * FROM equipment ORDER BY code").fetchall()
    parts = conn.execute("SELECT * FROM parts ORDER BY category, name").fetchall()
    conn.close()
    return render_template('history_form.html', equipment=equipment, parts=parts)


@app.route('/history/<int:hist_id>/delete', methods=['POST'])
@login_required
def history_delete(hist_id):
    if current_user.role != 'admin':
        flash('관리자만 삭제할 수 있습니다.', 'danger')
        return redirect(url_for('history_list'))
    conn = get_db()
    row = conn.execute("SELECT * FROM replacement_history WHERE id=?", (hist_id,)).fetchone()
    if row:
        conn.execute("UPDATE parts SET quantity=quantity+?, updated_at=datetime('now','localtime') WHERE id=?",
                     (row['quantity'], row['part_id']))
        conn.execute("DELETE FROM replacement_history WHERE id=?", (hist_id,))
        conn.commit()
        flash('이력이 삭제되었고 재고가 복구되었습니다.', 'success')
    conn.close()
    return redirect(url_for('history_list'))


# ─── 사용자 관리 (관리자 전용) ────────────────────────────────────────────────

@app.route('/users')
@login_required
def users_list():
    if current_user.role != 'admin':
        flash('관리자만 접근할 수 있습니다.', 'danger')
        return redirect(url_for('dashboard'))
    conn = get_db()
    users = conn.execute("SELECT * FROM users ORDER BY role DESC, name").fetchall()
    conn.close()
    return render_template('users.html', users=users)


@app.route('/users/add', methods=['GET', 'POST'])
@login_required
def users_add():
    if current_user.role != 'admin':
        flash('관리자만 접근할 수 있습니다.', 'danger')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        name = request.form['name'].strip()
        role = request.form.get('role', 'user')
        conn = get_db()
        try:
            conn.execute("INSERT INTO users (username,password,name,role) VALUES (?,?,?,?)",
                         (username, generate_password_hash(password), name, role))
            conn.commit()
            flash('사용자가 등록되었습니다.', 'success')
            return redirect(url_for('users_list'))
        except sqlite3.IntegrityError:
            flash('이미 존재하는 아이디입니다.', 'danger')
        finally:
            conn.close()
    return render_template('user_form.html', user=None, title='사용자 등록')


@app.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def users_edit(user_id):
    if current_user.role != 'admin':
        flash('관리자만 접근할 수 있습니다.', 'danger')
        return redirect(url_for('dashboard'))
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        flash('존재하지 않는 사용자입니다.', 'danger')
        return redirect(url_for('users_list'))
    if request.method == 'POST':
        name = request.form['name'].strip()
        role = request.form.get('role', 'user')
        new_pw = request.form.get('password', '').strip()
        if new_pw:
            conn.execute("UPDATE users SET name=?,role=?,password=? WHERE id=?",
                         (name, role, generate_password_hash(new_pw), user_id))
        else:
            conn.execute("UPDATE users SET name=?,role=? WHERE id=?", (name, role, user_id))
        conn.commit()
        conn.close()
        flash('사용자 정보가 수정되었습니다.', 'success')
        return redirect(url_for('users_list'))
    conn.close()
    return render_template('user_form.html', user=user, title='사용자 수정')


@app.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
def users_delete(user_id):
    if current_user.role != 'admin':
        flash('관리자만 접근할 수 있습니다.', 'danger')
        return redirect(url_for('dashboard'))
    if user_id == current_user.id:
        flash('자기 자신은 삭제할 수 없습니다.', 'danger')
        return redirect(url_for('users_list'))
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    flash('사용자가 삭제되었습니다.', 'success')
    return redirect(url_for('users_list'))


# ─── API (자동완완성, 재고 알림) ──────────────────────────────────────────────

@app.route('/api/parts')
@login_required
def api_parts():
    q = request.args.get('q', '')
    conn = get_db()
    rows = conn.execute("SELECT id, code, name, quantity, unit FROM parts WHERE name LIKE ? OR code LIKE ? LIMIT 20",
                        (f'%{q}%', f'%{q}%')).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/low_stock_count')
@login_required
def api_low_stock():
    conn = get_db()
    cnt = conn.execute("SELECT COUNT(*) as c FROM parts WHERE quantity <= min_quantity").fetchone()['c']
    conn.close()
    return jsonify({'count': cnt})


if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
