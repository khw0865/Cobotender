# Admin Dashboard Update

파일 위치:
- templates/admin.html
- static/css/admin.css
- static/js/admin.js

필요 API:
- GET /api/robot/status
- POST /api/robot/command

/api/robot/status 응답 예시:
{
  "mode": "AUTO",
  "joints": [0, 0, 90, 0, 90, 0],
  "recipe": "Old Fashioned",
  "step": "재료 투입",
  "progress": 45,
  "connections": {"ros": true, "mcu": true, "plc": true},
  "speed": {"linear": 250, "angular": 40},
  "resource": {"cpu": 18, "memory": 42, "temperature": 47},
  "taskIndex": 2
}

mode 색상:
- IDLE: 흰색
- AUTO: 초록색
- MANUAL: 파란색
- ERROR: 노란색
- ESTOP: 빨간색 큰 동그라미
