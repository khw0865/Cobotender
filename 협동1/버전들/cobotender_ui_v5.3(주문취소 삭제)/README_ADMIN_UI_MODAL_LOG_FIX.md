# Admin UI Modal / Log Fix

기준 패키지: `cobotender_home_return_cancel_fix.zip`

## 변경 사항

1. 관리자 로봇 명령 확인창 변경
   - 기존 `window.confirm()` 브라우저 기본 팝업 제거
   - 현재 관리자 UI의 다크/골드 계열 디자인과 맞춘 커스텀 모달 추가
   - 적용 명령: 일시정지, 재개, 주문취소, 비상정지, 비상정지 해제 + 홈복귀, 안전모드 Recovery

2. 로그 패널 고정 높이 처리
   - 로그가 쌓여도 `SYSTEM LOG` 패널이 아래로 계속 길어지지 않도록 고정 높이 적용
   - 로그 내부 영역만 세로 스크롤되도록 변경
   - 로그가 긴 경우 줄바꿈 처리 및 커스텀 스크롤바 적용

## 수정 파일

- `cobotender_ui_v5.2_no_conflict/templates/admin.html`
- `cobotender_ui_v5.2_no_conflict/static/css/admin.css`
- `cobotender_ui_v5.2_no_conflict/static/js/admin.js`

## 실행 구성

이 패키지는 이전 `home_return_cancel_fix` 구성과 동일하게 다음을 포함합니다.

- `cobotender_ui_v5.2_no_conflict/` : Flask UI
- `bartender.py` : 홈복귀 cancel 수정 반영 제어코드
- `bartender_admin_control_bridge.py` : UI/제어코드 중계 노드
