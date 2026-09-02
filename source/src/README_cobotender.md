# CoboTender 바텐더 로봇 시스템

CoboTender는 **Flask 기반 고객/관리자 HMI**, **SQLite 주문·재고 DB**, **ROS2 토픽 통신**, **Doosan M0609 + RG2 그리퍼 제어 코드**를 통합한 바텐더 로봇 프로젝트입니다.

고객용 UI에서 메뉴를 주문하면 ROS2 메시지로 로봇 제어 노드에 주문이 전달되고, 로봇은 제조·서빙·홈복귀 상태를 UI에 다시 전달합니다. 관리자용 UI에서는 로봇 상태 확인, 일시정지, 재개, 비상정지, 비상해제, 홈복귀, 안전모드 Recovery 요청을 수행할 수 있습니다.

---

## 1. 패키지 구성

```text
src/
├── bartender_interfaces/
│   ├── package.xml
│   ├── CMakeLists.txt
│   └── msg/
│       ├── Menu.msg
│       └── Status.msg
│
└── cobotender/
    ├── package.xml
    ├── setup.py
    ├── setup.cfg
    ├── resource/
    │   └── cobotender
    ├── launch/
    │   └── cobotender.launch.py
    ├── cobotender/
    │   ├── __init__.py
    │   ├── app.py
    │   ├── bartender.py
    │   └── bartender_admin_control_bridge.py
    ├── templates/
    │   ├── customer.html
    │   ├── admin_login.html
    │   ├── admin.html
    │   ├── inventory.html
    │   └── orders.html
    ├── static/
    │   ├── css/
    │   │   ├── customer.css
    │   │   └── admin.css
    │   ├── js/
    │   │   ├── customer.js
    │   │   └── admin.js
    │   └── images/
    └── database/
        └── bar.db
```

---

## 2. 주요 실행 파일

### 2-1. `app.py`

Flask 웹 서버와 UI용 ROS2 bridge 노드를 함께 실행합니다.

주요 기능:

- 고객용 주문 UI 제공
- 관리자 로그인 및 관리자 대시보드 제공
- SQLite DB 초기화 및 주문/재고/요청사항 관리
- 고객 주문을 `/ui/menu_command` 토픽으로 발행
- 로봇 작업 상태 `/robot/process_state` 구독
- 관리자 명령을 `/ui/admin_control` 토픽으로 발행
- 관리자 화면에 표시할 조인트 각도, 조인트 속도, 로봇 상태, 로그 제공

웹 접속 주소:

```text
http://localhost:5000/customer
http://localhost:5000/admin
```

관리자 기본 로그인:

```text
ID: admin
PW: admin
```

---

### 2-2. `bartender.py`

Doosan M0609 로봇팔의 실제 제조 동작을 수행하는 핵심 제어 노드입니다.

주요 기능:

- `/ui/menu_command`로 고객 주문 수신
- 주문 메뉴를 queue에 저장 후 순차 제조
- 칵테일 제조, 스트레이트 제조, 컵 서빙, 홈복귀 수행
- Doosan `movej`, `movel`, `moveb`, `mwait` 기반 모션 수행
- 디스펜서 버튼 누름, 트레이 이동, 쉐이킹, 컵 전달 동작 수행
- RG2 그리퍼 제어용 디지털 출력 및 `/onrobot/sendCommand` 서비스 사용
- `/robot/process_state`로 제조 단계 발행
- 비상정지, 비상해제, 일시정지, 재개, 홈복귀, Recovery 처리
- `/dsr01/robot_monitor_status`로 관리자 bridge용 상태 JSON 발행

---

### 2-3. `bartender_admin_control_bridge.py`

관리자 UI와 로봇 제어 코드 사이에서 명령을 중계하는 bridge 노드입니다.

관리자 UI는 모든 제어 명령을 `/ui/admin_control` 하나로 보냅니다. Bridge는 명령 종류에 따라 실제 로봇 제어 토픽으로 분배합니다.

명령 분배 구조:

