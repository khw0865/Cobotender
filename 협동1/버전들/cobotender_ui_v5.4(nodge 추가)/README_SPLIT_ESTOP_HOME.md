# v5.3 Admin UI Command Split

변경 사항:

- 관리자 UI에서 `주문취소` 버튼 제거
- 기존 `비상정지 해제 + 홈복귀` 버튼을 다음 두 개로 분리
  - `비상해제` → `/ui/admin_control: ESTOP_RELEASE` → bridge → `/ui/emergency_stop: false`
  - `홈복귀` → `/ui/admin_control: HOME_RETURN` → bridge → `/dsr01/task_control: HOME_RETURN`
- 최신 UI는 `/ui/admin_control`만 발행합니다. `/ui/emergency_stop`은 bridge만 발행합니다.
- 제어코드에서 `/ui/emergency_stop false`는 이제 홈복귀 없이 소프트 비상정지 latch만 해제합니다.
- 홈복귀는 `HOME_RETURN` 명령으로만 시작됩니다.
- `CANCEL`, `ESTOP_RELEASE_HOME`은 이전 UI 호환용으로 남겨두었습니다.
