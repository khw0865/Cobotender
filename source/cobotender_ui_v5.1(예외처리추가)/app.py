from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3
from pathlib import Path
from datetime import datetime, date
import math
import re
import threading
import time
import json


try:
    import rclpy
    from rclpy.node import Node
    from rclpy.executors import MultiThreadedExecutor
    from sensor_msgs.msg import JointState
    from std_msgs.msg import Bool, String
    ROS_CORE_AVAILABLE = True
except Exception as exc:
    rclpy = None
    Node = object
    MultiThreadedExecutor = None
    JointState = None
    Bool = None
    String = None
    ROS_CORE_AVAILABLE = False
    ROS_CORE_IMPORT_ERROR = str(exc)
else:
    ROS_CORE_IMPORT_ERROR = ''

try:
    from control_msgs.msg import DynamicJointState
    DYNAMIC_JOINT_AVAILABLE = True
except Exception as exc:
    DynamicJointState = None
    DYNAMIC_JOINT_AVAILABLE = False
    DYNAMIC_JOINT_IMPORT_ERROR = str(exc)
else:
    DYNAMIC_JOINT_IMPORT_ERROR = ''


try:
    from bartender_interfaces.msg import Menu, Status
    BARTENDER_MSG_AVAILABLE = True
except Exception as exc:
    Menu = None
    Status = None
    BARTENDER_MSG_AVAILABLE = False
    BARTENDER_MSG_IMPORT_ERROR = str(exc)
else:
    BARTENDER_MSG_IMPORT_ERROR = ''

# TCP velocity service is intentionally not used in the admin dashboard.
# Joint velocity is displayed from ROS2 joint-state topics for better responsiveness.


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

# UI 메뉴명을 bartender_final.py에서 사용하는 menu 코드로 변환합니다.
# /ui/menu_command : bartender_interfaces/msg/Menu, field: menu(int)
MENU_COMMAND_MAP = {
    'Old Fashioned': 0,          # Whiskey cocktail
    'Mojito': 1,                 # Vodka cocktail slot
    'Whisky Sour': 2,            # Non-Alcohol cocktail slot
    'Macallan 12': 3,            # STRAIGHT1
    'Glenfiddich 12': 4,         # STRAIGHT2
    'Jameson': 5,                # STRAIGHT3
    "Maker's Mark": 6,          # STRAIGHT4
    "Ballantine's 17": 7,       # STRAIGHT5
    'Johnnie Walker Black': 8,   # STRAIGHT6
}

