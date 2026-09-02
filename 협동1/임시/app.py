from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3
from pathlib import Path
from datetime import datetime, date
import math
import threading
import time

try:
    import psutil
except Exception:
    psutil = None

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.executors import MultiThreadedExecutor
    from dsr_msgs2.msg import RobotState
    from dsr_msgs2.srv import MoveStop, ServoOff, SetRobotControl, SetSafeStopResetType
    from dsr_msgs2.srv import Robotiq2FOpen, Robotiq2FClose
    ROS_AVAILABLE = True
except Exception as exc:
    rclpy = None
    Node = object
    MultiThreadedExecutor = None
    RobotState = None
    MoveStop = ServoOff = SetRobotControl = SetSafeStopResetType = None
    Robotiq2FOpen = Robotiq2FClose = None
    ROS_AVAILABLE = False
    ROS_IMPORT_ERROR = str(exc)
else:
    ROS_IMPORT_ERROR = ''

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




# =============================
# Doosan ROS2 Direct Bridge
# =============================
# Flask와 별도의 ROS2 제어 노드를 두지 않고, app.py 내부에서 rclpy Node를 실행합니다.
# MultiThreadedExecutor를 별도 daemon thread에서 spin하여 Flask 요청 처리와 ROS2 callback/service 처리를 분리합니다.

class DoosanRosBridge(Node if ROS_AVAILABLE else object):
    def __init__(self):
        if not ROS_AVAILABLE:
            self.available = False
            self.import_error = ROS_IMPORT_ERROR
            self.last_state_time = 0.0
            self.joints = [0.0] * 6
            self.joint_velocity = [0.0] * 6
            self.tcp_velocity = [0.0] * 6
            self.joint_temperature = [0.0] * 6
            self.robot_mode_raw = None
            self.robot_state_raw = None
            self.logs = ['ROS2 import failed: ' + self.import_error]
            return

        super().__init__('bartender_admin_ui_bridge')
        self.available = True
        self.import_error = ''
        self.last_state_time = 0.0
        self.joints = [0.0] * 6
        self.joint_velocity = [0.0] * 6
        self.tcp_velocity = [0.0] * 6
        self.joint_temperature = [0.0] * 6
        self.robot_mode_raw = None
        self.robot_state_raw = None
        self.logs = []

        # 실제 토픽 이름은 dsr_bringup2 실행 시 name/namespace 설정에 따라 달라질 수 있습니다.
        # 기본값은 /dsr01/state 로 맞췄습니다.
        self.state_topic = '/dsr01/state'
        self.create_subscription(RobotState, self.state_topic, self._state_callback, 10)

        self.clients = {
            'robot_on': self.create_client(SetRobotControl, '/dsr01/system/set_robot_control'),
            'robot_off': self.create_client(ServoOff, '/dsr01/system/servo_off'),
            'estop': self.create_client(MoveStop, '/dsr01/motion/move_stop'),
            'estop_reset': self.create_client(SetSafeStopResetType, '/dsr01/system/set_safe_stop_reset_type'),
            'grip': self.create_client(Robotiq2FClose, '/dsr01/gripper/robotiq_2f_close'),
            'ungrip': self.create_client(Robotiq2FOpen, '/dsr01/gripper/robotiq_2f_open'),
        }
        self._log('ROS2 bridge started. Subscribing ' + self.state_topic)

    def _log(self, message):
        stamp = datetime.now().strftime('%H:%M:%S')
        self.logs.insert(0, f'[{stamp}] {message}')
        self.logs = self.logs[:80]

    def _state_callback(self, msg):
        self.last_state_time = time.time()
        self.joints = [float(v) for v in getattr(msg, 'actual_joint_position', [0.0] * 6)]
        self.joint_velocity = [float(v) for v in getattr(msg, 'actual_joint_velocity', [0.0] * 6)]
        self.tcp_velocity = [float(v) for v in getattr(msg, 'actual_tcp_velocity', [0.0] * 6)]
        self.joint_temperature = [float(v) for v in getattr(msg, 'joint_temperature', [0.0] * 6)]
        self.robot_mode_raw = getattr(msg, 'robot_mode', None)
        self.robot_state_raw = getattr(msg, 'robot_state', None)

    def _set_request_field(self, request, candidates, value):
        for name in candidates:
            if hasattr(request, name):
                setattr(request, name, value)
                return True
        return False

    def _call_service(self, key, configure=None, timeout=1.0):
        if not self.available:
            return False, 'ROS2 사용 불가: ' + self.import_error

        client = self.clients.get(key)
        if client is None:
            return False, f'{key} client가 설정되지 않았습니다.'

        if not client.wait_for_service(timeout_sec=timeout):
            return False, f'{client.srv_name} 서비스를 찾을 수 없습니다.'

        request = client.srv_type.Request()
        if configure:
            configure(request)

        future = client.call_async(request)
        future.add_done_callback(lambda f: self._service_done(key, f))
        return True, f'{key} 명령을 로봇에 전송했습니다.'

    def _service_done(self, key, future):
        try:
            result = future.result()
            self._log(f'{key} service response: {result}')
        except Exception as exc:
            self._log(f'{key} service failed: {exc}')

    def command(self, command):
        if command == 'robot_on':
            def cfg(req):
                # Doosan SetRobotControl 서비스의 필드명은 설치 버전에 따라 다를 수 있어 후보 필드를 모두 처리합니다.
                self._set_request_field(req, ['robot_control', 'control', 'control_mode', 'mode'], 1)
            return self._call_service('robot_on', cfg)

        if command == 'robot_off':
            return self._call_service('robot_off')

        if command == 'grip':
            return self._call_service('grip')

        if command == 'ungrip':
            return self._call_service('ungrip')

        if command == 'estop':
            def cfg(req):
                # 일반적으로 STOP_TYPE_QUICK 또는 DR_SSTOP에 해당하는 정수값을 사용합니다.
                self._set_request_field(req, ['stop_mode', 'mode', 'stop_type'], 1)
            return self._call_service('estop', cfg, timeout=0.3)

        if command == 'estop_reset':
            def cfg(req):
                self._set_request_field(req, ['reset_type', 'type', 'mode'], 0)
            return self._call_service('estop_reset', cfg)

        return False, '알 수 없는 명령입니다.'

    def mode_text(self):
        # Doosan RobotState.robot_state: standby/moving/safe_stop/emergency_stop 등의 숫자 상태를 UI용 문자열로 변환
        state = self.robot_state_raw
        if state == 2:
            return 'AUTO'
        if state in (5, 9):
            return 'ERROR'
        if state == 6:
            return 'ESTOP'
        if state is None:
            return 'IDLE'
        return 'IDLE'

    def linear_speed(self):
        if len(self.tcp_velocity) >= 3:
            return round(math.sqrt(sum(v * v for v in self.tcp_velocity[:3])), 1)
        return 0

    def angular_speed(self):
        if len(self.tcp_velocity) >= 6:
            return round(math.sqrt(sum(v * v for v in self.tcp_velocity[3:6])), 1)
        return 0

    def resource_status(self):
        cpu = psutil.cpu_percent(interval=None) if psutil else 0
        mem = psutil.virtual_memory().percent if psutil else 0
        if self.joint_temperature and any(self.joint_temperature):
            temp = round(sum(self.joint_temperature) / len(self.joint_temperature), 1)
        else:
            temp = 0
            if psutil:
                try:
                    temps = psutil.sensors_temperatures()
                    vals = [t.current for group in temps.values() for t in group if getattr(t, 'current', None) is not None]
                    if vals:
                        temp = round(sum(vals) / len(vals), 1)
                except Exception:
                    temp = 0
        return {'cpu': cpu, 'memory': mem, 'temperature': temp}

    def status_payload(self):
        ros_connected = self.available and (time.time() - self.last_state_time < 3.0)
        return {
            'mode': self.mode_text() if ros_connected else 'ERROR',
            'joints': self.joints,
            'recipe': '대기 중',
            'step': 'Ready',
            'connections': {
                'ros': ros_connected,
                'mcu': ros_connected,
                'plc': ros_connected,
            },
            'speed': {
                'linear': self.linear_speed(),
                'angular': self.angular_speed(),
            },
            'resource': self.resource_status(),
            'taskIndex': 0,
            'logs': self.logs,
            'ros_available': self.available,
            'ros_import_error': self.import_error,
            'robot_state_raw': self.robot_state_raw,
            'robot_mode_raw': self.robot_mode_raw,
        }

