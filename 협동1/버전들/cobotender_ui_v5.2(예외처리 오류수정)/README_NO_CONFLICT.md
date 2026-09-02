# CoboTender no-conflict integration package

## 실행 대상
1. Flask UI: `cobotender_ui_v5.2_no_conflict/app.py`
2. Bridge: `bartender_admin_control_bridge.py`
3. Robot control: `bartender.py`

## 통신 방향
관리자 UI는 `/ui/admin_control`만 발행합니다.
Bridge가 명령을 분배합니다.

- `/ui/admin_control` → Bridge
  - `PAUSE`, `RESUME`, `CANCEL`, `ESTOP`, `ESTOP_RELEASE_HOME`, `RECOVER`
- Bridge → Robot control
  - `/dsr01/task_control`: `PAUSE`, `RESUME`, `CANCEL`
  - `/ui/emergency_stop`: `True` / `False`
  - `/dsr01/recovery_command`: `RECOVER`
- Robot control → Bridge/UI
  - `/robot/process_state`: 고객 UI 제조 상태
  - `/dsr01/robot_monitor_status`: 관리자 안전상태 JSON
- Bridge → UI
  - `/robot/admin_bridge_status`: 관리자 표시용 safety/log JSON

## 충돌 방지 반영 내용
- UI의 비상정지/해제 버튼에서 `/ui/emergency_stop` 직접 발행 fallback 제거
- Bridge에 중복 관리자 명령 0.8초 필터 추가
- Robot control에 `/ui/emergency_stop` 중복 Bool 0.8초 필터 추가
- Robot control의 stop/home 요청 idempotent 처리
- 작업 중에도 관리자 명령을 받도록 command node 분리 유지
- 메뉴 subscription을 작업 후 destroy/recreate하지 않도록 변경
- `/dsr01/robot_monitor_status` heartbeat 추가. 장시간 motion 중 bridge가 끊김으로 표시되는 현상 완화

## topic info 기대값
세 프로세스를 모두 실행한 뒤:

```bash
ros2 topic info /ui/admin_control
# Publisher count: 1  (UI)
# Subscription count: 1 (Bridge)

ros2 topic info /dsr01/task_control
# Publisher count: 1  (Bridge)
# Subscription count: 1 (Robot control)

ros2 topic info /dsr01/recovery_command
# Publisher count: 1  (Bridge)
# Subscription count: 1 (Robot control)

ros2 topic info /ui/emergency_stop
# Publisher count: 1  (Bridge)
# Subscription count: 1 (Robot control)

ros2 topic info /dsr01/robot_monitor_status
# Publisher count: 1  (Robot control)
# Subscription count: 1 (Bridge)

ros2 topic info /robot/admin_bridge_status
# Publisher count: 1  (Bridge)
# Subscription count: 1 (UI)
```

`/ui/emergency_stop`의 Publisher count가 2 이상이면 UI 또는 다른 노드가 아직 직접 emergency_stop을 발행하고 있는 것입니다.