```text
/ui/admin_control String "ESTOP"
→ /ui/emergency_stop Bool True

/ui/admin_control String "ESTOP_RELEASE"
→ /ui/emergency_stop Bool False

/ui/admin_control String "PAUSE"
→ /dsr01/task_control String "PAUSE"

/ui/admin_control String "RESUME"
→ /dsr01/task_control String "RESUME"

/ui/admin_control String "HOME_RETURN"
→ /dsr01/task_control String "HOME_RETURN"

/ui/admin_control String "RECOVER"
→ /dsr01/recovery_command String "RECOVER"
```

Bridge를 따로 둔 이유:

- UI 코드를 단순하게 유지
- 명령별 토픽 타입 분배를 한 곳에서 관리
- 비상정지, Recovery 같은 안전 관련 명령의 중복 발행 방지
- `bartender.py`의 상태 JSON을 UI용 상태로 정리

---

## 3. 커스텀 메시지

### 3-1. `bartender_interfaces/msg/Menu.msg`

고객 UI에서 로봇으로 주문 메뉴를 전달할 때 사용합니다.

```text
int32 WHISKEY     = 0
int32 VODKA       = 1
int32 NON_ALCOHOL = 2
int32 STRAIGHT1   = 3
int32 STRAIGHT2   = 4
int32 STRAIGHT3   = 5
int32 STRAIGHT4   = 6
int32 STRAIGHT5   = 7
int32 STRAIGHT6   = 8

int32 menu
```

메뉴 매핑:

| UI 메뉴명 | menu code |
|---|---:|
| Old Fashioned | 0 |
| Mojito | 1 |
| Whisky Sour | 2 |
| Macallan 12 | 3 |
| Glenfiddich 12 | 4 |
| Jameson | 5 |
| Maker's Mark | 6 |
| Ballantine's 17 | 7 |
| Johnnie Walker Black | 8 |

---

### 3-2. `bartender_interfaces/msg/Status.msg`

로봇이 UI로 현재 작업 단계를 전달할 때 사용합니다.

```text
int32 WAITING         = 0
int32 MAKING          = 1
int32 MAKING_DONE     = 2
int32 DELIVERING      = 3
int32 DELIVERED       = 4
int32 RETURNING_HOME  = 5

int32 status
```

상태 흐름:

```text
WAITING
→ MAKING
→ MAKING_DONE
→ DELIVERING
→ DELIVERED
→ RETURNING_HOME
→ WAITING
```

---

## 4. 주요 ROS2 토픽

| 토픽 | 타입 | 방향 | 설명 |
|---|---|---|---|
| `/ui/menu_command` | `bartender_interfaces/msg/Menu` | UI → Robot | 고객 주문 메뉴 전송 |
| `/robot/process_state` | `bartender_interfaces/msg/Status` | Robot → UI | 제조/서빙 진행 상태 전송 |
| `/ui/admin_control` | `std_msgs/msg/String` | Admin UI → Bridge | 관리자 명령 통합 전송 |
| `/ui/emergency_stop` | `std_msgs/msg/Bool` | Bridge → Robot | 비상정지 True, 비상해제 False |
| `/dsr01/task_control` | `std_msgs/msg/String` | Bridge → Robot | PAUSE, RESUME, HOME_RETURN 등 |
| `/dsr01/recovery_command` | `std_msgs/msg/String` | Bridge → Robot | RECOVER 명령 전송 |
| `/dsr01/robot_monitor_status` | `std_msgs/msg/String` JSON | Robot → Bridge | 로봇 내부 상태 모니터링 |
| `/robot/admin_bridge_status` | `std_msgs/msg/String` JSON | Bridge → UI | 관리자 UI용 상태 정보 |
| `/dsr01/joint_states` | `sensor_msgs/msg/JointState` | Robot → UI | 조인트 각도 표시용 |
| `/dsr01/dynamic_joint_states` | `control_msgs/msg/DynamicJointState` | Robot → UI | 조인트 속도 표시용 |

---

## 5. 주요 서비스