robot_bridge = None
ros_executor = None
ros_thread = None


def start_ros_bridge():
    global robot_bridge, ros_executor, ros_thread
    if robot_bridge is not None:
        return

    robot_bridge = DoosanRosBridge()
    if not ROS_AVAILABLE:
        return

    ros_executor = MultiThreadedExecutor(num_threads=2)
    ros_executor.add_node(robot_bridge)
    ros_thread = threading.Thread(target=ros_executor.spin, daemon=True)
    ros_thread.start()


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

@app.route('/admin/inventory')
def admin_inventory():
    if not session.get('admin'):
        return redirect('/admin')
    return render_template('inventory.html')

@app.route('/admin/orders')
def admin_orders():
    if not session.get('admin'):
        return redirect('/admin')
    return render_template('orders.html')

@app.route('/logout')
def logout():
    session.clear(); return redirect('/admin')

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


@app.get('/api/robot/status')
def api_robot_status_direct():
    if robot_bridge is None:
        return jsonify({'mode': 'ERROR', 'joints': [0,0,0,0,0,0], 'connections': {'ros': False, 'mcu': False, 'plc': False}, 'speed': {'linear': 0, 'angular': 0}, 'resource': {'cpu': 0, 'memory': 0, 'temperature': 0}, 'taskIndex': 0, 'logs': ['ROS bridge not started']})
    return jsonify(robot_bridge.status_payload())

@app.post('/api/robot/command')
def api_robot_command_direct():
    command = request.get_json(force=True).get('command')
    if robot_bridge is None:
        return jsonify({'ok': False, 'message': 'ROS bridge가 시작되지 않았습니다.'}), 503
    ok, message = robot_bridge.command(command)
    add_log(message)
    return jsonify({'ok': ok, 'message': message}), (200 if ok else 503)

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
    start_ros_bridge()
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
