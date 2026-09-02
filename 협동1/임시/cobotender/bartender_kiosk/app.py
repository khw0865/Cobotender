from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3
from pathlib import Path
from datetime import datetime, date

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'database' / 'bar.db'

app = Flask(__name__)
app.secret_key = 'bartender-kiosk-dev-secret'

COCKTAILS = [
    ('cocktail','Old Fashioned',9000,"Maker's Mark, 설탕, 비터, 오렌지 필을 혼합한 클래식 칵테일",'cocktail_old_fashioned.jpg',0,0,0),
    ('cocktail','Mojito',8500,'Jameson, 라임, 민트, 설탕, 탄산수를 섞은 청량한 위스키 모히토','cocktail_mojito.jpg',0,0,0),
    ('cocktail','Whisky Sour',9500,'Johnnie Walker Black, 레몬 주스, 설탕 시럽을 섞은 산뜻한 칵테일','cocktail_sour.jpg',0,0,0),
]
WHISKIES = [
    ('straight','Macallan 12',14000,'40% · 쉐리향이 진하고 부드러운 피니시','whisky_macallan12.jpg',40,30,700),
    ('straight','Glenfiddich 12',11000,'40% · 배와 사과향이 산뜻한 싱글몰트','whisky_glenfiddich12.jpg',40,30,700),
    ('straight','Jameson',8000,'40% · 부드럽고 가벼운 아이리시 위스키','whisky_jameson.jpg',40,30,700),
    ('straight','Maker\'s Mark',10000,'45% · 바닐라와 캐러멜 향이 강한 버번','whisky_makers.jpg',45,30,750),
    ('straight','Ballantine\'s 17',13000,'40% · 균형 잡힌 블렌디드 위스키','whisky_ballantines17.jpg',40,30,700),
    ('straight','Johnnie Walker Black',9000,'40% · 스모키하고 묵직한 블렌디드 위스키','whisky_black.jpg',40,30,700),
]
SNACKS = [
    ('snack','치즈 플래터',12000,'위스키와 잘 어울리는 치즈와 견과 구성','snack_cheese.jpg',0,0,0),
    ('snack','감자튀김',7000,'바삭한 감자튀김과 케첩','snack_fries.jpg',0,0,0),
    ('snack','나초',8000,'나초칩, 살사, 치즈소스 구성','snack_nacho.jpg',0,0,0),
]
REQUESTS = [
    ('request','물',0,'직원에게 물 요청','request_water.jpg',0,0,0),
    ('request','냅킨',0,'직원에게 냅킨 요청','request_napkin.jpg',0,0,0),
    ('request','직원호출',0,'관리자 화면에 직원 호출 알림 전송','request_staff.jpg',0,0,0),
]


# 칵테일은 실제 로봇 제조 시 스트레이트 위스키 재고를 재료로 사용한다고 가정합니다.
# 값은 "칵테일 1잔을 만들 때 차감할 위스키 ml"입니다.
COCKTAIL_RECIPES = {
    'Old Fashioned': [
        {'name': "Maker's Mark", 'ml': 45},
    ],
    'Mojito': [
        {'name': 'Jameson', 'ml': 30},
    ],
    'Whisky Sour': [
        {'name': 'Johnnie Walker Black', 'ml': 45},
    ],
}

def get_cocktail_shortage(cur, cocktail_name, qty=1):
    """칵테일 제조에 필요한 위스키 재고 부족 여부를 반환합니다."""
    recipe = COCKTAIL_RECIPES.get(cocktail_name, [])
    for ing in recipe:
        row = cur.execute('SELECT stock_ml FROM menu WHERE name=? AND category=?', (ing['name'], 'straight')).fetchone()
        required_ml = ing['ml'] * qty
        if row is None or row['stock_ml'] < required_ml:
            return ing['name']
    return None



