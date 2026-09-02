from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3
from pathlib import Path
from datetime import datetime, date
import math
import threading
import time
import importlib

try:
    import psutil
except Exception:
    psutil = None

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.executors import MultiThreadedExecutor
    from sensor_msgs.msg import JointState
    ROS_AVAILABLE = True
except Exception as exc:
    rclpy = None
    Node = object
    MultiThreadedExecutor = None
    JointState = None
    ROS_AVAILABLE = False
    ROS_IMPORT_ERROR = str(exc)
else:
    ROS_IMPORT_ERROR = ''


def import_dsr_service(service_name):
    """dsr_msgs2.srv 서비스 타입을 선택적으로 가져옵니다.

    조인트 상태 표시는 sensor_msgs/msg/JointState만 사용하므로 dsr_msgs2가 없어도 동작합니다.
    단, Robot ON/OFF, E-STOP 같은 실제 명령은 dsr_msgs2 서비스 타입이 필요합니다.
    """
    try:
        srv_module = importlib.import_module('dsr_msgs2.srv')
        return getattr(srv_module, service_name)
    except Exception:
        return None


MoveStop = import_dsr_service('MoveStop')
ServoOff = import_dsr_service('ServoOff')
SetRobotControl = import_dsr_service('SetRobotControl')
SetSafeStopResetType = import_dsr_service('SetSafeStopResetType')
SetToolDigitalOutput = import_dsr_service('SetToolDigitalOutput')

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

COCKTAIL_RECIPES = {
    'Old Fashioned': [{'name': "Maker's Mark", 'ml': 45}],
    'Mojito': [{'name': 'Jameson', 'ml': 30}],
    'Whisky Sour': [{'name': 'Johnnie Walker Black', 'ml': 45}],
}


