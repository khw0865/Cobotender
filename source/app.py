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
    from sensor_msgs.msg import JointState
    ROS_CORE_AVAILABLE = True
except Exception as exc:
    rclpy = None
    Node = object
    MultiThreadedExecutor = None
    JointState = None
    ROS_CORE_AVAILABLE = False
    ROS_CORE_IMPORT_ERROR = str(exc)
else:
    ROS_CORE_IMPORT_ERROR = ''

try:
    from dsr_msgs2.srv import MoveStop, ServoOff, SetRobotControl, SetSafeStopResetType
    DSR_SERVICE_AVAILABLE = True
except Exception as exc:
    MoveStop = ServoOff = SetRobotControl = SetSafeStopResetType = None
    DSR_SERVICE_AVAILABLE = False
    DSR_SERVICE_IMPORT_ERROR = str(exc)
else:
    DSR_SERVICE_IMPORT_ERROR = ''

try:
    from dsr_msgs2.srv import SetToolDigitalOutput
    TOOL_IO_AVAILABLE = True
except Exception:
    SetToolDigitalOutput = None
    TOOL_IO_AVAILABLE = False


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'database' / 'bar.db'

app = Flask(__name__)
app.secret_key = 'bartender-kiosk-dev-secret'


COCKTAILS = [
    ('cocktail', 'Old Fashioned', 9000, "Maker's Mark, 설탕, 비터, 오렌지 필을 혼합한 클래식 칵테일", 'cocktail_old_fashioned.jpg', 0, 0, 0),
    ('cocktail', 'Mojito', 8500, 'Jameson, 라임, 민트, 설탕, 탄산수를 섞은 청량한 위스키 모히토', 'cocktail_mojito.jpg', 0, 0, 0),
    ('cocktail', 'Whisky Sour', 9500, 'Johnnie Walker Black, 레몬 주스, 설탕 시럽을 섞은 산뜻한 칵테일', 'cocktail_sour.jpg', 0, 0, 0),
]

WHISKIES = [
    ('straight', 'Macallan 12', 14000, '40% · 쉐리향이 진하고 부드러운 피니시', 'whisky_macallan12.jpg', 40, 30, 700),
    ('straight', 'Glenfiddich 12', 11000, '40% · 배와 사과향이 산뜻한 싱글몰트', 'whisky_glenfiddich12.jpg', 40, 30, 700),
    ('straight', 'Jameson', 8000, '40% · 부드럽고 가벼운 아이리시 위스키', 'whisky_jameson.jpg', 40, 30, 700),
    ('straight', "Maker's Mark", 10000, '45% · 바닐라와 캐러멜 향이 강한 버번', 'whisky_makers.jpg', 45, 30, 750),
    ('straight', "Ballantine's 17", 13000, '40% · 균형 잡힌 블렌디드 위스키', 'whisky_ballantines17.jpg', 40, 30, 700),
    ('straight', 'Johnnie Walker Black', 9000, '40% · 스모키하고 묵직한 블렌디드 위스키', 'whisky_black.jpg', 40, 30, 700),
]

SNACKS = [
    ('snack', '치즈 플래터', 12000, '위스키와 잘 어울리는 치즈와 견과 구성', 'snack_cheese.jpg', 0, 0, 0),
    ('snack', '감자튀김', 7000, '바삭한 감자튀김과 케첩', 'snack_fries.jpg', 0, 0, 0),
    ('snack', '나초', 8000, '나초칩, 살사, 치즈소스 구성', 'snack_nacho.jpg', 0, 0, 0),
]

REQUESTS = [
    ('request', '물', 0, '직원에게 물 요청', 'request_water.jpg', 0, 0, 0),
    ('request', '냅킨', 0, '직원에게 냅킨 요청', 'request_napkin.jpg', 0, 0, 0),
    ('request', '직원호출', 0, '관리자 화면에 직원 호출 알림 전송', 'request_staff.jpg', 0, 0, 0),
]

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
    recipe = COCKTAIL_RECIPES.get(cocktail_name, [])
    for ing in recipe:
        row = cur.execute(
            'SELECT stock_ml FROM menu WHERE name=? AND category=?',
            (ing['name'], 'straight')
        ).fetchone()
        required_ml = ing['ml'] * qty
        if row is None or row['stock_ml'] < required_ml:
            return ing['name']
    return None