def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = db()
    cur = con.cursor()
    cur.executescript('''
    CREATE TABLE IF NOT EXISTS menu(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        name TEXT UNIQUE NOT NULL,
        price INTEGER NOT NULL,
        description TEXT,
        image TEXT,
        alcohol REAL DEFAULT 0,
        serving_ml INTEGER DEFAULT 0,
        bottle_ml INTEGER DEFAULT 0,
        stock_ml INTEGER DEFAULT 0,
        sold_out INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE NOT NULL,
        created_at TEXT NOT NULL,
        total_price INTEGER NOT NULL,
        status TEXT DEFAULT 'completed'
    );
    CREATE TABLE IF NOT EXISTS order_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        menu_id INTEGER,
        menu_name TEXT NOT NULL,
        category TEXT NOT NULL,
        qty INTEGER NOT NULL,
        unit_price INTEGER NOT NULL,
        line_total INTEGER NOT NULL,
        FOREIGN KEY(order_id) REFERENCES orders(id)
    );
    CREATE TABLE IF NOT EXISTS staff_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_type TEXT NOT NULL,
        created_at TEXT NOT NULL,
        handled INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS robot_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        message TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS robot_state(
        id INTEGER PRIMARY KEY CHECK(id=1),
        power INTEGER DEFAULT 0,
        busy INTEGER DEFAULT 0,
        emergency INTEGER DEFAULT 0,
        error INTEGER DEFAULT 0
    );
    ''')
    cur.execute('INSERT OR IGNORE INTO robot_state(id,power,busy,emergency,error) VALUES(1,0,0,0,0)')
    if cur.execute('SELECT COUNT(*) FROM menu').fetchone()[0] == 0:
        for item in COCKTAILS + WHISKIES + SNACKS + REQUESTS:
            cur.execute('''INSERT INTO menu(category,name,price,description,image,alcohol,serving_ml,bottle_ml,stock_ml)
                           VALUES(?,?,?,?,?,?,?,?,?)''', item[:8] + (item[7] * 3 if item[7] else 999999,))
    con.commit()
    con.close()


def add_log(message):
    con = db()
    con.execute('INSERT INTO robot_logs(created_at,message) VALUES(?,?)', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), message))
    con.commit(); con.close()

@app.route('/')
def index():
    return redirect('/customer')

@app.route('/customer')
def customer():
    return render_template('customer.html')

