import threading
import select
import sys
import time
import json
import math

import DR_init
import rclpy
from bartender_interfaces.msg import Menu, Status
from dsr_msgs2.srv import MoveStop, SetRobotControl
from onrobot_rg_msgs.srv import SetCommand
from rclpy.node import Node
from rclpy.executors import SingleThreadedExecutor
from std_msgs.msg import Bool, String


ROBOT_ID = "dsr01"
ROBOT_MODEL = "m0609"

DR_init.__dsr__id = ROBOT_ID
DR_init.__dsr__model = ROBOT_MODEL

ON, OFF = 1, 0

# UI/robot topic names. Change these constants if the integration topic names change.
MENU_TOPIC = "/ui/menu_command"          # bartender_interfaces/Menu, UI -> robot
STATUS_TOPIC = "/robot/process_state"    # bartender_interfaces/Status, robot -> UI
ESTOP_TOPIC = "/ui/emergency_stop"       # std_msgs/Bool, UI -> robot

# Admin bridge topics. The Flask admin UI publishes to /ui/admin_control,
# the bridge node forwards operational commands to these robot-side topics.
TASK_CONTROL_TOPIC = f"/{ROBOT_ID}/task_control"             # std_msgs/String: PAUSE, RESUME, HOME_RETURN, legacy CANCEL
RECOVERY_TOPIC = f"/{ROBOT_ID}/recovery_command"             # std_msgs/String: RECOVER
MONITOR_STATUS_TOPIC = f"/{ROBOT_ID}/robot_monitor_status"   # std_msgs/String(JSON), robot -> admin bridge/UI

# Doosan hardware state codes returned by get_robot_state().
STATE_ROBOT_STANDBY = 1
STATE_ROBOT_MOVING = 2
STATE_ROBOT_SAFE_OFF = 3
STATE_ROBOT_PROTECTIVE_STOP = 5
STATE_ROBOT_EMERGENCY_STOP = 6

# Doosan SetRobotControl command values used by the previous flower robot recovery code.
CONTROL_RESET_SAFE_STOP = 2
CONTROL_RESET_SAFE_OFF = 3

VELOCITY = 200
ACC = 200
SLOW_VELOCITY = 200
SLOW_ACC = 200

DISPENSE_HOLD_S = 4.0
POUR_HOLD_S = 2.0
SHAKE_REPEAT = 3

# Nudge-style external force stop.
# If the measured TCP/tool force magnitude exceeds this threshold while the robot is
# performing normal motion, the current motion is stopped through /dsr01/motion/move_stop.
NUDGE_FORCE_THRESHOLD_N = 20.0
NUDGE_FORCE_CHECK_PERIOD_S = 0.05
NUDGE_TRIGGER_COUNT = 2
NUDGE_STOP_COOLDOWN_S = 1.0

