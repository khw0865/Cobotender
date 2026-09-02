# Robot rclpy Direct Fix

적용 파일:

- `app.py` → 프로젝트 루트에 덮어쓰기
- `static/js/admin.js` → `static/js/admin.js`에 덮어쓰기

## 핵심 변경

- 별도 제어 node 없이 `app.py` 안에서 `rclpy` Node를 생성합니다.
- `MultiThreadedExecutor(num_threads=2)`를 별도 daemon thread에서 spin합니다.
- 관리자 UI는 `/api/robot/status`를 1초마다 조회해 실제 `RobotState.actual_joint_position`을 표시합니다.
- 관리자 명령 버튼은 `/api/robot/command`를 통해 Doosan ROS2 service client로 직접 요청됩니다.

## 사용 전 확인

Doosan ROS2 bringup이 먼저 실행되어 있어야 합니다.

기본으로 가정한 이름:

- 상태 토픽: `/dsr01/state`
- 비상정지: `/dsr01/motion/move_stop`
- Robot OFF: `/dsr01/system/servo_off`
- Robot ON: `/dsr01/system/set_robot_control`
- 비상정지 해제: `/dsr01/system/set_safe_stop_reset_type`
- Grip: `/dsr01/gripper/robotiq_2f_close`
- Ungrip: `/dsr01/gripper/robotiq_2f_open`

네 환경에서 서비스명이 다르면 `app.py`의 `self.clients = {...}` 부분의 서비스명만 바꾸면 됩니다.

## 실행

```bash
python app.py
```

Flask debug reloader는 ROS2 node 중복 생성을 막기 위해 `use_reloader=False`로 설정했습니다.