@app.route('/admin', methods=['GET','POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        if request.form.get('username') == 'admin' and request.form.get('password') == 'admin':
            session['admin'] = True
            return redirect('/admin/dashboard')
        error = '아이디 또는 비밀번호를 확인하세요'
    return render_template('admin_login.html', error=error)

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin'):
        return redirect('/admin')
    return render_template('admin.html')

@app.route('/logout')
def logout():
    session.clear(); return redirect('/admin')



@app.route('/admin/inventory')
def admin_inventory_page():
    if not session.get('admin'):
        return redirect('/admin')
    return render_template('inventory.html')

@app.route('/admin/orders')
def admin_orders_page():
    if not session.get('admin'):
        return redirect('/admin')
    return render_template('orders.html')

@app.get('/api/menu')
def api_menu():
    con = db()
    rows = [dict(r) for r in con.execute('SELECT * FROM menu ORDER BY category,id')]
    con.close()
    con = db()
    cur = con.cursor()
    for r in rows:
        if r['category'] == 'straight':
            r['sold_out'] = 1 if r['stock_ml'] < r['serving_ml'] else 0
        elif r['category'] == 'cocktail':
            r['sold_out'] = 1 if get_cocktail_shortage(cur, r['name'], 1) else 0
        else:
            r['sold_out'] = 0
    con.close()
    return jsonify(rows)

@app.post('/api/order')
def api_order():
    data = request.get_json(force=True)
    items = data.get('items', [])
    if not items:
        return jsonify({'ok': False, 'message': '장바구니가 비어있습니다.'}), 400
    con = db(); cur = con.cursor()
    menu_by_id = {r['id']: dict(r) for r in cur.execute('SELECT * FROM menu')}
    total = 0
    checked = []
    for it in items:
        mid, qty = int(it['id']), int(it['qty'])
        m = menu_by_id.get(mid)
        if not m or qty <= 0:
            continue
        if m['category'] == 'straight' and m['stock_ml'] < m['serving_ml'] * qty:
            con.close(); return jsonify({'ok': False, 'message': f'{m["name"]} 재고가 부족합니다.'}), 409
        if m['category'] == 'cocktail':
            shortage = get_cocktail_shortage(cur, m['name'], qty)
            if shortage:
                con.close(); return jsonify({'ok': False, 'message': f'{m["name"]} 제조에 필요한 {shortage} 재고가 부족합니다.'}), 409
        checked.append((m, qty)); total += m['price'] * qty
    order_number = 'A' + datetime.now().strftime('%Y%m%d%H%M%S')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur.execute('INSERT INTO orders(order_number,created_at,total_price,status) VALUES(?,?,?,?)', (order_number, now, total, 'completed'))
    order_id = cur.lastrowid
    for m, qty in checked:
        cur.execute('''INSERT INTO order_items(order_id,menu_id,menu_name,category,qty,unit_price,line_total)
                       VALUES(?,?,?,?,?,?,?)''', (order_id, m['id'], m['name'], m['category'], qty, m['price'], m['price']*qty))
        if m['category'] == 'straight':
            cur.execute('UPDATE menu SET stock_ml = MAX(stock_ml - ?, 0) WHERE id=?', (m['serving_ml']*qty, m['id']))
        if m['category'] == 'cocktail':
            for ing in COCKTAIL_RECIPES.get(m['name'], []):
                cur.execute('UPDATE menu SET stock_ml = MAX(stock_ml - ?, 0) WHERE name=? AND category=?', (ing['ml']*qty, ing['name'], 'straight'))
        if m['category'] == 'request':
            cur.execute('INSERT INTO staff_requests(request_type,created_at) VALUES(?,?)', (m['name'], now))
    con.commit(); con.close()
    return jsonify({'ok': True, 'order_number': order_number, 'total': total})

@app.get('/api/inventory')
def api_inventory():
    con = db()
    rows = [dict(r) for r in con.execute("SELECT * FROM menu WHERE category='straight' ORDER BY id")]
    con.close(); return jsonify(rows)

@app.post('/api/inventory')
def api_inventory_update():
    data = request.get_json(force=True)
    con = db(); cur = con.cursor()
    for row in data.get('items', []):
        mid = int(row['id']); bottles = max(0, int(row['bottles']))
        bottle_ml = cur.execute('SELECT bottle_ml FROM menu WHERE id=?', (mid,)).fetchone()['bottle_ml']
        cur.execute('UPDATE menu SET stock_ml=? WHERE id=?', (bottles * bottle_ml, mid))
    con.commit(); con.close(); add_log('재고 정보가 수정되었습니다.')
    return jsonify({'ok': True})

@app.get('/api/orders')
def api_orders():
    con = db(); cur = con.cursor()
    orders = []
    for o in cur.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 200'):
        items = [dict(i) for i in con.execute('SELECT menu_name,qty,line_total FROM order_items WHERE order_id=?', (o['id'],))]
        d = dict(o); d['items'] = items; orders.append(d)
    today = date.today().strftime('%Y-%m-%d')
    total_today = cur.execute("SELECT COALESCE(SUM(total_price),0) FROM orders WHERE substr(created_at,1,10)=?", (today,)).fetchone()[0]
    con.close(); return jsonify({'orders': orders, 'total_today': total_today})

@app.get('/api/staff_requests')
def api_staff_requests():
    con = db()
    rows = [dict(r) for r in con.execute('SELECT * FROM staff_requests WHERE handled=0 ORDER BY id')]
    con.close(); return jsonify(rows)

@app.post('/api/staff_requests/<int:req_id>/handle')
def api_staff_handle(req_id):
    con = db(); con.execute('UPDATE staff_requests SET handled=1 WHERE id=?', (req_id,)); con.commit(); con.close()
    return jsonify({'ok': True})

@app.get('/api/robot')
def api_robot_state():
    con = db(); state = dict(con.execute('SELECT * FROM robot_state WHERE id=1').fetchone())
    logs = [dict(r) for r in con.execute('SELECT * FROM robot_logs ORDER BY id DESC LIMIT 30')]
    con.close(); return jsonify({'state': state, 'logs': logs})

@app.post('/api/robot/action')
def api_robot_action():
    action = request.get_json(force=True).get('action')
    con = db(); cur = con.cursor(); state = dict(cur.execute('SELECT * FROM robot_state WHERE id=1').fetchone())
    allow_when_busy = {'emergency_stop'}
    if state['busy'] and action not in allow_when_busy:
        con.close(); return jsonify({'ok': False, 'message': '로봇 동작 중에는 조작할 수 없습니다.'}), 409
    msg = ''
    if action == 'power_on': cur.execute('UPDATE robot_state SET power=1,error=0 WHERE id=1'); msg='로봇팔 ON'
    elif action == 'power_off': cur.execute('UPDATE robot_state SET power=0,busy=0 WHERE id=1'); msg='로봇팔 OFF'
    elif action == 'emergency_stop': cur.execute('UPDATE robot_state SET busy=0,emergency=1 WHERE id=1'); msg='비상정지 실행'
    elif action == 'reset_error': cur.execute('UPDATE robot_state SET busy=0,emergency=0,error=0 WHERE id=1'); msg='오류/비상정지 해제'
    elif action in ['home','grip','ungrip','joint_move']:
        cur.execute('UPDATE robot_state SET busy=1 WHERE id=1'); msg=f'{action} 명령 실행 대기'
    else:
        con.close(); return jsonify({'ok': False, 'message': '알 수 없는 명령입니다.'}), 400
    cur.execute('INSERT INTO robot_logs(created_at,message) VALUES(?,?)', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg))
    con.commit(); con.close(); return jsonify({'ok': True, 'message': msg})

@app.post('/api/robot/finish')
def api_robot_finish():
    con = db(); con.execute('UPDATE robot_state SET busy=0 WHERE id=1'); con.commit(); con.close()
    add_log('동작 완료 처리')
    return jsonify({'ok': True})

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