# =============================
# ROS2 Direct Bridge
# =============================

class DoosanRosBridge(Node if ROS_CORE_AVAILABLE else object):
    def __init__(self):
        if not ROS_CORE_AVAILABLE:
            self.available = False
            self.core_import_error = ROS_CORE_IMPORT_ERROR
            self.dsr_import_error = DSR_SERVICE_IMPORT_ERROR
            self.last_joint_time = 0.0
            self.joints = [0.0] * 6
            self.velocities = [0.0] * 6
            self.command_logs = ['ROS2 import failed: ' + self.core_import_error]
            self.last_mode = 'ERROR'
            return

        super().__init__('bartender_admin_ui_bridge')

        self.available = True
        self.core_import_error = ''
        self.dsr_import_error = DSR_SERVICE_IMPORT_ERROR if not DSR_SERVICE_AVAILABLE else ''
        self.last_joint_time = 0.0
        self.joints = [0.0] * 6
        self.velocities = [0.0] * 6
        self.command_logs = []
        self.last_mode = 'IDLE'
        self.last_command = 'Ready'
        self.lock = threading.Lock()

        self.joint_topic = '/dsr01/joint_states'
        self.create_subscription(JointState, self.joint_topic, self._joint_callback, 10)

        self.service_clients = {}
        if DSR_SERVICE_AVAILABLE:
            self.service_clients['robot_on'] = self.create_client(SetRobotControl, '/dsr01/system/set_robot_control')
            self.service_clients['robot_off'] = self.create_client(ServoOff, '/dsr01/system/servo_off')
            self.service_clients['estop'] = self.create_client(MoveStop, '/dsr01/motion/move_stop')
            self.service_clients['estop_reset'] = self.create_client(SetSafeStopResetType, '/dsr01/system/set_safe_stop_reset_type')

        if TOOL_IO_AVAILABLE:
            self.service_clients['grip'] = self.create_client(SetToolDigitalOutput, '/dsr01/io/set_tool_digital_output')
            self.service_clients['ungrip'] = self.create_client(SetToolDigitalOutput, '/dsr01/io/set_tool_digital_output')

        self._log('ROS2 bridge started. Subscribing ' + self.joint_topic)
        if not DSR_SERVICE_AVAILABLE:
            self._log('DSR service import failed: ' + self.dsr_import_error)
        if not TOOL_IO_AVAILABLE:
            self._log('Tool digital output service type not available. grip/ungrip need mapping check.')

    def _log(self, message):
        stamp = datetime.now().strftime('%H:%M:%S')
        with getattr(self, 'lock', threading.Lock()):
            self.command_logs.insert(0, f'[{stamp}] {message}')
            self.command_logs = self.command_logs[:80]

    def _joint_callback(self, msg):
        positions = list(msg.position)
        velocities = list(msg.velocity)

        # JointState position can be radian in many ROS2 systems.
        # If all values look like radian values, convert to degree for UI display.
        if positions and max(abs(v) for v in positions[:6]) <= 6.5:
            positions = [math.degrees(v) for v in positions]

        with self.lock:
            self.last_joint_time = time.time()
            self.joints = [float(v) for v in positions[:6]] + [0.0] * max(0, 6 - len(positions))
            self.velocities = [float(v) for v in velocities[:6]] + [0.0] * max(0, 6 - len(velocities))

    def _set_field(self, request_obj, names, value):
        for name in names:
            if hasattr(request_obj, name):
                setattr(request_obj, name, value)
                return True
        return False

    def _call_service(self, key, configure=None, timeout=1.0):
        if not self.available:
            return False, 'ROS2 사용 불가: ' + self.core_import_error

        client = self.service_clients.get(key)
        if client is None:
            return False, f'{key} 서비스 클라이언트가 설정되지 않았습니다.'

        if not client.wait_for_service(timeout_sec=timeout):
            return False, f'{client.srv_name} 서비스를 찾을 수 없습니다.'

        req = client.srv_type.Request()
        if configure:
            configure(req)

        future = client.call_async(req)
        future.add_done_callback(lambda f: self._service_done(key, f))

        self.last_command = key
        if key == 'estop':
            self.last_mode = 'ESTOP'
        elif key in ('robot_on', 'estop_reset'):
            self.last_mode = 'IDLE'
        return True, f'{key} 명령을 로봇에 전송했습니다.'

    def _service_done(self, key, future):
        try:
            result = future.result()
            self._log(f'{key} response: {result}')
        except Exception as exc:
            self._log(f'{key} failed: {exc}')
            self.last_mode = 'ERROR'

    def command(self, command):
        if command == 'robot_on':
            def cfg(req):
                self._set_field(req, ['robot_control', 'control', 'control_mode', 'mode'], 1)
            return self._call_service('robot_on', cfg)

        if command == 'robot_off':
            return self._call_service('robot_off')

        if command == 'estop':
            def cfg(req):
                self._set_field(req, ['stop_mode', 'mode', 'stop_type'], 1)
            return self._call_service('estop', cfg, timeout=0.3)

        if command == 'estop_reset':
            def cfg(req):
                self._set_field(req, ['reset_type', 'type', 'mode'], 0)
            return self._call_service('estop_reset', cfg)

        if command == 'grip':
            def cfg(req):
                self._set_field(req, ['index', 'digital_output', 'channel', 'pin'], 1)
                self._set_field(req, ['value', 'val', 'output', 'state'], 1)
            return self._call_service('grip', cfg)

        if command == 'ungrip':
            def cfg(req):
                self._set_field(req, ['index', 'digital_output', 'channel', 'pin'], 1)
                self._set_field(req, ['value', 'val', 'output', 'state'], 0)
            return self._call_service('ungrip', cfg)

        return False, '알 수 없는 명령입니다.'

    def resource_status(self):
        cpu = psutil.cpu_percent(interval=None) if psutil else 0
        memory = psutil.virtual_memory().percent if psutil else 0
        temp = 0

        if psutil:
            try:
                temps = psutil.sensors_temperatures()
                values = [t.current for group in temps.values() for t in group if getattr(t, 'current', None) is not None]
                if values:
                    temp = round(sum(values) / len(values), 1)
            except Exception:
                temp = 0

        return {
            'cpu': cpu,
            'memory': memory,
            'temperature': temp,
        }

    def status_payload(self):
        with self.lock:
            joints = list(self.joints)
            velocities = list(self.velocities)
            logs = list(self.command_logs)
            last_joint_time = self.last_joint_time
            mode = self.last_mode

        ros_connected = self.available and (time.time() - last_joint_time < 3.0)
        if not ros_connected:
            mode = 'ERROR'

        linear_speed = 0
        angular_speed = round(max((abs(v) for v in velocities), default=0), 2)

        return {
            'mode': mode,
            'joints': joints,
            'recipe': '대기 중',
            'step': self.last_command,
            'connections': {
                'ros': ros_connected,
                'mcu': ros_connected,
                'plc': ros_connected,
            },
            'speed': {
                'linear': linear_speed,
                'angular': angular_speed,
            },
            'resource': self.resource_status(),
            'taskIndex': 0,
            'logs': logs,
            'ros_available': self.available,
            'ros_core_import_error': self.core_import_error,
            'dsr_service_import_error': self.dsr_import_error,
        }