class DoosanRosBridge(Node if ROS_AVAILABLE else object):
    """Flask 내부에서 실행되는 ROS2 브릿지.

    - 현재 조인트 각도: /dsr01/joint_states(sensor_msgs/msg/JointState) 구독
    - Robot ON/OFF, E-STOP 등: Doosan ROS2 service client 호출
    - 별도 제어 노드 없이 app.py 하나가 UI와 ROS2 사이를 직접 연결
    """

    def __init__(self):
        if not ROS_AVAILABLE:
            self.available = False
            self.import_error = ROS_IMPORT_ERROR
            self.joints = [0.0] * 6
            self.joint_velocity = [0.0] * 6
            self.last_joint_time = 0.0
            self.last_command_mode = 'ERROR'
            self.logs = ['ROS2 import failed: ' + self.import_error]
            return

        super().__init__('bartender_admin_ui_bridge')
        self.available = True
        self.import_error = ''
        self.joints = [0.0] * 6
        self.joint_velocity = [0.0] * 6
        self.last_joint_time = 0.0
        self.last_command_mode = 'IDLE'
        self.logs = []

        self.joint_topic = '/dsr01/joint_states'
        self.create_subscription(JointState, self.joint_topic, self._joint_callback, 10)

        self.clients = {}
        self._create_client_if_available('robot_on', SetRobotControl, '/dsr01/system/set_robot_control')
        self._create_client_if_available('robot_off', ServoOff, '/dsr01/system/servo_off')
        self._create_client_if_available('estop', MoveStop, '/dsr01/motion/move_stop')
        self._create_client_if_available('estop_reset', SetSafeStopResetType, '/dsr01/system/set_safe_stop_reset_type')
        self._create_client_if_available('tool_do', SetToolDigitalOutput, '/dsr01/io/set_tool_digital_output')

        self._log('ROS2 bridge started.')
        self._log('Subscribing ' + self.joint_topic)
        if SetToolDigitalOutput is None:
            self._log('SetToolDigitalOutput service type not available. Grip/Ungrip requires gripper service mapping.')

    def _create_client_if_available(self, key, srv_type, service_name):
        if srv_type is None:
            self.clients[key] = None
            return
        self.clients[key] = self.create_client(srv_type, service_name)

    def _log(self, message):
        stamp = datetime.now().strftime('%H:%M:%S')
        self.logs.insert(0, f'[{stamp}] {message}')
        self.logs = self.logs[:100]

    def _joint_callback(self, msg):
        self.last_joint_time = time.time()
        position = list(getattr(msg, 'position', []))[:6]
        velocity = list(getattr(msg, 'velocity', []))[:6]

        while len(position) < 6:
            position.append(0.0)
        while len(velocity) < 6:
            velocity.append(0.0)

        # sensor_msgs/JointState는 보통 rad 단위입니다.
        # 만약 값이 이미 degree처럼 크면 그대로 쓰고, rad 범위면 degree로 변환합니다.
        max_abs = max(abs(float(v)) for v in position) if position else 0.0
        if max_abs <= (2 * math.pi + 0.2):
            self.joints = [round(math.degrees(float(v)), 3) for v in position]
            self.joint_velocity = [round(math.degrees(float(v)), 3) for v in velocity]
        else:
            self.joints = [round(float(v), 3) for v in position]
            self.joint_velocity = [round(float(v), 3) for v in velocity]

    def _set_request_field(self, request_obj, candidates, value):
        for name in candidates:
            if hasattr(request_obj, name):
                setattr(request_obj, name, value)
                return True
        return False

    def _call_service(self, key, configure=None, timeout=1.0):
        if not self.available:
            return False, 'ROS2 사용 불가: ' + self.import_error

        client = self.clients.get(key)
        if client is None:
            return False, f'{key} 서비스 타입을 찾을 수 없습니다. dsr_msgs2 환경 source 또는 서비스 매핑 확인이 필요합니다.'

        if not client.wait_for_service(timeout_sec=timeout):
            return False, f'{client.srv_name} 서비스를 찾을 수 없습니다.'

        request_obj = client.srv_type.Request()
        if configure:
            configure(request_obj)

        future = client.call_async(request_obj)
        future.add_done_callback(lambda f: self._service_done(key, f))
        return True, f'{key} 명령을 로봇에 전송했습니다.'

    def _service_done(self, key, future):
        try:
            result = future.result()
            self._log(f'{key} service response: {result}')
        except Exception as exc:
            self._log(f'{key} service failed: {exc}')

    def _tool_output(self, value):
        def cfg(req):
            self._set_request_field(req, ['index', 'pin', 'channel'], 1)
            self._set_request_field(req, ['val', 'value', 'output'], int(value))
        return self._call_service('tool_do', cfg)

    def command(self, command):
        if command == 'robot_on':
            def cfg(req):
                self._set_request_field(req, ['robot_control', 'control', 'control_mode', 'mode'], 1)
            ok, msg = self._call_service('robot_on', cfg)
            if ok:
                self.last_command_mode = 'IDLE'
            return ok, msg

        if command == 'robot_off':
            ok, msg = self._call_service('robot_off')
            if ok:
                self.last_command_mode = 'IDLE'
            return ok, msg

        if command == 'grip':
            ok, msg = self._tool_output(1)
            return ok, msg if ok else msg + ' Grip은 현재 /dsr01/io/set_tool_digital_output 기준으로 구성되어 있습니다.'

        if command == 'ungrip':
            ok, msg = self._tool_output(0)
            return ok, msg if ok else msg + ' Ungrip은 현재 /dsr01/io/set_tool_digital_output 기준으로 구성되어 있습니다.'

        if command == 'estop':
            def cfg(req):
                self._set_request_field(req, ['stop_mode', 'mode', 'stop_type'], 1)
            ok, msg = self._call_service('estop', cfg, timeout=0.3)
            if ok:
                self.last_command_mode = 'ESTOP'
            return ok, msg

        if command == 'estop_reset':
            def cfg(req):
                self._set_request_field(req, ['reset_type', 'type', 'mode', 'safe_stop_reset_type'], 0)
            ok, msg = self._call_service('estop_reset', cfg)
            if ok:
                self.last_command_mode = 'IDLE'
            return ok, msg

        return False, '알 수 없는 명령입니다.'

    def ros_connected(self):
        return self.available and (time.time() - self.last_joint_time < 3.0)

    def mode_text(self):
        if not self.ros_connected():
            return 'ERROR'
        if self.last_command_mode == 'ESTOP':
            return 'ESTOP'
        moving = any(abs(v) > 0.1 for v in self.joint_velocity)
        return 'AUTO' if moving else 'IDLE'

    def angular_speed(self):
        if not self.joint_velocity:
            return 0
        return round(max(abs(v) for v in self.joint_velocity), 1)

    def resource_status(self):
        cpu = psutil.cpu_percent(interval=None) if psutil else 0
        memory = psutil.virtual_memory().percent if psutil else 0
        temperature = 0
        if psutil:
            try:
                temps = psutil.sensors_temperatures()
                values = [t.current for group in temps.values() for t in group if getattr(t, 'current', None) is not None]
                if values:
                    temperature = round(sum(values) / len(values), 1)
            except Exception:
                temperature = 0
        return {'cpu': round(cpu, 1), 'memory': round(memory, 1), 'temperature': temperature}

    def status_payload(self):
        connected = self.ros_connected()
        return {
            'mode': self.mode_text(),
            'joints': self.joints,
            'recipe': '대기 중',
            'step': 'Ready',
            'connections': {
                'ros': connected,
                'mcu': connected,
                'plc': connected,
            },
            'speed': {
                'linear': 0,
                'angular': self.angular_speed(),
            },
            'resource': self.resource_status(),
            'taskIndex': 0,
            'logs': self.logs,
            'ros_available': self.available,
            'ros_import_error': self.import_error,
        }


