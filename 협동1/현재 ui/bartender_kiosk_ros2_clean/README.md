# Clean ROS2 Flask app.py

적용 위치: 기존 프로젝트 루트의 `app.py`를 이 파일로 교체하세요.

실행:

```bash
source /opt/ros/humble/setup.bash
# dsr 서비스 명령까지 사용하려면 두산 워크스페이스도 source 필요
# source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
python app.py
```

접속:

```text
http://127.0.0.1:5001/admin
```

주요 변경:
- `/dsr01/joint_states` (`sensor_msgs/msg/JointState`) 구독
- `rclpy.init()` 후 Node 생성
- `MultiThreadedExecutor(num_threads=2)` 별도 thread 사용
- `self.clients` 이름 충돌 제거 → `self.service_clients` 사용
- `/admin/inventory`, `/admin/orders` 라우트 포함
- `__pycache__` 제외

주의:
- 조인트 상태 표시는 `sensor_msgs`만으로 가능.
- Robot ON/OFF, E-STOP 등 서비스 명령은 `dsr_msgs2.srv` import가 필요함.
- Grip/Ungrip은 `/dsr01/io/set_tool_digital_output` 서비스 타입을 기준으로 작성했으므로 실제 그리퍼 배선에 맞게 index/value를 조정해야 할 수 있음.
