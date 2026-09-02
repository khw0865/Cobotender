# ROS2 직접 연결 정리본

## 실행 순서

```bash
source /opt/ros/humble/setup.bash
# Doosan 패키지를 직접 빌드한 워크스페이스가 있다면 추가로 source
# source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
python app.py
```

접속:

```text
http://127.0.0.1:5001/admin
```

## 반영 내용

- `rclpy.init()` 이후 ROS2 Node를 생성하도록 수정
- `MultiThreadedExecutor(num_threads=2)`를 별도 thread에서 실행
- 현재 조인트 각도는 `/dsr01/joint_states` (`sensor_msgs/msg/JointState`)에서 수신
- `/admin/inventory`, `/admin/orders` 라우트 포함
- `__pycache__`, `.pyc` 제거

## 참고

- 조인트 각도 수신은 `dsr_msgs2` 없이 동작합니다.
- Robot ON/OFF, E-STOP, Grip/Ungrip 서비스 호출은 `dsr_msgs2.srv` 서비스 타입이 필요합니다.
- Grip/Ungrip은 기본적으로 `/dsr01/io/set_tool_digital_output` 서비스 기준으로 구성되어 있습니다. 실제 그리퍼 배선 채널이 다르면 `app.py`의 `_tool_output()`에서 채널 번호를 바꾸면 됩니다.