| 서비스 | 타입 | 사용 위치 | 설명 |
|---|---|---|---|
| `/dsr01/motion/move_stop` | `dsr_msgs2/srv/MoveStop` | `bartender.py` | 비상정지/취소 시 현재 모션 정지 |
| `/dsr01/system/set_robot_control` | `dsr_msgs2/srv/SetRobotControl` | `bartender.py` | Safe Stop / Safe Off 복구 처리 |
| `/onrobot/sendCommand` | `onrobot_rg_msgs/srv/SetCommand` | `bartender.py` | OnRobot RG2 그리퍼 명령 전송 |

---

## 6. 관리자 제어 동작 방식

### 6-1. 일시정지

```text
관리자 UI
→ /ui/admin_control String "PAUSE"
→ bridge
→ /dsr01/task_control String "PAUSE"
→ bartender.py request_pause()
→ pause_requested=True
```

일시정지는 즉시 `move_stop`을 호출하는 방식이 아닙니다. 현재 동작 단위가 끝난 뒤 코드 내부 체크 지점에서 대기하는 협조적 일시정지 방식입니다.

### 6-2. 재개

```text
관리자 UI
→ /ui/admin_control String "RESUME"
→ bridge
→ /dsr01/task_control String "RESUME"
→ bartender.py request_resume()
→ pause_requested=False
```

재개 요청이 들어오면 일시정지 대기 루프를 빠져나와 다음 동작을 계속 수행합니다.

### 6-3. 비상정지

```text
관리자 UI
→ /ui/admin_control String "ESTOP"
→ bridge
→ /ui/emergency_stop Bool True
→ bartender.py request_emergency_stop()
→ /dsr01/motion/move_stop 호출
```

비상정지는 현재 모션을 정지하고 작업 취소 플래그를 설정합니다. 대기 중인 주문 queue도 삭제합니다.

### 6-4. 비상해제

```text
관리자 UI
→ /ui/admin_control String "ESTOP_RELEASE"
→ bridge
→ /ui/emergency_stop Bool False
→ bartender.py release_emergency_stop_only()
```

비상해제는 정지 상태 플래그만 해제합니다. 로봇이 자동으로 움직이지 않으며, 홈복귀는 별도 명령으로 실행합니다.

### 6-5. 홈복귀

```text
관리자 UI
→ /ui/admin_control String "HOME_RETURN"
→ bridge
→ /dsr01/task_control String "HOME_RETURN"
→ bartender.py request_home_return()
→ return_home(ignore_cancel=True)
```

비상정지 이후에는 일반적으로 다음 순서로 복귀합니다.

```text
비상정지 → 비상해제 → 홈복귀
```

### 6-6. Recovery

```text
관리자 UI
→ /ui/admin_control String "RECOVER"
→ bridge
→ /dsr01/recovery_command String "RECOVER"
→ bartender.py approve_recovery()
```

Safe Stop, Safe Off, Emergency Stop 등 안전 상태에서 복구 승인 플래그를 켜고, `set_robot_control` 서비스를 통해 복구를 진행합니다.

---

## 7. 빌드 방법

작업 공간 기준:

```text
~/ws_cobot_pjt/ws_dsr
```

빌드:

```bash
cd ~/ws_cobot_pjt/ws_dsr

colcon build --packages-select bartender_interfaces cobotender --symlink-install

source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
```

빌드 후 실행 파일 확인:

```bash
ros2 pkg executables cobotender
```

정상적으로 등록되면 다음 실행 파일이 보여야 합니다.

```text
cobotender app
cobotender bartender
cobotender bridge
```

---

## 8. 실행 전 필수 준비

### 8-1. 모든 터미널에서 workspace source

CoboTender를 실행하거나 토픽을 확인하는 모든 터미널에서 먼저 다음 명령을 실행합니다.

```bash
source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
```

필요한 경우 ROS_DOMAIN_ID도 Doosan driver와 동일하게 맞춥니다.

```bash
export ROS_DOMAIN_ID=5
```

### 8-2. Doosan M0609 + RG2 bringup 실행

CoboTender 실행 전에 별도 터미널에서 Doosan bringup을 먼저 실행해야 합니다.

