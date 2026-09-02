# Bartender Kiosk Flask UI

## 실행 방법
```bash
cd bartender_kiosk
pip install -r requirements.txt
python app.py
```

## 접속 주소
- 고객용 UI: http://127.0.0.1:5000/customer
- 관리자 UI: http://127.0.0.1:5000/admin
- 관리자 계정: admin / admin

## 이미지 교체
`static/images` 안의 placeholder 이미지를 같은 파일명으로 교체하면 됩니다.
예: `slide_gatsby.jpg`, `slide_gil_beer.jpg`, `slide_public_warning.jpg`

## 주요 기능
- 고객 키오스크 광고 슬라이드
- 카테고리별 메뉴 표시
- 메뉴 상세 팝업 및 수량 입력
- 장바구니 합산
- 주문 DB 저장
- 주문 후 로딩/제조 완료 화면
- 직원호출 관리자 팝업
- 관리자 로그인
- 로봇 조작 UI 및 동작 중 조작 제한
- 위스키 재고 병 단위 입력 및 ml 차감
- 품절 표시/비활성화
- 주문 내역 및 금일 매출 합산