robot_bridge = None
ros_executor = None
ros_thread = None


def start_ros_bridge():
    global robot_bridge, ros_executor, ros_thread
    if robot_bridge is not None:
        return

    if ROS_AVAILABLE and not rclpy.ok():
        rclpy.init(args=None)

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
    con.commit()
    con.close()


def get_cocktail_shortage(cur, cocktail_name, qty=1):
    recipe = COCKTAIL_RECIPES.get(cocktail_name, [])
    for ingredient in recipe:
        row = cur.execute('SELECT stock_ml FROM menu WHERE name=? AND category=?', (ingredient['name'], 'straight')).fetchone()
        required_ml = ingredient['ml'] * qty
        if row is None or row['stock_ml'] < required_ml:
            return ingredient['name']
    return None


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
    session.clear()
    return redirect('/admin')


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
    total = 0
    checked = []

    for item in items:
        menu_id = int(item['id'])
        qty = int(item['qty'])
        menu_item = menu_by_id.get(menu_id)
        if not menu_item or qty <= 0:
            continue
        if menu_item['category'] == 'straight' and menu_item['stock_ml'] < menu_item['serving_ml'] * qty:
            con.close()
            return jsonify({'ok': False, 'message': f'{menu_item["name"]} 재고가 부족합니다.'}), 409
        if menu_item['category'] == 'cocktail':
            shortage = get_cocktail_shortage(cur, menu_item['name'], qty)
            if shortage:
                con.close()
                return jsonify({'ok': False, 'message': f'{menu_item["name"]} 제조에 필요한 {shortage} 재고가 부족합니다.'}), 409
        checked.append((menu_item, qty))
        total += menu_item['price'] * qty

    order_number = 'A' + datetime.now().strftime('%Y%m%d%H%M%S')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur.execute('INSERT INTO orders(order_number,created_at,total_price,status) VALUES(?,?,?,?)', (order_number, now, total, 'completed'))
    order_id = cur.lastrowid

    for menu_item, qty in checked:
        cur.execute('''INSERT INTO order_items(order_id,menu_id,menu_name,category,qty,unit_price,line_total)
                       VALUES(?,?,?,?,?,?,?)''', (order_id, menu_item['id'], menu_item['name'], menu_item['category'], qty, menu_item['price'], menu_item['price'] * qty))
        if menu_item['category'] == 'straight':
            cur.execute('UPDATE menu SET stock_ml = MAX(stock_ml - ?, 0) WHERE id=?', (menu_item['serving_ml'] * qty, menu_item['id']))
        if menu_item['category'] == 'cocktail':
            for ingredient in COCKTAIL_RECIPES.get(menu_item['name'], []):
                cur.execute('UPDATE menu SET stock_ml = MAX(stock_ml - ?, 0) WHERE name=? AND category=?', (ingredient['ml'] * qty, ingredient['name'], 'straight'))
        if menu_item['category'] == 'request':
            cur.execute('INSERT INTO staff_requests(request_type,created_at) VALUES(?,?)', (menu_item['name'], now))

    con.commit()
    con.close()
    return jsonify({'ok': True, 'order_number': order_number, 'total': total})


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
        bottle_ml = cur.execute('SELECT bottle_ml FROM menu WHERE id=?', (menu_id,)).fetchone()['bottle_ml']
        cur.execute('UPDATE menu SET stock_ml=? WHERE id=?', (bottles * bottle_ml, menu_id))
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
        items = [dict(i) for i in con.execute('SELECT menu_name,qty,line_total FROM order_items WHERE order_id=?', (order['id'],))]
        row = dict(order)
        row['items'] = items
        orders.append(row)
    today = date.today().strftime('%Y-%m-%d')
    total_today = cur.execute("SELECT COALESCE(SUM(total_price),0) FROM orders WHERE substr(created_at,1,10)=?", (today,)).fetchone()[0]
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
            'recipe': '상태 수신 실패',
            'step': 'ROS bridge not started',
            'connections': {'ros': False, 'mcu': False, 'plc': False},
            'speed': {'linear': 0, 'angular': 0},
            'resource': {'cpu': 0, 'memory': 0, 'temperature': 0},
            'taskIndex': 0,
            'logs': ['[SYSTEM] ROS bridge not started'],
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


