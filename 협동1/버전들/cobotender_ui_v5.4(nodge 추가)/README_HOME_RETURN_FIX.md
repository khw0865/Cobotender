# CoboTender Home Return Fix

## 수정 목적
관리자 UI에서 `비상정지 해제 + 홈복귀`를 눌렀을 때 홈복귀가 시작되지 않는 문제를 수정했습니다.

## 원인
`request_cancel_and_home()`이 `cancel_requested=True`를 설정한 뒤 홈복귀를 수행하는 구조였고, 기존 코드의 `return_home()`은 `movej()`를 호출했습니다.

하지만 `install_motion_wrappers()` 이후 `movej()`는 `safe_movej()`로 바뀌며, `safe_movej()` 시작 시 `check_cancel()`을 호출합니다.

따라서 홈복귀 동작 자체가 아래 흐름으로 차단되었습니다.

```text
비상정지 해제 + 홈복귀
→ cancel_requested=True
→ return_home()
→ safe_movej()
→ check_cancel()
→ RuntimeError("Task canceled")
→ 홈복귀 실패
```

## 수정 내용
1. `return_home(ignore_cancel=False)` 옵션 추가
2. `ignore_cancel=True`일 때 `_raw_movej`, `_raw_mwait`를 직접 사용
3. `_home_return_worker()`에서 홈복귀 전 `cancel_requested=False`, `pause_requested=False` 처리
4. `run_recipe()`의 cancel/home except 경로에서도 동일 처리
5. 홈복귀 시작/완료 상태를 `/dsr01/robot_monitor_status`로 publish

## 교체 대상
ROS2 패키지의 기존 `bartender.py`를 이 파일로 교체하세요.

## 확인 명령
```bash
ros2 topic echo /ui/admin_control
ros2 topic echo /ui/emergency_stop
ros2 topic echo /dsr01/robot_monitor_status
```

기대 흐름:

```text
비상정지 버튼          → /ui/admin_control: ESTOP → /ui/emergency_stop: true
비상정지 해제+홈복귀   → /ui/admin_control: ESTOP_RELEASE_HOME → /ui/emergency_stop: false
```

`/dsr01/robot_monitor_status`에는 홈복귀 시작/완료 로그가 표시되어야 합니다.
