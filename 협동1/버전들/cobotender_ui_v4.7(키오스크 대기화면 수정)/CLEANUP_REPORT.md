# CoboTender UI v4.2 코드 점검 및 정리 내역

## 정리한 내용

### app.py

- 현재 관리자 UI에서 사용하지 않는 `psutil` 의존성을 제거했습니다.
- 관리자 UI에서 삭제된 `시스템 리소스`, `주변장치 연결 상태`와 관련된 응답 필드를 제거했습니다.
- 더 이상 사용하지 않는 `resource_status()` 함수를 제거했습니다.
- 현재 UI에서 사용하지 않는 `robot_logs` 테이블 생성 코드를 제거했습니다.
- `robot_logs`에 저장하던 `add_log()` 함수를 제거했습니다.
- `add_log()` 호출부를 제거했습니다.
- `/dsr01/joint_states`에서 받아오지만 현재 화면에서 사용하지 않는 joint velocity 저장 코드를 제거했습니다.
- 사용되지 않는 `last_status_time` 변수를 제거했습니다.
- 주문 저장, 재고 차감, 직원 요청, ROS2 메뉴 명령 발행, 비상정지 토픽 발행, 로봇 상태 수신 기능은 유지했습니다.

### static/js/admin.js

- 현재 화면에서 사용하지 않는 `connections`, `resource` fallback 데이터를 제거했습니다.
- 로봇 상태 표시, 조인트 각도 표시, 속도 표시, 작업 단계 표시, 로그 표시, 직원 요청 커스텀 모달 기능은 유지했습니다.

### README.md

- 프로젝트 폴더명을 v4.2 기준으로 수정했습니다.
- 현재 사용하지 않는 DB 테이블 설명을 정리했습니다.

## 유지한 기능

- 고객 UI 메뉴 표시 및 장바구니
- 주문 DB 저장
- 스트레이트 재고 자동 차감
- 칵테일 재료 재고 자동 차감
- 품절 판정
- 직원 요청 DB 저장 및 관리자 모달 알림
- 관리자 로그인 / 로그아웃
- 관리자 재고 관리
- 관리자 주문 내역 조회
- `/dsr01/joint_states` 기반 현재 조인트 각도 표시
- `/dsr01/aux_control/get_current_velx` 기반 Linear / Angular 속도 표시
- `/ui/menu_command` 주문 토픽 발행
- `/robot/process_state` 상태 토픽 구독
- `/ui/emergency_stop` 비상정지 토픽 발행

## 추가 점검 의견

- `get_current_velx`는 Service 방식이라 Topic 기반 조인트 각도보다 갱신이 느릴 수 있습니다.
- 고객 UI의 제조 로딩 화면은 아직 고정 시간 기반입니다. 실제 로봇 상태(`/robot/process_state`)와 완전히 동기화하려면 추가 수정이 필요합니다.
- 제어 코드에서 작업 단계별 로그를 별도 토픽으로 publish하면 관리자 로그 내역을 더 정확하게 만들 수 있습니다.


## 2026-07-09 상태 표시 수정

- 관리자 UI의 작업 진행 정보 항목을 기존 세부 작업 단계(주문 대기/잔 위치 이동/재료 투입/믹싱/서빙 위치 이동)에서 `/robot/process_state`의 `Status.status` 프로토콜 기준으로 변경했습니다.
- `app.py`의 `ROBOT_STATUS_TEXT` 매핑을 status 0~5와 1:1 대응하도록 수정했습니다.
- `templates/admin.html`의 진행 단계 목록을 WAITING, MAKING, MAKING_DONE, DELIVERING, DELIVERED, RETURNING_HOME 6단계로 변경했습니다.

## 2026-07-09 추가 수정: 조인트 게이지 범위 반영

관리자 UI의 조인트 각도 게이지 계산 기준을 기존 공통 범위 `-180° ~ 180°`에서 각 축별 범위로 변경했습니다.

- J1: -360° ~ 360°
- J2: -95° ~ 95°
- J3: -135° ~ 135°
- J4: -360° ~ 360°
- J5: -135° ~ 135°
- J6: -360° ~ 360°

수정 파일: `static/js/admin.js`


## Joint Velocity UI 변경

- `static/js/admin.js`: 조인트 게이지 범위 전체를 `-360° ~ 360°`로 통일하고 `jointVelocities`, `jointVelocityAverage` 기반 표시 추가.
- `templates/admin.html`: 속도 패널을 `조인트 각속도` 패널로 변경. 좌측은 J1~J6 각속도, 우측은 평균 각속도.
- `static/css/admin.css`: 조인트 각속도 목록용 레이아웃 스타일 추가.
- `app.py`: `get_current_velx` 기반 TCP 속도 표시 제거, `/dsr01/joint_states` 및 `/dsr01/dynamic_joint_states` velocity 기반 표시값 추가.


## Customer Manufacturing Wait Update

- 고객용 UI의 제조중 화면이 기존 9초 고정 타이머가 아니라 `/api/robot/status`의 `robot_status_raw` 값을 polling하여 실제 로봇 상태에 맞춰 유지되도록 수정했습니다.
- `/api/order` 응답에 `requires_robot_work`, `robot_command_sent`, `robot_command` 정보를 추가했습니다.
- 로봇 상태 기준: 1(MAKING), 2(MAKING_DONE), 3(DELIVERING)는 제조/서빙 진행 화면 유지, 4(DELIVERED), 5(RETURNING_HOME), 또는 작업 시작 후 0(WAITING)으로 완료 화면 전환.