# 구버전 관리자 UI 호환용 API입니다. 새 대시보드는 /api/robot/status, /api/robot/command를 사용합니다.
@app.get('/api/robot')
def api_robot_state_legacy():
    con = db()
    state = dict(con.execute('SELECT * FROM robot_state WHERE id=1').fetchone())
    logs = [dict(r) for r in con.execute('SELECT * FROM robot_logs ORDER BY id DESC LIMIT 30')]
    con.close()
    return jsonify({'state': state, 'logs': logs})


@app.post('/api/robot/action')
def api_robot_action_legacy():
    data = request.get_json(force=True)
    action = data.get('action')
    command_map = {
        'power_on': 'robot_on',
        'power_off': 'robot_off',
        'emergency_stop': 'estop',
        'reset_error': 'estop_reset',
        'grip': 'grip',
        'ungrip': 'ungrip',
    }
    command = command_map.get(action)
    if not command:
        return jsonify({'ok': False, 'message': '알 수 없는 명령입니다.'}), 400
    if robot_bridge is None:
        return jsonify({'ok': False, 'message': 'ROS bridge가 시작되지 않았습니다.'}), 503
    ok, message = robot_bridge.command(command)
    add_log(message)
    return jsonify({'ok': ok, 'message': message}), (200 if ok else 503)


@app.post('/api/robot/finish')
def api_robot_finish():
    add_log('동작 완료 처리')
    return jsonify({'ok': True})


if __name__ == '__main__':
    init_db()
    start_ros_bridge()
    app.run(host='0.0.0.0', port=5001, debug=True, use_reloader=False)
