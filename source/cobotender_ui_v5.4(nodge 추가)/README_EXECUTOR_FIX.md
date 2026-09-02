# Executor wait-set 충돌 수정본

## 증상
`ros2 run rokey bartender` 실행 직후 다음 오류가 발생할 수 있었다.

```text
Exception in thread admin_control_spin:
IndexError: wait set index too big
```

## 원인
이전 `bartender.py`는 관리자 명령용 `command_node`를 별도 스레드에서 `rclpy.spin(command_node)`로 실행했다.

ROS 2 Humble의 `rclpy.spin()`은 기본 global executor를 사용한다. 동시에 메인 루프에서도 `rclpy.spin_once(node)`를 호출하고 있어서, 두 스레드가 같은 global executor/wait-set을 건드리며 충돌할 수 있다.

## 수정
관리자 명령용 노드는 별도의 `SingleThreadedExecutor`를 생성해 그 executor에서만 spin하도록 변경했다.

```python
from rclpy.executors import SingleThreadedExecutor

command_executor = SingleThreadedExecutor()
command_executor.add_node(command_node)

threading.Thread(target=command_executor.spin, daemon=True).start()
```

메인 DSR motion 노드의 기존 `rclpy.spin_once(node)` 구조는 유지했다. DSR_ROBOT2 내부 motion/service 호출과 충돌하지 않도록 작업 중에는 기존처럼 메인 노드 spin을 돌리지 않는다.

## 포함 파일
- `bartender.py`: executor 충돌 수정본
- `bartender_admin_control_bridge.py`: 기존 no-conflict bridge 유지
- `cobotender_ui_v5.2_no_conflict/`: 기존 no-conflict UI 유지