```bash
source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
ros2 launch m0609_rg2_bringup bringup.launch.py mode:=real host:=192.168.1.100 model:=m0609
```

이 bringup이 실행되어야 `/dsr01` 네임스페이스의 Doosan driver, 로봇 상태 토픽, motion service, RG2 관련 인터페이스가 정상적으로 연결됩니다.

---

## 9. 전체 시스템 실행

### 터미널 1: Doosan bringup

```bash
source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
ros2 launch m0609_rg2_bringup bringup.launch.py mode:=real host:=192.168.1.100 model:=m0609
```

### 터미널 2: CoboTender 실행

```bash
source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
ros2 launch cobotender cobotender.launch.py
```

실행되는 노드:

```text
bridge
bartender
app
```

웹 UI 접속:

```text
고객 UI:   http://localhost:5000/customer
관리자 UI: http://localhost:5000/admin
```

다른 PC에서 접속할 경우:

```text
http://<서버PC_IP>:5000/customer
http://<서버PC_IP>:5000/admin
```

---

## 10. 개별 실행 방법

전체 launch 대신 개별 실행도 가능합니다.

```bash
source ~/ws_cobot_pjt/ws_dsr/install/setup.bash

ros2 run cobotender bridge
ros2 run cobotender bartender
ros2 run cobotender app
```

단, 개별 실행 시에도 Doosan bringup은 먼저 실행되어 있어야 합니다.

---

## 11. 동작 확인 명령어

### 주문 토픽 확인

```bash
ros2 topic echo /ui/menu_command
```

고객 UI에서 Old Fashioned를 주문하면 다음처럼 보여야 합니다.

```text
menu: 0
```

### 로봇 진행 상태 확인

```bash
ros2 topic echo /robot/process_state
```

### 관리자 명령 확인

```bash
ros2 topic echo /ui/admin_control
```

### bridge가 분배한 작업 제어 명령 확인

```bash
ros2 topic echo /dsr01/task_control
```

### 비상정지 토픽 확인

```bash
ros2 topic echo /ui/emergency_stop
```

### 로봇 상태 JSON 확인

```bash
ros2 topic echo /dsr01/robot_monitor_status
```

### 관리자 UI용 bridge 상태 확인

```bash
ros2 topic echo /robot/admin_bridge_status
```

### 조인트 상태 확인

```bash
ros2 topic echo /dsr01/joint_states
ros2 topic echo /dsr01/dynamic_joint_states
```

---

## 12. DB 구조

SQLite DB 파일:

```text
src/cobotender/database/bar.db
```

주요 테이블:

| 테이블 | 설명 |
|---|---|
| `menu` | 메뉴명, 카테고리, 가격, 이미지, 재고량 저장 |
| `orders` | 주문 번호, 주문 시간, 총액 저장 |
| `order_items` | 주문별 상세 메뉴 저장 |
| `staff_requests` | 물, 냅킨, 직원호출, 안주 주문 요청 저장 |

DB가 비어 있으면 `app.py`의 `init_db()`에서 기본 메뉴 데이터를 생성합니다.

---

## 13. 고객 주문 처리 흐름

```text
고객 UI 주문
→ customer.js
→ POST /api/order
→ app.py
→ DB 주문 저장 및 재고 차감
→ /ui/menu_command Menu 발행
→ bartender.py menu_callback()
→ 주문 queue 저장
→ 제조 thread 실행
→ /robot/process_state Status 발행
→ 고객 UI 제조 상태 표시
```

안주와 요청사항은 로봇 제조 대상이 아니며, `staff_requests` 테이블에 기록되어 관리자 UI에 팝업으로 표시됩니다.

---

## 14. 관리자 상태 표시 흐름

```text
bartender.py
→ /dsr01/robot_monitor_status JSON 발행
→ bartender_admin_control_bridge.py 수신
→ /robot/admin_bridge_status JSON 발행
→ app.py 수신
→ admin.js가 /api/robot/status polling
→ 관리자 UI 갱신
```