# /robot/process_state : bartender_interfaces/msg/Status
# field: status(int)
# 관리자 UI의 작업 진행 정보는 아래 status 값과 1:1로 매핑됩니다.
ROBOT_STATUS_TEXT = {
    0: ('IDLE', 'WAITING - 대기 중 / 주문 가능 상태', 0),
    1: ('AUTO', 'MAKING - 음료 제조 중', 1),
    2: ('AUTO', 'MAKING_DONE - 제조 완료', 2),
    3: ('AUTO', 'DELIVERING - 손님한테 서빙 이동 중', 3),
    4: ('AUTO', 'DELIVERED - 서빙 완료', 4),
    5: ('AUTO', 'RETURNING_HOME - 초기 위치로 복귀 중', 5),
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
            self.bartender_msg_import_error = BARTENDER_MSG_IMPORT_ERROR
            self.last_joint_time = 0.0
            self.joints = [0.0] * 6
            self.joint_velocities = [0.0] * 6
            self.last_joint_velocity_time = 0.0
            self.command_logs = ['ROS2 import failed: ' + self.core_import_error]
            self.last_mode = 'ERROR'
            self.last_command = 'ROS2 연결 실패'
            self.last_recipe = '대기 중'
            self.last_task_index = 0
            self.robot_status_raw = 0
            self.admin_bridge_status = {}
            self.last_admin_bridge_status_time = 0.0
            self.lock = threading.Lock()
            return

        super().__init__('bartender_admin_ui_bridge')

        self.available = True
        self.core_import_error = ''
        self.bartender_msg_import_error = BARTENDER_MSG_IMPORT_ERROR if not BARTENDER_MSG_AVAILABLE else ''
        self.last_joint_time = 0.0
        self.joints = [0.0] * 6
        self.joint_velocities = [0.0] * 6
        self.last_joint_velocity_time = 0.0
        self.command_logs = []
        self.last_mode = 'IDLE'
        self.last_command = 'Ready'
        self.last_recipe = '대기 중'
        self.last_task_index = 0
        self.robot_status_raw = 0
        self.admin_bridge_status = {}
        self.last_admin_bridge_status_time = 0.0
        self.lock = threading.Lock()

        self.joint_topic = '/dsr01/joint_states'
        self.dynamic_joint_topic = '/dsr01/dynamic_joint_states'
        self.create_subscription(JointState, self.joint_topic, self._joint_callback, 10)
        if DYNAMIC_JOINT_AVAILABLE:
            self.create_subscription(
                DynamicJointState,
                self.dynamic_joint_topic,
                self._dynamic_joint_callback,
                10
            )
        else:
            self._log('control_msgs/msg/DynamicJointState import failed: ' + DYNAMIC_JOINT_IMPORT_ERROR)

        self.menu_publisher = None
        self.status_subscription = None
        if BARTENDER_MSG_AVAILABLE:
            self.menu_publisher = self.create_publisher(Menu, '/ui/menu_command', 10)
            self.status_subscription = self.create_subscription(
                Status,
                '/robot/process_state',
                self._process_state_callback,
                10
            )
        else:
            self._log('bartender_interfaces import failed: ' + self.bartender_msg_import_error)

        self.emergency_publisher = self.create_publisher(Bool, '/ui/emergency_stop', 10)
        self.admin_control_publisher = self.create_publisher(String, '/ui/admin_control', 10)
        self.admin_bridge_status_subscription = self.create_subscription(
            String,
            '/robot/admin_bridge_status',
            self._admin_bridge_status_callback,
            10
        )

        self._log('ROS2 bridge started. Subscribing ' + self.joint_topic)
        if DYNAMIC_JOINT_AVAILABLE:
            self._log('Subscribing ' + self.dynamic_joint_topic + ' for joint velocity')
        if BARTENDER_MSG_AVAILABLE:
            self._log('Publishing /ui/menu_command, subscribing /robot/process_state')
        self._log('Publishing /ui/emergency_stop')
        self._log('Publishing /ui/admin_control, subscribing /robot/admin_bridge_status')

    def _log(self, message):
        stamp = datetime.now().strftime('%H:%M:%S')
        with getattr(self, 'lock', threading.Lock()):
            self.command_logs.insert(0, f'[{stamp}] {message}')
            self.command_logs = self.command_logs[:80]

    def _pad_six(self, values):
        values = [float(v) for v in values[:6]]
        return values + [0.0] * max(0, 6 - len(values))

    def _joint_name_to_index(self, name):
        match = re.search(r'(\d+)$', str(name))
        if not match:
            return None
        idx = int(match.group(1)) - 1
        return idx if 0 <= idx < 6 else None

    def _joint_callback(self, msg):
        positions = list(msg.position)
        velocities = list(getattr(msg, 'velocity', []))

        # JointState position/velocity are usually radian/radian-sec in ROS2.
        # Convert to degree/degree-sec for the admin UI.
        positions_are_radian = bool(positions) and max(abs(v) for v in positions[:6]) <= 6.5
        if positions_are_radian:
            positions = [math.degrees(v) for v in positions]
            velocities = [math.degrees(v) for v in velocities]

        with self.lock:
            now = time.time()
            self.last_joint_time = now
            self.joints = self._pad_six(positions)
            if velocities:
                self.joint_velocities = self._pad_six(velocities)
                self.last_joint_velocity_time = now

    def _dynamic_joint_callback(self, msg):
        # /dsr01/dynamic_joint_states can arrive in a non-J1~J6 order.
        # Parse joint_names and write velocity values to the correct index.
        velocities = [0.0] * 6
        updated = False

        for joint_name, interface_value in zip(msg.joint_names, msg.interface_values):
            idx = self._joint_name_to_index(joint_name)
            if idx is None:
                continue

            try:
                interface_names = list(interface_value.interface_names)
                values = list(interface_value.values)
                vel_index = interface_names.index('velocity')
                velocity_rad_s = float(values[vel_index])
            except Exception:
                continue

            velocities[idx] = math.degrees(velocity_rad_s)
            updated = True

        if updated:
            with self.lock:
                self.joint_velocities = velocities
                self.last_joint_velocity_time = time.time()

    def _process_state_callback(self, msg):
        status = int(getattr(msg, 'status', 0))
        mode, step, task_index = ROBOT_STATUS_TEXT.get(status, ('ERROR', '알 수 없는 상태', 0))

        with self.lock:
            self.robot_status_raw = status
            self.last_mode = mode
            self.last_command = step
            self.last_task_index = task_index

        self._log(f'Robot process state: {status} / {step}')


    def _admin_bridge_status_callback(self, msg):
        try:
            data = json.loads(msg.data)
        except Exception as exc:
            self._log(f'Admin bridge status parse failed: {exc}')
            return

        with self.lock:
            self.admin_bridge_status = data
            self.last_admin_bridge_status_time = time.time()

    def publish_menu_command(self, menu_code, label='', qty=1):
        if not self.available:
            return False, 'ROS2 사용 불가: ' + self.core_import_error

        if not BARTENDER_MSG_AVAILABLE or self.menu_publisher is None:
            return False, 'bartender_interfaces/msg/Menu를 import할 수 없습니다: ' + self.bartender_msg_import_error

        try:
            count = max(1, int(qty))
            for _ in range(count):
                msg = Menu()
                msg.menu = int(menu_code)
                self.menu_publisher.publish(msg)

            with self.lock:
                self.last_recipe = label or f'menu {menu_code}'
                self.last_mode = 'AUTO'
                self.last_command = '주문 전송'

            self._log(f'Menu command published: {menu_code} / {label} x {count}')
            return True, f'{label or menu_code} 주문 명령을 전송했습니다.'
        except Exception as exc:
            self._log(f'Menu command publish failed: {exc}')
            return False, f'메뉴 명령 전송 실패: {exc}'

    def publish_emergency_stop(self, flag=True):
        if not self.available:
            return False, 'ROS2 사용 불가: ' + self.core_import_error

        try:
            msg = Bool()
            msg.data = bool(flag)
            self.emergency_publisher.publish(msg)

            with self.lock:
                self.last_mode = 'ESTOP' if flag else 'IDLE'
                self.last_command = 'Emergency Stop' if flag else 'Emergency Reset'
                self.last_task_index = 0

            self._log(f'Emergency stop published: {msg.data}')
            if flag:
                return True, '긴급 정지 신호를 전송했습니다.'
            return True, '긴급 정지 해제 신호를 전송했습니다. bartender_final.py에서 False를 해제 신호로 처리해야 합니다.'
        except Exception as exc:
            self._log(f'Emergency publish failed: {exc}')
            return False, f'긴급 정지 신호 전송 실패: {exc}'


    def publish_admin_control(self, command):
        if not self.available:
            return False, 'ROS2 사용 불가: ' + self.core_import_error

        if String is None:
            return False, 'std_msgs/msg/String을 사용할 수 없습니다.'

        command = str(command).strip().upper()
        try:
            msg = String()
            msg.data = command
            self.admin_control_publisher.publish(msg)

            with self.lock:
                if command == 'PAUSE':
                    self.last_mode = 'MANUAL'
                    self.last_command = '관리자 일시정지 요청'
                elif command == 'RESUME':
                    self.last_mode = 'AUTO'
                    self.last_command = '관리자 재개 요청'
                elif command == 'CANCEL':
                    self.last_mode = 'MANUAL'
                    self.last_command = '관리자 주문취소 요청'
                elif command == 'RECOVER':
                    self.last_mode = 'MANUAL'
                    self.last_command = '관리자 Recovery 요청'
                elif command == 'ESTOP':
                    self.last_mode = 'ESTOP'
                    self.last_command = '관리자 비상정지 요청'
                elif command == 'ESTOP_RELEASE_HOME':
                    self.last_mode = 'MANUAL'
                    self.last_command = '비상정지 해제 + 홈복귀 요청'

            self._log(f'Admin control published: {command}')
            return True, f'관리자 명령 {command}을 /ui/admin_control로 전송했습니다.'
        except Exception as exc:
            self._log(f'Admin control publish failed: {exc}')
            return False, f'관리자 명령 전송 실패: {exc}'

    def command(self, command):
        command = str(command or '').strip().lower()
        admin_command_map = {
            'pause': 'PAUSE',
            'resume': 'RESUME',
            'cancel': 'CANCEL',
            'recover': 'RECOVER',
            'estop': 'ESTOP',
            'estop_reset': 'ESTOP_RELEASE_HOME',
        }

        if command not in admin_command_map:
            return False, f'{command} 명령은 현재 UI-bridge 통신 규격에 포함되어 있지 않습니다.'

        ok, message = self.publish_admin_control(admin_command_map[command])

        # 기존 바텐더 제어코드 호환용 fallback입니다.
        # 새 bridge 노드를 실행하면 /ui/admin_control -> /ui/emergency_stop으로도 다시 분배되므로
        # 중복 publish가 될 수 있지만 Bool 정지/해제 신호라 안전하게 무시 가능합니다.
        if command == 'estop':
            self.publish_emergency_stop(True)
        elif command == 'estop_reset':
            self.publish_emergency_stop(False)

        return ok, message

    def status_payload(self):
        with self.lock:
            joints = list(self.joints)
            joint_velocities = list(self.joint_velocities)
            logs = list(self.command_logs)
            last_joint_time = self.last_joint_time
            last_joint_velocity_time = self.last_joint_velocity_time
            mode = self.last_mode
            recipe = self.last_recipe
            step = self.last_command
            task_index = self.last_task_index
            status_raw = self.robot_status_raw
            admin_bridge_status = dict(self.admin_bridge_status) if isinstance(self.admin_bridge_status, dict) else {}
            last_admin_bridge_status_time = self.last_admin_bridge_status_time

        now = time.time()
        ros_connected = self.available and (now - last_joint_time < 3.0)
        if not ros_connected:
            mode = 'ERROR'

        joint_velocities = self._pad_six(joint_velocities)
        joint_velocity_average = round(sum(abs(v) for v in joint_velocities) / 6.0, 2)
        joint_velocities_rounded = [round(v, 2) for v in joint_velocities]

        admin_bridge_age = (now - last_admin_bridge_status_time) if last_admin_bridge_status_time else None
        admin_bridge_connected = bool(admin_bridge_status) and admin_bridge_age is not None and admin_bridge_age < 3.0
        admin_bridge_status['ui_bridge_connected'] = admin_bridge_connected
        admin_bridge_status['ui_bridge_age_sec'] = round(admin_bridge_age, 3) if admin_bridge_age is not None else None

        bridge_logs = []
        for item in admin_bridge_status.get('logs', []) if isinstance(admin_bridge_status, dict) else []:
            if isinstance(item, dict):
                ts = item.get('time', '--:--:--')
                level = item.get('level', 'INFO')
                msg = item.get('message', '')
                bridge_logs.append(f'[{ts}] [BRIDGE/{level}] {msg}')
            else:
                bridge_logs.append(f'[BRIDGE] {item}')
        combined_logs = (bridge_logs + logs)[:100]

        return {
            'mode': mode,
            'joints': joints,
            'jointVelocities': joint_velocities_rounded,
            'jointVelocityAverage': joint_velocity_average,
            'recipe': recipe,
            'step': step,
            'speed': {
                'jointVelocities': joint_velocities_rounded,
                'jointAverage': joint_velocity_average,
            },
            'taskIndex': task_index,
            'logs': combined_logs,
            'adminBridge': admin_bridge_status,
            'robot_status_raw': status_raw,
            'ros_available': self.available,
            'ros_core_import_error': self.core_import_error,
            'bartender_msg_import_error': self.bartender_msg_import_error,
            'dynamic_joint_available': DYNAMIC_JOINT_AVAILABLE,
            'dynamic_joint_import_error': DYNAMIC_JOINT_IMPORT_ERROR,
            'joint_velocity_age_sec': round(now - last_joint_velocity_time, 3) if last_joint_velocity_time else None,
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


def publish_robot_menu_commands(checked_items):
    """Publish drink orders to the robot and return a small status payload.

    Customer UI uses this result to decide whether it should wait for the
    robot process state instead of using a fixed loading timeout.
    """
    command_items = []
    for menu_item, qty in checked_items:
        menu_code = MENU_COMMAND_MAP.get(menu_item['name'])
        if menu_code is None:
            continue
        command_items.append((menu_item, qty, menu_code))

    result = {
        'required': bool(command_items),
        'sent': 0,
        'failed': 0,
        'messages': [],
    }

    if not command_items:
        return result

    if robot_bridge is None:
        result['failed'] = sum(max(1, int(qty)) for _, qty, _ in command_items)
        result['messages'].append('ROS bridge가 시작되지 않아 로봇 명령을 전송하지 못했습니다.')
        return result

    for menu_item, qty, menu_code in command_items:
        count = max(1, int(qty))
        ok, message = robot_bridge.publish_menu_command(menu_code, menu_item['name'], count)
        result['messages'].append(message)
        if ok:
            result['sent'] += count
        else:
            result['failed'] += count

    return result


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
@app.route('/admin/', methods=['GET', 'POST'])
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

        if menu_item['category'] == 'snack':
            # 안주는 로봇 제조 대상은 아니지만 직원이 준비해야 하므로
            # 기존 요구사항 팝업과 같은 staff_requests 테이블에 기록합니다.
            snack_request = f'안주 주문: {menu_item["name"]} x{qty}'
            cur.execute(
                'INSERT INTO staff_requests(request_type,created_at) VALUES(?,?)',
                (snack_request, now)
            )

    con.commit()
    con.close()

    robot_command_result = publish_robot_menu_commands(checked)

    return jsonify({
        'ok': True,
        'order_number': order_number,
        'total': total,
        'requires_robot_work': robot_command_result['required'],
        'robot_command_sent': robot_command_result['sent'] > 0,
        'robot_command': robot_command_result,
    })


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
            'jointVelocities': [0, 0, 0, 0, 0, 0],
            'jointVelocityAverage': 0,
            'speed': {'jointVelocities': [0, 0, 0, 0, 0, 0], 'jointAverage': 0},
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
    return jsonify({'ok': ok, 'message': message}), (200 if ok else 503)


def main(args=None):
    init_db()
    start_ros_bridge()
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
