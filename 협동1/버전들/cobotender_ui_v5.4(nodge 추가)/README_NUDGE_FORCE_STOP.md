# Nudge 외력 정지 기능 추가

## 목적
작업 중 로봇 TCP/tool 기준 외력이 약 20N 이상 감지되면 현재 모션을 정지시키고, 관리자 UI 로그에 외력 감지 메시지를 표시합니다.

## 적용 파일
- `bartender.py`

## 추가 동작
- `get_tool_force()` 값을 주기적으로 확인합니다.
- Fx, Fy, Fz의 벡터 크기 `sqrt(Fx^2 + Fy^2 + Fz^2)`가 `20.0N` 이상이면 `/dsr01/motion/move_stop`을 호출합니다.
- 내부적으로 `emergency_stopped=True`, `cancel_requested=True`가 되므로 현재 작업은 중단되고, 기존 소프트 비상정지 후처리 흐름으로 들어갑니다.
- 이후 관리자 UI에서 `비상해제` → `홈복귀` 순서로 복귀시키면 됩니다.

## 튜닝 상수
`bartender.py` 상단에서 조정할 수 있습니다.

```python
NUDGE_FORCE_THRESHOLD_N = 20.0
NUDGE_FORCE_CHECK_PERIOD_S = 0.05
NUDGE_TRIGGER_COUNT = 2
NUDGE_STOP_COOLDOWN_S = 1.0
```

## 의도적 힘제어 구간 예외
디스펜서 버튼 누르기와 쉐이커 뚜껑 누르기 구간은 코드상 의도적으로 20N 이상의 힘을 줄 수 있습니다.
해당 구간에서는 nudge 감시를 잠시 비활성화하도록 처리했습니다.

- 디스펜서 버튼 누름: `set_desired_force([40, 0, 0, ...])`
- 쉐이커 뚜껑 누름: `set_desired_force([0, 0, -20, ...])`

## 확인 방법
관리자 bridge 상태 또는 로그에서 아래 메시지를 확인합니다.

```text
Nudge 외력 감지: 23.4 N >= 20.0 N — 모션 정지
```

토픽으로 직접 확인할 수도 있습니다.

```bash
ros2 topic echo /dsr01/robot_monitor_status
```

출력 JSON에 다음 필드가 추가됩니다.

```json
{
  "nudge_stop_requested": true,
  "last_nudge_force_n": 23.4,
  "nudge_threshold_n": 20.0
}
```