robot_bridge = None
ros_executor = None
ros_thread = None


def start_ros_bridge():
    global robot_bridge, ros_executor, ros_thread

    if robot_bridge is not None:
        return

    if ROS_CORE_AVAILABLE and not rclpy.ok():
        rclpy.init()

    robot_bridge = DoosanRosBridge()

    if not ROS_CORE_AVAILABLE:
        return

    ros_executor = MultiThreadedExecutor(num_threads=2)
    ros_executor.add_node(robot_bridge)
    ros_thread = threading.Thread(target=ros_executor.spin, daemon=True)
    ros_thread.start()


# =============================
# Database
# =============================

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
    ''')

    if cur.execute('SELECT COUNT(*) FROM menu').fetchone()[0] == 0:
        for item in COCKTAILS + WHISKIES + SNACKS + REQUESTS:
            category, name, price, desc, image, alcohol, serving_ml, bottle_ml = item
            stock_ml = bottle_ml * 3 if bottle_ml else 999999
            cur.execute('''
                INSERT INTO menu(category,name,price,description,image,alcohol,serving_ml,bottle_ml,stock_ml)
                VALUES(?,?,?,?,?,?,?,?,?)
            ''', (category, name, price, desc, image, alcohol, serving_ml, bottle_ml, stock_ml))

    con.commit()
    con.close()


def add_log(message):
    con = db()
    con.execute(
        'INSERT INTO robot_logs(created_at,message) VALUES(?,?)',
        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), message)
    )
    con.commit()
    con.close()


# =============================
# Page routes
# =============================

@app.route('/')
def index():
    return redirect('/customer')


@app.route('/customer')
def customer():
    return render_template('customer.html')


@app.route('/admin', methods=['GET', 'POST'])
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
    session.clear()
    return redirect('/admin')


# =============================
# Customer / menu APIs
# =============================

@app.get('/api/menu')
def api_menu():
    con = db()
    rows = [dict(r) for r in con.execute('SELECT * FROM menu ORDER BY category,id')]
    cur = con.cursor()

    for row in rows:
        if row['category'] == 'straight':
            row['sold_out'] = 1 if row['stock_ml'] < row['serving_ml'] else 0
        elif row['category'] == 'cocktail':
            row['sold_out'] = 1 if get_cocktail_shortage(cur, row['name'], 1) else 0
        else:
            row['sold_out'] = 0

    con.close()
    return jsonify(rows)


@app.post('/api/order')
def api_order():
    data = request.get_json(force=True)
    items = data.get('items', [])

    if not items:
        return jsonify({'ok': False, 'message': '장바구니가 비어있습니다.'}), 400

    con = db()
    cur = con.cursor()
    menu_by_id = {r['id']: dict(r) for r in cur.execute('SELECT * FROM menu')}
    checked = []
    total = 0

    for item in items:
        menu_id = int(item['id'])
        qty = int(item['qty'])
        menu_item = menu_by_id.get(menu_id)

        if not menu_item or qty <= 0:
            continue

        if menu_item['category'] == 'straight':
            required = menu_item['serving_ml'] * qty
            if menu_item['stock_ml'] < required:
                con.close()
                return jsonify({'ok': False, 'message': f'{menu_item["name"]} 재고가 부족합니다.'}), 409

        if menu_item['category'] == 'cocktail':
            shortage = get_cocktail_shortage(cur, menu_item['name'], qty)
            if shortage:
                con.close()
                return jsonify({'ok': False, 'message': f'{menu_item["name"]} 제조에 필요한 {shortage} 재고가 부족합니다.'}), 409

        checked.append((menu_item, qty))
        total += menu_item['price'] * qty

    if not checked:
        con.close()
        return jsonify({'ok': False, 'message': '주문 가능한 항목이 없습니다.'}), 400

    order_number = 'A' + datetime.now().strftime('%Y%m%d%H%M%S')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cur.execute(
        'INSERT INTO orders(order_number,created_at,total_price,status) VALUES(?,?,?,?)',
        (order_number, now, total, 'completed')
    )
    order_id = cur.lastrowid

    for menu_item, qty in checked:
        cur.execute('''
            INSERT INTO order_items(order_id,menu_id,menu_name,category,qty,unit_price,line_total)
            VALUES(?,?,?,?,?,?,?)
        ''', (
            order_id,
            menu_item['id'],
            menu_item['name'],
            menu_item['category'],
            qty,
            menu_item['price'],
            menu_item['price'] * qty,
        ))

        if menu_item['category'] == 'straight':
            cur.execute(
                'UPDATE menu SET stock_ml = MAX(stock_ml - ?, 0) WHERE id=?',
                (menu_item['serving_ml'] * qty, menu_item['id'])
            )

        if menu_item['category'] == 'cocktail':
            for ing in COCKTAIL_RECIPES.get(menu_item['name'], []):
                cur.execute(
                    'UPDATE menu SET stock_ml = MAX(stock_ml - ?, 0) WHERE name=? AND category=?',
                    (ing['ml'] * qty, ing['name'], 'straight')
                )

        if menu_item['category'] == 'request':
            cur.execute(
                'INSERT INTO staff_requests(request_type,created_at) VALUES(?,?)',
                (menu_item['name'], now)
            )

    con.commit()
    con.close()
    return jsonify({'ok': True, 'order_number': order_number, 'total': total})


# =============================
# Admin APIs
# =============================

@app.get('/api/inventory')
def api_inventory():
    con = db()
    rows = [dict(r) for r in con.execute("SELECT * FROM menu WHERE category='straight' ORDER BY id")]
    con.close()
    return jsonify(rows)


@app.post('/api/inventory')
def api_inventory_update():
    data = request.get_json(force=True)
    con = db()
    cur = con.cursor()

    for row in data.get('items', []):
        menu_id = int(row['id'])
        bottles = max(0, int(row['bottles']))
        bottle_row = cur.execute('SELECT bottle_ml FROM menu WHERE id=?', (menu_id,)).fetchone()
        if not bottle_row:
            continue
        cur.execute('UPDATE menu SET stock_ml=? WHERE id=?', (bottles * bottle_row['bottle_ml'], menu_id))

    con.commit()
    con.close()
    add_log('재고 정보가 수정되었습니다.')
    return jsonify({'ok': True})


@app.get('/api/orders')
def api_orders():
    con = db()
    cur = con.cursor()
    orders = []

    for order in cur.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 200'):
        items = [dict(i) for i in con.execute(
            'SELECT menu_name,qty,line_total FROM order_items WHERE order_id=?',
            (order['id'],)
        )]
        order_dict = dict(order)
        order_dict['items'] = items
        orders.append(order_dict)

    today = date.today().strftime('%Y-%m-%d')
    total_today = cur.execute(
        "SELECT COALESCE(SUM(total_price),0) FROM orders WHERE substr(created_at,1,10)=?",
        (today,)
    ).fetchone()[0]

    con.close()
    return jsonify({'orders': orders, 'total_today': total_today})


@app.get('/api/staff_requests')
def api_staff_requests():
    con = db()
    rows = [dict(r) for r in con.execute('SELECT * FROM staff_requests WHERE handled=0 ORDER BY id')]
    con.close()
    return jsonify(rows)


@app.post('/api/staff_requests/<int:req_id>/handle')
def api_staff_handle(req_id):
    con = db()
    con.execute('UPDATE staff_requests SET handled=1 WHERE id=?', (req_id,))
    con.commit()
    con.close()
    return jsonify({'ok': True})


@app.get('/api/robot/status')
def api_robot_status():
    if robot_bridge is None:
        return jsonify({
            'mode': 'ERROR',
            'joints': [0, 0, 0, 0, 0, 0],
            'recipe': '대기 중',
            'step': 'ROS bridge not started',
            'connections': {'ros': False, 'mcu': False, 'plc': False},
            'speed': {'linear': 0, 'angular': 0},
            'resource': {'cpu': 0, 'memory': 0, 'temperature': 0},
            'taskIndex': 0,
            'logs': ['ROS bridge not started'],
        })
    return jsonify(robot_bridge.status_payload())


@app.post('/api/robot/command')
def api_robot_command():
    command = request.get_json(force=True).get('command')

    if robot_bridge is None:
        return jsonify({'ok': False, 'message': 'ROS bridge가 시작되지 않았습니다.'}), 503

    ok, message = robot_bridge.command(command)
    add_log(message)
    return jsonify({'ok': ok, 'message': message}), (200 if ok else 503)


if __name__ == '__main__':
    init_db()
    start_ros_bridge()
    app.run(host='0.0.0.0', port=5001, debug=True, use_reloader=False)