class BartenderRobot:
    def __init__(self, node, command_node=None):
        self.node = node
        # DSR_ROBOT2 motion APIs use self.node. UI/admin command subscriptions are
        # handled by a separate ROS node so PAUSE/CANCEL/RECOVER can be received
        # while the DSR node is executing blocking motion calls.
        self.command_node = command_node or node
        self.lock = threading.Lock()
        self.task_running = False
        self.cancel_requested = False
        self.home_return_running = False
        self.home_return_requested = False
        # True이면 소프트 비상정지 후 해제/홈복귀 명령을 기다리는 상태입니다.
        self.emergency_stopped = False
        self.pause_requested = False
        self.recovery_approved = False
        self.waiting_recovery = False
        self.recovery_lock = threading.Lock()
        self.hw_lock = threading.Lock()
        self.hw_code = STATE_ROBOT_STANDBY
        self._handling_hw_stop = False
        # /ui/emergency_stop can be published by more than one legacy source.
        # Keep the action idempotent so a single UI click cannot start duplicate stop/home flows.
        self._last_estop_value = None
        self._last_estop_time = 0.0
        # Periodic status heartbeat for the admin bridge. This does NOT call DSR APIs.
        self._monitor_stop_event = threading.Event()
        self._monitor_thread = None
        # Nudge/external-force monitoring. get_tool_force() is checked in a separate
        # guard thread so external contact can stop the motion before the next recipe step.
        self._nudge_stop_event = threading.Event()
        self._nudge_thread = None
        self._nudge_above_count = 0
        self._nudge_api_error_logged = False
        self._nudge_suppress_count = 0
        self.nudge_stop_requested = False
        self.last_nudge_force_n = 0.0
        self.pending_menu = None
        self.current_status = Status.WAITING

        self.status_pub = self.command_node.create_publisher(Status, STATUS_TOPIC, 10)
        self.monitor_status_pub = self.command_node.create_publisher(String, MONITOR_STATUS_TOPIC, 10)
        self.move_stop_client = self.command_node.create_client(MoveStop, f"/{ROBOT_ID}/motion/move_stop")
        self.set_robot_control_client = self.command_node.create_client(SetRobotControl, f"/{ROBOT_ID}/system/set_robot_control")
        self.gripper_command_client = self.command_node.create_client(SetCommand, "/onrobot/sendCommand")
        self.menu_sub = None
        self.estop_sub = None
        self.task_control_sub = None
        self.recovery_sub = None
        self.create_ui_subscriptions()

        self.configure_tool()
        self.define_positions()
        self.install_motion_wrappers()
        self.start_monitor_heartbeat()
        self.start_nudge_monitor()
        self.publish_status(Status.WAITING)
        self.publish_monitor_status("Bartender robot node started.", "OK")
        self.get_logger().info("Bartender robot node started.")

    def get_logger(self):
        return self.node.get_logger()

    def start_monitor_heartbeat(self):
        if self._monitor_thread is not None:
            return
        self._monitor_thread = threading.Thread(
            target=self._monitor_heartbeat_loop,
            daemon=True,
            name="bartender_monitor_heartbeat",
        )
        self._monitor_thread.start()

    def _monitor_heartbeat_loop(self):
        # Keep /dsr01/robot_monitor_status alive so the admin bridge does not
        # mark the robot disconnected during long blocking DSR motions.
        # Do not call get_robot_state() here; DSR state refresh stays in the
        # motion thread after mwait() to avoid generator/executor collisions.
        while not self._monitor_stop_event.is_set():
            try:
                self.publish_monitor_status()
            except Exception as exc:
                self.get_logger().warn(f"monitor heartbeat publish failed: {exc}")
            time.sleep(0.5)

    def stop_background_threads(self):
        self._monitor_stop_event.set()
        self._nudge_stop_event.set()

    def start_nudge_monitor(self):
        if self._nudge_thread is not None:
            return
        self._nudge_thread = threading.Thread(
            target=self._nudge_monitor_loop,
            daemon=True,
            name="bartender_nudge_force_guard",
        )
        self._nudge_thread.start()

    def is_nudge_suppressed(self):
        with self.lock:
            return self._nudge_suppress_count > 0

    def set_nudge_suppressed(self, suppressed, reason=""):
        with self.lock:
            if suppressed:
                self._nudge_suppress_count += 1
            else:
                self._nudge_suppress_count = max(0, self._nudge_suppress_count - 1)
        if reason:
            state = "suppressed" if suppressed else "resumed"
            self.get_logger().info(f"Nudge force guard {state}: {reason}")

    def _nudge_monitor_loop(self):
        while not self._nudge_stop_event.is_set():
            try:
                if not self.should_monitor_nudge_force():
                    self._nudge_above_count = 0
                    time.sleep(NUDGE_FORCE_CHECK_PERIOD_S)
                    continue

                force_n = self.read_tool_force_norm()
                if force_n is None:
                    time.sleep(0.5)
                    continue

                with self.lock:
                    self.last_nudge_force_n = force_n

                if force_n >= NUDGE_FORCE_THRESHOLD_N:
                    self._nudge_above_count += 1
                else:
                    self._nudge_above_count = 0

                if self._nudge_above_count >= NUDGE_TRIGGER_COUNT:
                    self._nudge_above_count = 0
                    self.request_nudge_stop(force_n)
                    time.sleep(NUDGE_STOP_COOLDOWN_S)

            except Exception as exc:
                if not self._nudge_api_error_logged:
                    self._nudge_api_error_logged = True
                    self.get_logger().warn(f"Nudge force guard stopped reading force: {exc}")
                time.sleep(0.5)

            time.sleep(NUDGE_FORCE_CHECK_PERIOD_S)

    def should_monitor_nudge_force(self):
        with self.lock:
            return (
                (self.task_running or self.home_return_running)
                and not self.cancel_requested
                and not self.emergency_stopped
                and not self.waiting_recovery
                and self._nudge_suppress_count == 0
            )

    def read_tool_force_norm(self):
        try:
            force = get_tool_force()
        except Exception as exc:
            if not self._nudge_api_error_logged:
                self._nudge_api_error_logged = True
                self.get_logger().warn(f"get_tool_force 오류 — nudge 감시 일시 비활성: {exc}")
            return None

        try:
            values = list(force)
            fx, fy, fz = float(values[0]), float(values[1]), float(values[2])
            return math.sqrt(fx * fx + fy * fy + fz * fz)
        except Exception as exc:
            if not self._nudge_api_error_logged:
                self._nudge_api_error_logged = True
                self.get_logger().warn(f"get_tool_force 반환값 파싱 실패: {force!r}, err={exc}")
            return None

    def request_nudge_stop(self, force_n):
        with self.lock:
            if self.emergency_stopped or self.cancel_requested or self.nudge_stop_requested:
                already = True
            else:
                already = False
                self.cancel_requested = True
                self.home_return_requested = False
                self.emergency_stopped = True
                self.pause_requested = False
                self.nudge_stop_requested = True
                self.last_nudge_force_n = float(force_n)
        if already:
            return

        msg = f"Nudge 외력 감지: {force_n:.1f} N >= {NUDGE_FORCE_THRESHOLD_N:.1f} N — 모션 정지"
        self.get_logger().warn(msg)
        self.publish_monitor_status(msg, "ERROR")
        self._call_motion_stop()

    def create_ui_subscriptions(self):
        self.menu_sub = self.command_node.create_subscription(Menu, MENU_TOPIC, self.menu_callback, 10)
        self.estop_sub = self.command_node.create_subscription(Bool, ESTOP_TOPIC, self.emergency_stop_callback, 10)
        self.task_control_sub = self.command_node.create_subscription(String, TASK_CONTROL_TOPIC, self.task_control_callback, 10)
        self.recovery_sub = self.command_node.create_subscription(String, RECOVERY_TOPIC, self.recovery_callback, 10)

    def refresh_menu_subscription(self):
        # The command node spins in a background thread. Destroying/recreating
        # subscriptions from the recipe worker can race with the executor.
        # Keep the subscriber alive; menu_callback already rejects commands
        # while the robot is busy and accepts the next command after WAITING.
        self.get_logger().info("Ready for next menu command.")

    def configure_tool(self):
        set_tool("Tool_Weight11")
        set_tcp("Tool_v1")

    def define_positions(self):
        self.home = posj(0.0, 0.0, 90.0, 0.0, 90.0, 0.0)

        # 메뉴 이름은 UI 로그와 메뉴 유효성 확인에 사용합니다.
        self.dispenser = {
            Menu.WHISKEY: {
                "name": "whiskey",
            },
            Menu.VODKA: {
                "name": "vodka",
            },
            Menu.NON_ALCOHOL: {
                "name": "non_alcohol",
            },
            Menu.STRAIGHT1: {
                "name": "straight1",
            },
            Menu.STRAIGHT2: {
                "name": "straight2",
            },
            Menu.STRAIGHT3: {
                "name": "straight3",
            },
            Menu.STRAIGHT4: {
                "name": "straight4",
            },
            Menu.STRAIGHT5: {
                "name": "straight5",
            },
            Menu.STRAIGHT6: {
                "name": "straight6",
            },
        }

        # 디스펜서 버튼/트레이 좌표는 버튼 번호 기준으로 관리합니다.
        # 트레이는 처음에 1번 버튼 아래에 있다고 가정합니다.
        self.initial_tray_button = 1
        self.current_tray_button = self.initial_tray_button
        self.dispenser_buttons = {
            1: {
                "approach": posx(948.9, -233.68, 248.52, 4.66, 93.2, 94.48),
                "press": posx(1030.79, -235.07, 258.29, 1.15, 89.55, 89.1),
                "retreat": posx(931.97, -204.55, 209.84, 4.46, 90.31, 89.53),
                "container_pick": posx(1020.83, -211.86, 200.36, 1.85, 88.8, 87.56),
            },
            2: {
                "approach": posx(932.41, -172.1, 240.45, 4.78, 94.36, 90.11),
                "press": posx(1030.14, -176.83, 254.68, 2.6, 92.05, 89.83),
                "retreat": posx(931.97, -204.55, 209.84, 4.46, 90.31, 89.53),
                "container_pick": posx(1020.83, -211.86, 200.36, 1.85, 88.8, 87.56),
            },
            3: {
                "approach": posx(923.04, -115.23, 247.72, 5.99, 96.02, 91.71),
                "press": posx(1028.59, -117.83, 258.45, 2.82, 92.49, 86.45),
                "retreat": posx(920.22, -82.59, 206.91, 1.03, 90.75, 93.4),
                "container_pick": posx(1019.0, -83.27, 202.78, 0.4, 88.68, 91.73),
            },
            4: {
                "approach": posx(939.02, -57.54, 240.01, 6.04, 94.72, 86.52),
                "press": posx(1027.1, -59.74, 255.21, 4.74, 93.45, 86.85),
                "retreat": posx(920.22, -82.59, 206.91, 1.03, 90.75, 93.4),
                "container_pick": posx(1019.0, -83.27, 202.78, 0.4, 88.68, 91.73),
            },
            5: {
                "approach": posx(920.36, -0.86, 237.26, 7.33, 94.7, 91.08),
                "press": posx(1026.2, 1.64, 252.67, 4.61, 92.81, 92.33),
                "retreat": posx(903.7, 34.13, 208.03, 3.69, 91.36, 90.2),
                "container_pick": posx(1009.43, 36.22, 192.87, 3.05, 92.31, 93.7),
            },
            6: {
                "approach": posx(927.61, 63.81, 249.49, 6.54, 93.42, 90.18),
                "press": posx(1025.18, 59.49, 249.3, 4.76, 93.38, 89.35),
                "retreat": posx(903.7, 34.13, 208.03, 3.69, 91.36, 90.2),
                "container_pick": posx(1009.43, 36.22, 192.87, 3.05, 92.31, 93.7),
            },
        }

        # 메뉴별 제조에 필요한 재료 버튼 순서입니다.
        # 예: 보드카는 4번 버튼 재료를 붓고, 이어서 5번 버튼 재료를 붓습니다.
        self.recipes = {
            Menu.WHISKEY: [1],
            Menu.VODKA: [4, 5],
            Menu.NON_ALCOHOL: [5, 6],
        }

        # 스트레이트 메뉴는 디스펜서 버튼 1~6에 각각 1:1로 대응합니다.
        # 컵 위치는 별도로 나누지 않고 기존 whiskey cup 좌표를 공통 사용합니다.
        self.straight_recipes = {
            Menu.STRAIGHT1: 1,
            Menu.STRAIGHT2: 2,
            Menu.STRAIGHT3: 3,
            Menu.STRAIGHT4: 4,
            Menu.STRAIGHT5: 5,
            Menu.STRAIGHT6: 6,
        }

        # 쉐이킹 컵과 관련된 좌표들
        # 메뉴별 나뉘어있지만 좌표는 동일(같은 쉐이킹 컵에서 쉐이킹을 수행)
        self.containers = {
            Menu.WHISKEY: {
                "name": "whiskey_cup",
                "pour_approach": posx(601.6, 103.68, 201.12, 45.71, 94.31, 88.21),
                "pour_pose": posx(666.22, 174.31, 217.0, 34.48, 91.7, 92.24),
                "pour_tilt": posx(633.32, 169.02, 220.61, 39.72, 123.41, 93.8),
                "pick_approach": posx(421.0, 307.39, 150.88, 85.42, 89.8, -91.11),
                "pick": posx(421.0, 357.39, 150.88, 85.42, 89.8, -91.11),
            },
            Menu.VODKA: {
                "name": "vodka_cup",
                "pour_approach": posx(601.6, 103.68, 201.12, 45.71, 94.31, 88.21),
                "pour_pose": posx(666.22, 174.31, 217.0, 34.48, 91.7, 92.24),
                "pour_tilt": posx(633.32, 169.02, 220.61, 39.72, 123.41, 93.8),
                "pick_approach": posx(421.0, 307.39, 150.88, 85.42, 89.8, -91.11),
                "pick": posx(421.0, 357.39, 150.88, 85.42, 89.8, -91.11),
                
            },
            Menu.NON_ALCOHOL: {
                "name": "non_alcohol_cup",
                "pour_approach": posx(601.6, 103.68, 201.12, 45.71, 94.31, 88.21),
                "pour_pose": posx(666.22, 174.31, 217.0, 34.48, 91.7, 92.24),
                "pour_tilt": posx(633.32, 169.02, 220.61, 39.72, 123.41, 93.8),
                "pick_approach": posx(421.0, 307.39, 150.88, 85.42, 89.8, -91.11),
                "pick": posx(421.0, 357.39, 150.88, 85.42, 89.8, -91.11),
            },
        }


        # 쉐이킹 컵과 뚜껑까지 고려한 좌표
        self.bottle_pose_up = posx(584.27, 252.36, 220.95, 56.56, 91.04, 88.28) # 뚜껑을 집기 전 위치하게 될 좌표 d
        self.bottle_pose = posx(590.37, 249.46, 77.56, 55.66, 92.93, 89.02) # 뚜껑을 집을 좌표 d

        self.shake_cup_up = posx(723.23, 236.07, 351.74, 41.91, 92.61, 89.35) # 뚜껑을 집은 후, 뚜껑을 쉐이크 컵에 끼우기 전 좌표 d
        self.shake_cup = posx(732.23, 236.07, 231.53, 41.91, 92.61, 89.35) # 뚜껑을 쉐이크 컵에 끼우는 좌표 d

        # 쉐이크 컵 및 쉐이크 동작과 관련된 좌표
        self.shake_cup_approach = posx(601.6, 103.68, 201.12, 45.71, 94.31, 88.21) # 트레이를 쥔 상태로, 트레이에 담긴 액체를 쉐이킹컵에 붓기 위해 다가갈 좌표 d
        self.shake_cup_pour_pose = posx(666.22, 174.31, 217.0, 34.48, 91.7, 92.24) # 쉐이킹 컵에 트레이에 담긴 음료를 붓기 전 좌표 d
        self.shake_cup_tilt = posx(633.32, 169.02, 220.61, 39.72, 123.41, 93.8) # 쉐이킹 컵에 트레이에 담긴 음료를 붓는 동작 좌표(기울어진 좌표) d

        self.shake_cup_pick_approach = posx(736.7, 246.28, 255.79, 51.74, 90.47, 91.53) # 쉐이킹 컵을 집기 전 다가갈 좌표 b
        self.shake_cup_pick = posx(736.7, 246.28, 155.79, 51.74, 90.47, 91.53) # 쉐이킹 컵을 집을 좌표 b
        
        self.shake_pose = posx(740.57, 60.05, 315.55, 33.33, 90.13, 88.86) # 쉐이킹을 수행하게될 좌표들, 좌표 1 b
        self.shake_pose_2 = posx(740.57, 60.05, 315.55, 33.33, 90.13, 88.86) # 쉐이킹을 수행하게될 좌표들, 좌표 2 b
        self.shake_pose_3 = posx(462.75, 392.9, 471.92, 91.77, 93.01, 93.29) # 쉐이킹을 수행하게될 좌표들, 좌표 3 b
        self.shake_pose_4 = posx(251.0, 402.12, 668.32, 93.71, 90.68, 93.15) # 쉐이킹을 수행하게될 좌표들, 좌표 4 -> 3으로

        # 손님에게 제공될 제조된 음료가 담길 컵 관련 좌표
        # self.cust_approach = posx(361.35, 186.42, 197.1, 123.2, 96.44, 90.41) # 손님컵에 음료를 붓기 전 접근하게 될 좌표 
        self.cust_pose = posx(361.35, 186.42, 197.1, 123.2, 96.44, 90.41) # 손님에게 음료를 붓기 전 좌표 b
        self.cust_tilt = posx(377.17, 180.74, 184.03, 125.62, 137.31, 86.1) # 손님컵에 음료를 붓게 될 좌표, 추후 천천히 붓게 수정해야함 b
        self.cust_pick = posx(312.25, 265.1, 121.58, 124.23, 89.82, 90.28) # 손님컵을 집을 좌표 b
        self.cust_pick_up = posx(312.25, 265.1, 221.58, 124.23, 89.82, 90.28) # 손님컵을 집고 위로 올라갈 좌표, 코드 로직상 추가해아함, 일단 모션은 만듦

        # 손님 테이블에 접근하여 손님이 컵을 받는 상황과 관려된 좌표
        self.serve_approach = posx(388.88, 470.12, 410.23, 93.0, 91.41, 91.83) # 손님 테이블 근처에서 손님이 실제로 받기 전 좌표 b
        self.serve_pose = posx(380.69, 650.81, 445.85, 91.24, 89.36, 91.74) # 손님 테이블 근처에서 손님이 실제로 받게되는 좌표 b
        


    def publish_status(self, status):
        with self.lock:
            self.current_status = int(status)
        msg = Status()
        msg.status = int(status)
        self.status_pub.publish(msg)
        self.publish_monitor_status()
        self.get_logger().info(f"status={int(status)}")

    def menu_callback(self, msg):
        menu = int(msg.menu)
        if menu not in self.dispenser:
            self.get_logger().warn(f"Invalid menu command: {menu}")
            return

        with self.lock:
            busy = (
                self.task_running
                or self.home_return_running
                or self.pending_menu is not None
                or self.current_status != Status.WAITING
                or self.emergency_stopped
                or self.pause_requested
                or self.waiting_recovery
            )
            if busy:
                self.get_logger().warn(
                    f"Menu ignored. Robot is busy or stopped. menu={menu}, "
                    f"status={self.current_status}, estop={self.emergency_stopped}"
                )
                return
            self.cancel_requested = False
            self.pending_menu = menu

        self.get_logger().info(f"Menu accepted: {menu} ({self.dispenser[menu]['name']})")

    def take_pending_menu(self):
        with self.lock:
            menu = self.pending_menu
            self.pending_menu = None
        return menu

    def emergency_stop_callback(self, msg):
        # UI/bridge 규격:
        #   /ui/emergency_stop True  -> 즉시 소프트 비상정지
        #   /ui/emergency_stop False -> 소프트 비상정지 상태만 해제
        # 홈복귀는 /dsr01/task_control HOME_RETURN으로 별도 처리합니다.
        # Legacy UI versions could publish the same Bool directly while the
        # bridge also forwarded it. Suppress near-duplicate same-value events.
        flag = bool(msg.data)
        now = time.time()
        with self.lock:
            duplicate = (
                self._last_estop_value is not None
                and self._last_estop_value == flag
                and now - self._last_estop_time < 0.8
            )
            self._last_estop_value = flag
            self._last_estop_time = now
        if duplicate:
            self.get_logger().warn(f"Duplicate emergency_stop {flag} ignored.")
            return

        if flag:
            self.request_emergency_stop("emergency_stop topic True")
        else:
            self.request_emergency_release("emergency_stop topic False: release stop only")

    def task_control_callback(self, msg):
        command = msg.data.strip().upper()
        self.get_logger().info(f"Admin task control received: {command}")

        if command == "PAUSE":
            self.request_pause("admin UI PAUSE")
        elif command == "RESUME":
            self.request_resume("admin UI RESUME")
        elif command == "CANCEL":
            # Legacy command. The latest admin UI no longer exposes 주문취소.
            self.request_cancel_and_home("legacy admin UI CANCEL")
        elif command == "ESTOP":
            self.request_emergency_stop("admin UI ESTOP")
        elif command == "ESTOP_RELEASE":
            self.request_emergency_release("admin UI ESTOP_RELEASE")
        elif command == "HOME_RETURN":
            self.request_home_return("admin UI HOME_RETURN")
        elif command == "ESTOP_RELEASE_HOME":
            # Legacy combined command. New UI sends ESTOP_RELEASE and HOME_RETURN separately.
            self.request_emergency_release("legacy admin UI ESTOP_RELEASE_HOME")
            self.request_home_return("legacy admin UI ESTOP_RELEASE_HOME")
        elif command == "RECOVER":
            self.approve_recovery("admin UI RECOVER via task_control")
        else:
            self.get_logger().warn(f"Unknown admin task control command: {command}")
            self.publish_monitor_status(f"알 수 없는 관리자 명령: {command}", "WARN")

    def recovery_callback(self, msg):
        if msg.data.strip().upper() == "RECOVER":
            self.approve_recovery("admin UI RECOVER")

    def approve_recovery(self, reason):
        with self.recovery_lock:
            self.recovery_approved = True
        self.get_logger().info(f"Recovery approved: {reason}")
        self.publish_monitor_status("관리자 Recovery 승인 수신", "OK")

    def request_pause(self, reason):
        with self.lock:
            if self.pause_requested:
                already = True
            else:
                already = False
                self.pause_requested = True
        if already:
            self.get_logger().warn(f"Pause request ignored; already paused/requested: {reason}")
            return
        self.get_logger().warn(f"Pause requested: {reason}")
        self.publish_monitor_status("관리자 일시정지 요청 — 현재 동작 완료 후 일시정지", "WARN")

    def request_resume(self, reason):
        with self.lock:
            was_paused = self.pause_requested
            self.pause_requested = False
        if not was_paused:
            self.get_logger().info(f"Resume request received while not paused: {reason}")
            self.publish_monitor_status("관리자 재개 요청 수신 — 현재 일시정지 상태가 아닙니다", "INFO")
            return
        self.get_logger().info(f"Resume requested: {reason}")
        self.publish_monitor_status("관리자 재개 요청", "OK")

    def _call_motion_stop(self):
        if self.move_stop_client.wait_for_service(timeout_sec=0.2):
            req = MoveStop.Request()
            req.stop_mode = DR_SSTOP
            self.move_stop_client.call_async(req)
        else:
            self.get_logger().warn("motion/move_stop service is not available.")

    def request_emergency_stop(self, reason):
        # 정지만 수행합니다. 홈 복귀는 UI에서 False 해제 명령이 들어왔을 때 수행합니다.
        with self.lock:
            if self.emergency_stopped and self.cancel_requested and not self.home_return_requested:
                already = True
            else:
                already = False
                self.cancel_requested = True
                self.home_return_requested = False
                self.emergency_stopped = True
                self.pause_requested = False
        if already:
            self.get_logger().warn(f"Emergency stop ignored; already stopped: {reason}")
            return
        self.get_logger().warn(f"Emergency stop requested: {reason}")
        self.publish_monitor_status("소프트 비상정지 요청 수신", "ERROR")
        self._call_motion_stop()

    def request_emergency_release(self, reason):
        """Release only the software emergency-stop latch. No robot motion is started here."""
        with self.lock:
            was_stopped = self.emergency_stopped
            self.emergency_stopped = False
            self.cancel_requested = False
            self.pause_requested = False
            self.home_return_requested = False
            self.nudge_stop_requested = False

        if was_stopped:
            self.get_logger().info(f"Emergency stop released: {reason}")
            self.publish_monitor_status("소프트 비상정지 해제 완료 — 홈복귀는 별도 버튼으로 실행하세요", "OK")
        else:
            self.get_logger().info(f"Emergency release received while not stopped: {reason}")
            self.publish_monitor_status("비상해제 요청 수신 — 현재 소프트 비상정지 상태가 아닙니다", "INFO")

    def request_home_return(self, reason):
        """Request home return as a standalone command.

        If the software emergency-stop latch is still active, the operator must press
        비상해제 first. This keeps release and motion as two explicit admin actions.
        """
        with self.lock:
            if self.emergency_stopped:
                stopped = True
            else:
                stopped = False

        if stopped:
            self.get_logger().warn(f"Home return rejected; emergency stop is still latched: {reason}")
            self.publish_monitor_status("홈복귀 거부 — 먼저 비상해제 버튼을 누르세요", "WARN")
            return

        self.request_cancel_and_home(reason)

    def request_cancel_and_home(self, reason):
        # 홈복귀 / legacy 취소 공통 경로입니다.
        # Duplicate fallback/bridge messages must not create two home-return workers.
        with self.lock:
            if self.home_return_running or self.home_return_requested:
                already = True
                task_running = self.task_running
                home_running = self.home_return_running
            else:
                already = False
                self.cancel_requested = True
                self.home_return_requested = True
                self.emergency_stopped = False
                self.pause_requested = False
                task_running = self.task_running
                home_running = self.home_return_running

        if already:
            self.get_logger().warn(f"Cancel/home request ignored; already requested/running: {reason}")
            return

        self.get_logger().warn(f"Home return requested: {reason}")
        self.publish_monitor_status("홈복귀 요청 수신", "WARN")
        self._call_motion_stop()

        if not task_running and not home_running:
            self.start_home_return_thread(reason)

    def start_home_return_thread(self, reason):
        with self.lock:
            if self.home_return_running:
                self.get_logger().warn("Home return request ignored. Home return is already running.")
                return
            self.home_return_running = True
            # DSR motion API 호출 중에는 메인 loop가 spin_once를 돌리지 않도록 task_running도 True로 둡니다.
            self.task_running = True

        thread = threading.Thread(target=self._home_return_worker, args=(reason,), daemon=True)
        thread.start()

    def _home_return_worker(self, reason):
        try:
            self.get_logger().warn(f"Home return worker started: {reason}")
            self.safe_release_force()
            time.sleep(0.3)
            self.publish_status(Status.RETURNING_HOME)

            # 홈복귀는 취소/정지 요청을 처리하기 위한 복구 동작입니다.
            # 따라서 cancel_requested=True 상태로 safe_movej()를 타면
            # check_cancel()에서 홈복귀 자체가 즉시 차단됩니다.
            # 홈복귀 직전에는 취소/일시정지 플래그를 해제하고,
            # return_home(ignore_cancel=True)로 raw motion을 사용합니다.
            with self.lock:
                self.cancel_requested = False
                self.pause_requested = False

            self.return_home(ignore_cancel=True)
            self.get_logger().info("Returned home after emergency release.")
        except Exception as exc:
            self.get_logger().error(f"Home return worker failed: {exc}")
        finally:
            self.safe_release_force()
            self.publish_status(Status.WAITING)
            with self.lock:
                self.task_running = False
                self.cancel_requested = False
                self.home_return_requested = False
                self.home_return_running = False
                self.emergency_stopped = False
                self.nudge_stop_requested = False

    def check_cancel(self):
        self.wait_if_paused()
        with self.lock:
            canceled = self.cancel_requested
        if canceled:
            raise RuntimeError("Task canceled")

    def wait_if_paused(self):
        announced = False
        while True:
            with self.lock:
                paused = self.pause_requested
                canceled = self.cancel_requested
            if canceled:
                raise RuntimeError("Task canceled")
            if not paused:
                if announced:
                    self.publish_monitor_status("일시정지 해제 — 작업 재개", "OK")
                return
            if not announced:
                self.get_logger().warn("Task paused by admin UI. Waiting for RESUME...")
                self.publish_monitor_status("관리자 일시정지 중 — 재개 버튼 대기", "WARN")
                announced = True
            time.sleep(0.1)

    def run_recipe(self, menu):
        with self.lock:
            self.task_running = True
            self.cancel_requested = False
            self.home_return_requested = False
            self.emergency_stopped = False
            self.pause_requested = False
            self.waiting_recovery = False
            self.nudge_stop_requested = False
        # 정상 시나리오는 트레이가 항상 1번 디스펜서 아래에서 시작한다고 가정합니다.
        # 이전 작업이 정상 종료되면 아래 prepare_ingredients() 마지막에서 다시 1번으로 복귀합니다.
        self.current_tray_button = self.initial_tray_button
        try:
            if menu in self.straight_recipes:
                self.run_straight_recipe(menu)
                return

            self.publish_status(Status.MAKING)
            self.prepare_robot()
            self.prepare_ingredients(menu)
            self.bottle_to_shake_cup()
            self.shake_drink(menu)
            self.place_shake_cup_and_remove_lid()
            self.shake_to_cust()

            self.publish_status(Status.MAKING_DONE)
            self.publish_status(Status.DELIVERING)
            self.deliver_cup()

            self.publish_status(Status.DELIVERED)
            self.publish_status(Status.RETURNING_HOME)
            self.return_home()
            self.publish_status(Status.WAITING)
            self.get_logger().info("Bartender task complete.")
        except Exception as exc:
            self.get_logger().error(f"Bartender task stopped: {exc}")
            self.safe_release_force()
            with self.lock:
                go_home = self.home_return_requested
            if go_home:
                self.publish_status(Status.RETURNING_HOME)
                try:
                    # cancel/home 요청은 새 thread에서 home으로 보내지 않고,
                    # 기존 작업 thread 안에서만 motion API를 호출합니다.
                    time.sleep(0.5)

                    # 여기서도 cancel_requested=True가 유지되어 있으면
                    # return_home()이 safe wrapper의 check_cancel()에 막힙니다.
                    # 홈복귀 전용 동작에서는 취소/일시정지 플래그를 해제합니다.
                    with self.lock:
                        self.cancel_requested = False
                        self.pause_requested = False

                    self.return_home(ignore_cancel=True)
                    self.get_logger().info("Canceled task and returned home.")
                except Exception as home_exc:
                    self.get_logger().error(f"Return home after cancel failed: {home_exc}")
            else:
                self.get_logger().warn("Task stopped by emergency stop. Waiting for emergency release/home command.")
            self.publish_status(Status.WAITING)
        finally:
            with self.lock:
                self.task_running = False
                self.cancel_requested = False
                self.home_return_requested = False
                self.home_return_running = False

    def run_straight_recipe(self, menu):
        # 스트레이트 메뉴는 재료를 섞지 않고, 지정된 디스펜서 1개만 눌러 바로 제공합니다.
        # 디스펜서/트레이 위치는 메뉴마다 다르지만, 손님컵 좌표는 cust_* 좌표를 공통 사용합니다.
        button_id = self.straight_recipes[menu]

        self.publish_status(Status.MAKING)
        self.prepare_robot()

        # 1번 스트레이트는 트레이가 이미 1번 아래에 있으므로 move_tray_to_button()에서 이동이 생략됩니다.
        # 그 외 스트레이트는 1번 위치의 트레이를 해당 디스펜서 아래로 옮깁니다.
        self.move_tray_to_button(button_id)

        # 스트레이트 메뉴 번호에 대응되는 디스펜서 버튼을 눌러 재료를 트레이에 담습니다.
        self.press_dispenser_button(button_id)

        # 재료가 담긴 트레이를 집고, 쉐이킹 컵이 아니라 손님용 컵에 바로 붓습니다.
        self.pick_container_from_button(button_id)
        movel(self.cust_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        movel(self.cust_tilt, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.wait_with_cancel(POUR_HOLD_S)
        movel(self.cust_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        movel(self.cust_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()

        # 스트레이트는 트레이를 해당 스트레이트 원래 위치에 다시 놓습니다.
        self.place_container_to_button(button_id)

        # 컵을 잡고 흔들지 않은 상태로 바로 손님 제공 위치로 이동합니다.
        movel(self.cust_pose, vel=VELOCITY, acc=ACC)
        mwait()
        movel(self.cust_pick, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.grip_cup()
        movel(self.cust_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.check_cancel()

        self.publish_status(Status.MAKING_DONE)
        self.publish_status(Status.DELIVERING)
        self.deliver_cup()

        self.publish_status(Status.DELIVERED)
        self.publish_status(Status.RETURNING_HOME)
        self.return_home()
        self.publish_status(Status.WAITING)
        self.get_logger().info("Straight bartender task complete.")

    def prepare_robot(self):
        self.get_logger().info("Prepare robot: home and close gripper.")
        movej(self.home, vel=VELOCITY, acc=ACC)
        mwait()
        self.grip()
        wait(0.3)
        self.check_cancel()

    def prepare_ingredients(self, menu):
        recipe = self.recipes[menu]
        self.get_logger().info(f"Prepare ingredients for {self.dispenser[menu]['name']}: {recipe}")

        for index, button_id in enumerate(recipe):
            # 첫 재료 전에는 1번 위치에 있는 트레이를 해당 버튼 아래로 옮깁니다.
            # 첫 재료가 1번이면 이미 그 위치에 있으므로 이동을 생략합니다.
            # 이후 재료부터는 직전 pour 후 들고 있는 트레이를 다음 버튼 아래에 내려놓습니다.
            if index == 0:
                self.move_tray_to_button(button_id)
            else:
                self.place_container_to_button(button_id)

            self.press_dispenser_button(button_id)
            self.pick_container_from_button(button_id)
            self.pour_container_to_cup(menu)

        # 모든 재료를 컵에 부은 뒤, 마지막으로 들고 있는 트레이를 초기 위치에 반납합니다.
        self.place_container_to_button(self.initial_tray_button)

    def move_tray_to_button(self, target_button_id):
        # 트레이가 이미 목표 디스펜서 아래에 있으면 불필요한 pick/place 동작을 하지 않습니다.
        if self.current_tray_button == target_button_id:
            self.get_logger().info(f"Tray is already at dispenser {target_button_id}.")
            return

        # 현재 위치의 트레이를 집어서 목표 디스펜서 아래로 이동합니다.
        self.get_logger().info(f"Move tray: {self.current_tray_button} -> {target_button_id}")
        self.pick_container_from_button(self.current_tray_button)
        self.place_container_to_button(target_button_id)

    def press_dispenser_button(self, button_id):
        # 버튼 번호만 넘기면 해당 버튼의 접근 좌표와 누르는 좌표를 찾아 공통 누름 동작을 수행합니다.
        poses = self.dispenser_buttons[button_id]
        self.press_one_dispenser(poses["approach"], poses["press"], f"ingredient_{button_id}")

    def press_one_dispenser(self, approach_pose, press_pose, step_name):
        # 하나의 디스펜서 버튼을 누르는 공통 동작입니다.
        # approach_pose: 버튼을 누르기 전 안전하게 접근하는 위치
        # press_pose: 실제 버튼을 누르는 위치
        # 버튼 위치까지 간 뒤, Z축 방향 힘제어로 일정 시간 눌러 재료를 배출합니다.
        self.get_logger().info(f"Press dispenser step: {step_name}")
        self.grip()
        movel(approach_pose, vel=VELOCITY, acc=ACC)
        mwait()
        movel(press_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        wait(1.5)
        self.check_cancel()

        # 로봇팔을 compliance 모드로 바꿔 버튼을 위치로만 누르지 않고
        # 아래 방향(-Z)으로 일정한 힘을 주며 누르게 합니다.
        # 버튼 위치 오차가 조금 있어도 과도한 충격을 줄이는 목적입니다.
        self.set_nudge_suppressed(True, "intentional dispenser force press")
        try:
            task_compliance_ctrl()
            set_stiffnessx([300.0, 300.0, 300.0, 200.0, 200.0, 200.0])
            wait(0.3)
            set_desired_force([40.0, 0.0, 0.0, 0.0, 0.0, 0.0], [1, 0, 0, 0, 0, 0])
            self.wait_with_cancel(DISPENSE_HOLD_S)
        finally:
            self.safe_release_force()
            self.set_nudge_suppressed(False, "intentional dispenser force press")

        # 재료 배출이 끝나면 다시 접근 위치로 빠져나와 다음 동작을 준비합니다.
        movel(approach_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()

    def pick_container_from_button(self, button_id):
        # 지정된 디스펜서 아래에 놓인 트레이/컨테이너를 집습니다.
        # retreat 위치에서 안전하게 접근한 뒤 container_pick 위치로 내려가 grip합니다.
        poses = self.dispenser_buttons[button_id]
        retreat_pose = poses["retreat"]
        container_pick_pose = poses["container_pick"]
        self.get_logger().info(f"Pick container from dispenser {button_id}.")

        movel(retreat_pose, vel=VELOCITY, acc=ACC)
        mwait()
        self.ungrip()
        wait(1.0)
        movel(container_pick_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.grip_cup()
        wait(1.0)
        movel(retreat_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.check_cancel()

    def pour_container_to_cup(self, menu):
        cup = self.containers[menu]
        self.get_logger().info(f"Pour container contents to {cup['name']}.")
        movel(cup["pour_approach"], vel=VELOCITY, acc=ACC)
        mwait()
        movel(cup["pour_pose"], vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        movel(cup["pour_tilt"], vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.wait_with_cancel(POUR_HOLD_S)
        movel(cup["pour_pose"], vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()

    def place_container_to_button(self, button_id):
        # 현재 들고 있는 트레이/컨테이너를 지정된 디스펜서 아래에 내려놓습니다.
        # 내려놓은 뒤 current_tray_button을 갱신해서 다음 재료 이동 판단에 사용합니다.
        poses = self.dispenser_buttons[button_id]
        retreat_pose = poses["retreat"]
        container_pick_pose = poses["container_pick"]
        self.get_logger().info(f"Place container to dispenser {button_id}.")

        movel(retreat_pose, vel=VELOCITY, acc=ACC)
        mwait()
        movel(container_pick_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.ungrip()
        wait(1.0)
        movel(retreat_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.current_tray_button = button_id
        self.check_cancel()

    def pick_customer_cup(self, menu):
        cup = self.containers[menu]
        self.get_logger().info(f"Pick customer cup: {cup['name']}.")
        movel(cup["pick_approach"], vel=VELOCITY, acc=ACC)
        mwait()
        movel(cup["pick"], vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.grip_cup()
        wait(1.0)
        movel(cup["pick_approach"], vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.check_cancel()

    # 뚜껑 잡을때 위에서 잡아야됨 
    def bottle_to_shake_cup(self):
        self.get_logger().info("Attach lid to shake cup, then pick shake cup.")

        # 뚜껑 보관 위치에서 뚜껑을 집습니다.
        movel(self.bottle_pose_up, vel=VELOCITY, acc=ACC)
        mwait()
        self.ungrip()
        movel(self.bottle_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.grip_cup()
        movel(self.bottle_pose_up, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()

        # 쉐이킹 컵 위로 이동해 뚜껑을 끼웁니다.
        movel(self.shake_cup_up, vel=VELOCITY, acc=ACC)
        mwait()
        movel(self.shake_cup, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.set_nudge_suppressed(True, "intentional lid press force")
        try:
            task_compliance_ctrl()
            set_stiffnessx([300.0, 300.0, 300.0, 200.0, 200.0, 200.0])
            wait(0.3)
            set_desired_force([0.0, 0.0, -20.0, 0.0, 0.0, 0.0], [0, 0, 1, 0, 0, 0])
            self.wait_with_cancel(1.0)
        finally:
            self.safe_release_force()
            self.set_nudge_suppressed(False, "intentional lid press force")
        self.ungrip()

        # 뚜껑이 끼워진 쉐이킹 컵 자체를 다시 집습니다.
        movel(self.shake_cup_pick, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.grip_cup()
        movel(self.shake_cup_pick_approach, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.check_cancel()

    def place_shake_cup_and_remove_lid(self):
        self.get_logger().info("Place shake cup and remove lid.")

        # 쉐이킹 컵을 원래 위치에 내려놓습니다.
        movel(self.shake_cup_pick_approach, vel=VELOCITY, acc=ACC)
        mwait()
        movel(self.shake_cup_pick, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.ungrip()

        # 컵 위의 뚜껑을 잡아 위로 들어 올립니다.
        movel(self.shake_cup, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.grip_cup()
        movel(self.shake_cup_up, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()

        # 뚜껑을 원래 뚜껑 보관 위치에 내려놓습니다.
        movel(self.bottle_pose_up, vel=VELOCITY, acc=ACC)
        mwait()
        movel(self.bottle_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.ungrip()
        movel(self.bottle_pose_up, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.check_cancel()

    def shake_to_cust(self):
        self.get_logger().info("Pour shake cup contents to customer cup, then pick customer cup.")

        # 뚜껑이 제거된 쉐이킹 컵을 다시 집습니다.
        movel(self.shake_cup_pick_approach, vel=VELOCITY, acc=ACC)
        mwait()
        movel(self.shake_cup_pick, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.grip_cup()
        movel(self.shake_cup_pick_approach, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()

        # 쉐이킹 컵 안의 음료를 손님용 컵에 붓습니다.
        movel(self.cust_pose, vel=VELOCITY, acc=ACC)
        mwait()
        movel(self.cust_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        movel(self.cust_tilt, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.wait_with_cancel(POUR_HOLD_S)
        movel(self.cust_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()

        # 빈 쉐이킹 컵을 원래 위치에 내려놓습니다.
        movel(self.cust_pose, vel=VELOCITY, acc=ACC)
        mwait()
        movel(self.shake_cup_pick_approach, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        movel(self.shake_cup_pick, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.ungrip()
        mwait()
        movel(self.shake_cup_pick_approach, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()

        # 손님용 컵을 집어서 서빙 단계로 넘깁니다.
        movel(self.cust_pose, vel=VELOCITY, acc=ACC)
        mwait()
        movel(self.cust_pick, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.grip_cup()
        movel(self.cust_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.check_cancel()

    def shake_drink(self, menu):
        self.get_logger().info(f"Shake drink: {self.dispenser[menu]['name']}.")
        movel(self.shake_pose, vel=VELOCITY, acc=ACC)
        mwait()

        if menu == Menu.WHISKEY:
            # 위스키는 기존 방식대로 base 좌표계 기준 X-Y 원형 moveb 모션을 수행합니다.
            radius = 20.0
            circle_path = []
            for _ in range(5):
                circle_path += [
                    posb(DR_CIRCLE, posx(radius, 0, 0, 0, 0, 0), posx(0, -radius, 0, 0, 0, 0), radius=10),
                    posb(DR_CIRCLE, posx(-radius, 0, 0, 0, 0, 0), posx(0, radius, 0, 0, 0, 0), radius=10),
                ]
            moveb(circle_path, vel=100, acc=100, ref=DR_BASE, mod=DR_MV_MOD_REL)
            mwait()

        elif menu == Menu.VODKA:
            # 보드카는 같은 원형 moveb 모션을 tool 좌표계 기준으로 수행합니다.
            # 컵을 잡은 TCP 기준으로 원형 흔들림을 만들고 싶을 때 사용합니다.
            radius = 20.0
            circle_path = []
            for _ in range(5):
                circle_path += [
                    posb(DR_CIRCLE, posx(radius, 0, 0, 0, 0, 0), posx(0, -radius, 0, 0, 0, 0), radius=10),
                    posb(DR_CIRCLE, posx(-radius, 0, 0, 0, 0, 0), posx(0, radius, 0, 0, 0, 0), radius=10),
                ]
            moveb(circle_path, vel=100, acc=100, ref=DR_TOOL, mod=DR_MV_MOD_REL)
            mwait()

        elif menu == Menu.NON_ALCOHOL:
            # 논알콜은 shake.py에서 검증한 3단계 조인트 쉐이크 시퀀스를 그대로 사용합니다.
            movel(self.shake_pose_2, vel=VELOCITY, acc=ACC)
            mwait()

            j_up = posj(-2.16, 16.33, 88.87, -235.37, 48.80, 144.67)
            j_down = posj(-3.29, 15.64, 89.52, -274.21, 40.16, 166.18)
            for _ in range(SHAKE_REPEAT):
                movej(j_up, vel=200, acc=200, radius=30)
                movej(j_down, vel=200, acc=200, radius=30)
            mwait()

            movel(self.shake_pose_3, vel=VELOCITY, acc=ACC)
            mwait()

            j_up = posj(5.38, 16.86, 86.96, 102.98, 89.94, 166.19)
            j_down = posj(4.78, 16.87, 87.56, 74.98, 85.96, 166.99)
            for _ in range(SHAKE_REPEAT):
                movej(j_up, vel=50, acc=50, radius=30)
                movej(j_down, vel=50, acc=50, radius=30)
            mwait()

            movel(self.shake_pose_4, vel=VELOCITY, acc=ACC)
            mwait()

            j_up = posj(6.93, -22.50, 90.57, 104.06, 78.47, 202.39)
            j_down = posj(6.92, -22.50, 90.54, 63.22, 78.52, 202.38)
            for _ in range(SHAKE_REPEAT):
                movej(j_up, vel=400, acc=300, radius=30)
                movej(j_down, vel=400, acc=300, radius=30)
            mwait()

        movel(self.shake_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.check_cancel()

    def deliver_cup(self):
        self.get_logger().info("Deliver cup to service position.")
        movel(self.serve_approach, vel=VELOCITY, acc=ACC)
        mwait()
        movel(self.serve_pose, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()
        self.ungrip()
        mwait()
        movel(self.serve_approach, vel=SLOW_VELOCITY, acc=SLOW_ACC)
        mwait()

    def return_home(self, ignore_cancel=False):
        self.get_logger().info("Return home.")

        if ignore_cancel and hasattr(self, "_raw_movej") and hasattr(self, "_raw_mwait"):
            self.publish_monitor_status("홈 포지션 복귀 시작", "WARN")
            self.set_hw_code(STATE_ROBOT_MOVING)
            self.publish_monitor_status()

            self._raw_movej(self.home, vel=VELOCITY, acc=ACC)
            self._raw_mwait()

            self.check_hw_after_motion()
            self.publish_monitor_status("홈 포지션 복귀 완료", "OK")
            return

        movej(self.home, vel=VELOCITY, acc=ACC)
        mwait()

    def grip(self):
        """완전히 닫힘(1자) — 버튼 누르는 용도. DO3만 ON.
        ★ 핵심: '모든 DO OFF' 순간을 만들면 안 됨 (웹로직이 기본 상태로 오작동할 수 있음).
        원하는 DO 먼저 ON → 나머지 OFF 순서로 항상 규칙 하나는 매칭된 상태 유지.
        """
        self.get_logger().info("Grip operation: close fully (DO3)")
        # ① 원하는 DO 먼저 ON
        set_digital_output(3, ON)
        # ② 나머지 OFF
        set_digital_output(1, OFF)
        set_digital_output(2, OFF)
        # ③ RViz 시각화 (선택)
        self.send_virtual_gripper_command("0.785398")
        # ④ time.sleep 으로 확실히 대기 (두산 wait 은 파이썬 스크립트에서 안 먹음)
        time.sleep(1.5)

    def grip_cup(self):
        """컵/트레이 잡기 — 외력 감지형. DO1만 ON, 10mm 목표 5N 힘 상한."""
        self.get_logger().info("Grip cup operation: close to 10mm with force control 5N (DO1)")
        # ① DO1 먼저 ON (규칙 #0 = 10mm 5N 즉시 트리거)
        set_digital_output(1, ON)
        # ② 나머지 OFF ('모두 OFF' 순간을 안 만듦)
        set_digital_output(2, OFF)
        set_digital_output(3, OFF)
        self.send_virtual_gripper_command("0.650000")
        # ③ 외력 감지 완료까지 충분히 대기
        time.sleep(1.8)

    def ungrip(self):
        """열기 — DO2만 ON, 100mm 로 벌림."""
        self.get_logger().info("Ungrip operation: open to 100mm (DO2)")
        # ① DO2 먼저 ON
        set_digital_output(2, ON)
        # ② 나머지 OFF
        set_digital_output(1, OFF)
        set_digital_output(3, OFF)
        self.send_virtual_gripper_command("o")
        time.sleep(1.5)

    def set_gripper_outputs(self, output1, output2, output3):
        """(옛 방식, 이제 안 씀. 참고용으로 남김)
        그리퍼 함수에서 DO 를 순차 세팅하는데, 이 방식은 중간에 신호 충돌 가능.
        새 코드는 grip/grip_cup/ungrip 안에서 직접 순서 제어함.
        """
        set_digital_output(1, output1)
        set_digital_output(2, output2)
        set_digital_output(3, output3)

    def send_virtual_gripper_command(self, command):
        if not self.gripper_command_client.wait_for_service(timeout_sec=0.2):
            self.get_logger().warn("/onrobot/sendCommand service is not available. Skip RViz gripper command.")
            return
        req = SetCommand.Request()
        req.command = command
        self.gripper_command_client.call_async(req)

    def wait_with_cancel(self, duration_s):
        end_time = time.time() + duration_s
        while time.time() < end_time:
            self.check_cancel()
            time.sleep(0.05)

    def install_motion_wrappers(self):
        """Route existing movej/movel/moveb/mwait calls through safety-aware wrappers.

        The original recipe code can stay readable while every motion completion checks
        Doosan hardware state and enters recovery flow for SAFE_OFF / PROTECTIVE_STOP /
        EMERGENCY_STOP.
        """
        global movej, movel, moveb, mwait
        self._raw_movej = movej
        self._raw_movel = movel
        self._raw_moveb = moveb
        self._raw_mwait = mwait
        movej = self.safe_movej
        movel = self.safe_movel
        moveb = self.safe_moveb
        mwait = self.safe_mwait

    def set_hw_code(self, code):
        with self.hw_lock:
            self.hw_code = int(code)

    def get_hw_code(self):
        with self.hw_lock:
            return int(self.hw_code)

    def hw_state_name(self, code=None):
        code = self.get_hw_code() if code is None else int(code)
        return {
            STATE_ROBOT_STANDBY: "STANDBY",
            STATE_ROBOT_MOVING: "MOVING",
            STATE_ROBOT_SAFE_OFF: "SAFE_OFF",
            STATE_ROBOT_PROTECTIVE_STOP: "PROT_STOP",
            STATE_ROBOT_EMERGENCY_STOP: "EMRG_STOP",
        }.get(code, "UNKNOWN")

    def refresh_hw(self):
        try:
            code = int(get_robot_state())
            self.set_hw_code(code)
            return code
        except Exception as exc:
            self.get_logger().warn(f"get_robot_state 오류: {exc}")
            return self.get_hw_code()

    def publish_monitor_status(self, log_msg="", log_level="INFO", countdown=0, waiting_recovery=None):
        if waiting_recovery is None:
            waiting_recovery = self.waiting_recovery
        with self.lock:
            if self.pause_requested:
                fsm = "PAUSED"
            elif self.task_running or self.home_return_running:
                fsm = "BASIC"
            else:
                fsm = "IDLE"
            current_status = int(self.current_status)
            task_running = bool(self.task_running)
            paused = bool(self.pause_requested)
            emergency_stopped = bool(self.emergency_stopped)
            nudge_stop_requested = bool(self.nudge_stop_requested)
            last_nudge_force_n = float(self.last_nudge_force_n)
        hw_code = self.get_hw_code()
        msg = String()
        msg.data = json.dumps({
            "fsm": fsm,
            "hw_code": hw_code,
            "hw_state": self.hw_state_name(hw_code),
            "process_status": current_status,
            "task_running": task_running,
            "paused": paused,
            "emergency_stopped": emergency_stopped,
            "nudge_stop_requested": nudge_stop_requested,
            "last_nudge_force_n": round(last_nudge_force_n, 2),
            "nudge_threshold_n": NUDGE_FORCE_THRESHOLD_N,
            "cur_flower": 0,
            "done": 0,
            "total": 0,
            "resume_idx": 0,
            "countdown": int(countdown),
            "log": log_msg,
            "log_level": log_level,
            "waiting_recovery": bool(waiting_recovery),
        }, ensure_ascii=False)
        self.monitor_status_pub.publish(msg)

    def safe_movej(self, *args, **kwargs):
        self.check_cancel()
        self.set_hw_code(STATE_ROBOT_MOVING)
        self.publish_monitor_status()
        return self._raw_movej(*args, **kwargs)

    def safe_movel(self, *args, **kwargs):
        self.check_cancel()
        self.set_hw_code(STATE_ROBOT_MOVING)
        self.publish_monitor_status()
        return self._raw_movel(*args, **kwargs)

    def safe_moveb(self, *args, **kwargs):
        self.check_cancel()
        self.set_hw_code(STATE_ROBOT_MOVING)
        self.publish_monitor_status()
        return self._raw_moveb(*args, **kwargs)

    def safe_mwait(self, *args, **kwargs):
        result = None
        try:
            result = self._raw_mwait(*args, **kwargs)
        except Exception as exc:
            self.get_logger().warn(f"mwait/motion interrupted: {exc}")
        self.check_hw_after_motion()
        self.check_cancel()
        return result

    def check_hw_after_motion(self):
        if self._handling_hw_stop:
            return
        code = self.refresh_hw()
        if code == STATE_ROBOT_STANDBY:
            self.publish_monitor_status()
            return
        if code == STATE_ROBOT_MOVING:
            self.publish_monitor_status()
            return
        if code == STATE_ROBOT_PROTECTIVE_STOP:
            self.handle_protective_stop()
        elif code == STATE_ROBOT_EMERGENCY_STOP:
            self.handle_emergency_stop()
        elif code == STATE_ROBOT_SAFE_OFF:
            self.handle_safe_off()
        else:
            self.publish_monitor_status(f"알 수 없는 로봇 상태 감지: hw_code={code}", "WARN")

    def wait_for_recovery_approval(self, stop_label):
        with self.recovery_lock:
            self.recovery_approved = False
        self.waiting_recovery = True
        msg = f"{stop_label} 감지 — 관리자 UI에서 Recovery 버튼을 눌러 주세요"
        self.get_logger().warn(msg)
        while True:
            self.publish_monitor_status(msg, "WARN", waiting_recovery=True)
            with self.recovery_lock:
                approved = self.recovery_approved
                if approved:
                    self.recovery_approved = False
            if approved:
                break
            with self.lock:
                canceled = self.cancel_requested
            if canceled:
                self.waiting_recovery = False
                raise RuntimeError("Task canceled during recovery wait")
            time.sleep(0.2)
        self.waiting_recovery = False
        self.publish_monitor_status("Recovery 승인 확인 — 복구 절차 시작", "INFO", waiting_recovery=False)

    def call_set_robot_control(self, control_value):
        if not self.set_robot_control_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().error(f"/{ROBOT_ID}/system/set_robot_control 서비스를 찾을 수 없습니다.")
            return False
        req = SetRobotControl.Request()
        req.robot_control = int(control_value)
        future = self.set_robot_control_client.call_async(req)
        start_wait = time.time()
        while not future.done():
            if time.time() - start_wait > 5.0:
                self.get_logger().error("SetRobotControl 서비스 호출 시간 초과")
                return False
            time.sleep(0.02)
        try:
            return bool(future.result().success)
        except Exception as exc:
            self.get_logger().error(f"SetRobotControl 서비스 호출 실패: {exc}")
            return False

    def recover_servo(self, control_cmd, log_msg):
        self.get_logger().warn(log_msg)
        self.publish_monitor_status(log_msg, "WARN")
        try:
            drl_script_stop(DR_QSTOP_STO)
        except Exception:
            pass
        time.sleep(1.0)
        ok = self.call_set_robot_control(control_cmd)
        if not ok:
            self.publish_monitor_status("복구 실패 — SetRobotControl 호출 실패", "ERROR", waiting_recovery=True)
            return False
        time.sleep(3.0)
        code = self.refresh_hw()
        if code == STATE_ROBOT_STANDBY:
            self.publish_monitor_status("복구 완료 — STANDBY 상태", "OK")
            return True
        self.publish_monitor_status(f"복구 후에도 정상 상태가 아닙니다. hw_code={code}", "ERROR", waiting_recovery=True)
        return False

    def handle_protective_stop(self):
        self._handling_hw_stop = True
        try:
            self.wait_for_recovery_approval("안전정지(PROTECTIVE_STOP)")
            self.recover_servo(CONTROL_RESET_SAFE_STOP, "안전정지(5) 해제 시도")
            while self.refresh_hw() == STATE_ROBOT_PROTECTIVE_STOP:
                time.sleep(0.1)
            self.publish_monitor_status("안전정지 해제 완료 — 작업 재개", "OK")
        finally:
            self._handling_hw_stop = False

    def handle_safe_off(self):
        self._handling_hw_stop = True
        try:
            self.wait_for_recovery_approval("서보 꺼짐(SAFE_OFF)")
            self.recover_servo(CONTROL_RESET_SAFE_OFF, "서보 꺼짐(3) 복구 시도")
            while self.refresh_hw() == STATE_ROBOT_SAFE_OFF:
                time.sleep(0.1)
            self.publish_monitor_status("서보 복구 완료 — 작업 재개", "OK")
        finally:
            self._handling_hw_stop = False

    def handle_emergency_stop(self):
        self._handling_hw_stop = True
        try:
            self.publish_monitor_status("비상정지(6) — E-Stop 버튼을 먼저 해제해 주세요", "ERROR", waiting_recovery=False)
            while self.refresh_hw() == STATE_ROBOT_EMERGENCY_STOP:
                time.sleep(0.1)
            self.wait_for_recovery_approval("비상정지(E-STOP) 버튼 해제 후")
            self.recover_servo(CONTROL_RESET_SAFE_OFF, "비상정지 해제 후 서보 ON 시도")
            for remaining in range(5, 0, -1):
                self.publish_monitor_status(f"비상정지 복구 완료 — {remaining}초 후 작업 재개", "WARN", countdown=remaining)
                time.sleep(1.0)
            self.publish_monitor_status("비상정지 복구 완료 — 작업 재개", "OK", countdown=0)
        finally:
            self._handling_hw_stop = False

    def safe_release_force(self):
        try:
            release_force()
        except Exception:
            pass
        try:
            release_compliance_ctrl()
        except Exception:
            pass


def main(args=None):
    rclpy.init(args=args)
    node = rclpy.create_node("rokey_bartender", namespace=ROBOT_ID)
    command_node = rclpy.create_node("rokey_bartender_admin_control", namespace=ROBOT_ID)
    DR_init.__dsr__node = node

    global DR_BASE, DR_SSTOP, DR_TOOL, DR_MV_MOD_REL, DR_CIRCLE, DR_QSTOP_STO
    global get_current_posj, get_robot_state, get_tool_force, drl_script_stop
    global move_periodic, moveb, movej, movel, mwait, wait
    global release_compliance_ctrl, release_force
    global set_desired_force, set_digital_output, set_stiffnessx, set_tcp, set_tool
    global task_compliance_ctrl
    global posj, posx, posb

    try:
        from DSR_ROBOT2 import (
            DR_BASE,
            DR_SSTOP,
            DR_TOOL,
            DR_MV_MOD_REL,
            DR_CIRCLE,
            DR_QSTOP_STO,
            get_current_posj,
            get_robot_state,
            get_tool_force,
            drl_script_stop,
            move_periodic,
            moveb,
            movej,
            movel,
            mwait,
            release_compliance_ctrl,
            release_force,
            set_desired_force,
            set_digital_output,
            set_stiffnessx,
            set_tcp,
            set_tool,
            task_compliance_ctrl,
            wait,
        )
        from DR_common2 import posj, posx, posb
    except ImportError as exc:
        node.get_logger().error(f"Error importing DSR_ROBOT2 / DR_common2: {exc}")
        command_node.destroy_node()
        node.destroy_node()
        rclpy.shutdown()
        return

    robot = BartenderRobot(node, command_node)
    # IMPORTANT: do not use rclpy.spin(command_node) here.
    # rclpy.spin() uses the process-wide global executor. The main loop below
    # also calls rclpy.spin_once(node), which uses the same global executor.
    # Spinning the global executor from two threads can corrupt the wait set on
    # ROS 2 Humble and produce: IndexError: wait set index too big.
    # Use a private executor for the admin command node instead.
    command_executor = SingleThreadedExecutor()
    command_executor.add_node(command_node)

    def spin_command_node():
        try:
            command_executor.spin()
        except Exception as exc:
            try:
                robot.get_logger().error(f"admin_control executor stopped: {exc}")
            except Exception:
                pass

    command_spin_thread = threading.Thread(
        target=spin_command_node,
        daemon=True,
        name="admin_control_spin",
    )
    command_spin_thread.start()

    def run_recipe_worker(menu):
        robot.run_recipe(menu)
        robot.refresh_menu_subscription()

    def handle_keyboard_event():
        if not select.select([sys.stdin], [], [], 0.0)[0]:
            return
        key = sys.stdin.readline().strip().lower()
        if key == "a":
            robot.request_emergency_stop("keyboard a")
        elif key == "b":
            robot.request_emergency_release("keyboard b: release stop only")
        elif key:
            robot.get_logger().info("Keyboard test: a=emergency stop, b=release stop only")

    try:
        robot.get_logger().info("Keyboard test enabled: type 'a'+Enter for emergency stop, 'b'+Enter for release stop and return home.")
        while rclpy.ok():
            handle_keyboard_event()

            with robot.lock:
                task_running = robot.task_running

            # DSR_ROBOT2 내부 서비스 호출은 같은 node로 spin_until_future_complete()를 사용합니다.
            # 작업 중에 메인 thread에서 rclpy.spin_once()를 같이 돌리면 executor/generator 충돌이 발생하므로,
            # 작업 중에는 키보드 입력만 확인하고 ROS topic callback 처리는 작업이 없을 때만 수행합니다.
            if task_running:
                time.sleep(0.05)
                continue

            rclpy.spin_once(node, timeout_sec=0.1)
            menu = robot.take_pending_menu()
            if menu is not None:
                with robot.lock:
                    busy = robot.task_running or robot.home_return_running
                if busy:
                    robot.get_logger().warn(f"Menu ignored. Robot is busy. menu={menu}")
                    continue
                thread = threading.Thread(target=run_recipe_worker, args=(menu,), daemon=True)
                thread.start()
    except KeyboardInterrupt:
        with robot.lock:
            robot.cancel_requested = True
        robot.get_logger().warn("Cancel requested: KeyboardInterrupt")
        if robot.move_stop_client.wait_for_service(timeout_sec=0.2):
            req = MoveStop.Request()
            req.stop_mode = DR_SSTOP
            robot.move_stop_client.call_async(req)
        else:
            robot.get_logger().warn("motion/move_stop service is not available.")
        robot.safe_release_force()
        robot.publish_status(Status.WAITING)
    finally:
        try:
            robot.stop_background_threads()
        except Exception:
            pass
        try:
            command_executor.shutdown()
        except Exception:
            pass
        try:
            command_node.destroy_node()
        except Exception:
            pass
        try:
            node.destroy_node()
        except Exception:
            pass
        rclpy.shutdown()


if __name__ == "__main__":
    main()