관리자 UI에는 다음 정보가 표시됩니다.

- ROS 연결 상태
- 로봇 모드
- 작업 단계
- 조인트 각도 J1~J6
- 조인트 속도 J1~J6
- 하드웨어 상태
- Recovery 필요 여부
- 최근 로그
- 직원 요청 팝업

---

## 15. 자주 발생하는 문제

### 15-1. 웹 포트 5000 사용 중

증상:

```text
Address already in use
Port 5000 is in use by another program
```

해결:

```bash
sudo fuser -k 5000/tcp
```

그 다음 다시 실행합니다.

```bash
ros2 launch cobotender cobotender.launch.py
```

---

### 15-2. UI는 뜨지만 로봇과 연결되지 않음

확인 순서:

```bash
source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
ros2 topic list | grep dsr01
```

`/dsr01/joint_states`, `/dsr01/dynamic_joint_states`, `/dsr01/robot_state` 계열 토픽이 보이지 않으면 Doosan bringup이 먼저 실행되지 않았거나 ROS_DOMAIN_ID가 맞지 않는 상태입니다.

Doosan bringup 재실행:

```bash
source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
ros2 launch m0609_rg2_bringup bringup.launch.py mode:=real host:=192.168.1.100 model:=m0609
```

---

### 15-3. 고객 주문이 로봇으로 전달되지 않음

확인:

```bash
ros2 topic info /ui/menu_command -v
ros2 topic echo /ui/menu_command
```

정상이라면 publisher와 subscriber가 모두 있어야 하며, 주문 시 `menu: 0` 같은 메시지가 보여야 합니다.

---

### 15-4. 관리자 명령이 동작하지 않음

확인:

```bash
ros2 topic echo /ui/admin_control
ros2 topic echo /dsr01/task_control
ros2 topic echo /ui/emergency_stop
ros2 topic echo /dsr01/recovery_command
```

`/ui/admin_control`에는 뜨는데 뒤쪽 제어 토픽에 안 뜨면 bridge 노드를 확인합니다.

---

### 15-5. `/onrobot/sendCommand service is not available` 경고

이 경고는 RViz/OnRobot 명령 서비스가 없을 때 발생합니다.

```text
/onrobot/sendCommand service is not available. Skip RViz gripper command.
```

실제 디지털 출력 기반 그리퍼 동작과는 별개일 수 있습니다. RG2 서비스가 필요한 환경이면 bringup과 OnRobot 관련 노드 실행 상태를 확인합니다.

---

## 16. 주의사항

- `ros2 launch cobotender cobotender.launch.py` 실행 전에 반드시 Doosan bringup을 먼저 실행합니다.
- CoboTender 관련 명령을 실행하는 모든 터미널에서 `source ~/ws_cobot_pjt/ws_dsr/install/setup.bash`를 실행합니다.
- `cobotender.launch.py`는 ROS_DOMAIN_ID를 강제로 지정하지 않습니다. Doosan driver와 같은 터미널 환경값을 사용합니다.
- `setup.py`에서 bridge 실행 이름은 `bridge`입니다. launch 파일도 `executable='bridge'` 기준입니다.
- Flask 서버는 기본 포트 `5000`을 사용합니다.
- 관리자 로그인은 현재 개발용으로 `admin/admin`입니다.
- `app.secret_key`는 Flask session 서명용 개발 키입니다. 실제 배포 시 환경변수로 분리하는 것이 좋습니다.

---

## 17. 빠른 실행 요약

```bash
# 1. 빌드
cd ~/ws_cobot_pjt/ws_dsr
colcon build --packages-select bartender_interfaces cobotender --symlink-install

# 2. 터미널 1: Doosan bringup
source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
ros2 launch m0609_rg2_bringup bringup.launch.py mode:=real host:=192.168.1.100 model:=m0609

# 3. 터미널 2: CoboTender 실행
source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
ros2 launch cobotender cobotender.launch.py

# 4. 웹 접속
# 고객 UI:   http://localhost:5000/customer
# 관리자 UI: http://localhost:5000/admin
```
