# 🍸 CoboTender UI

Doosan Robotics Boot Camp Project

------------------------------------------------------------------------

# 프로젝트 소개

CoboTender는 Doosan Robot을 이용한 **바텐더 로봇 시스템**입니다.

본 프로젝트는 고객용 키오스크 UI, 관리자용 UI, Flask, SQLite3, ROS2
통신을 이용하여 주문부터 제조까지 하나의 시스템으로 구성하였습니다.

## 개발 환경

-   Ubuntu 22.04
-   Python 3.10
-   ROS2 Humble
-   Flask
-   SQLite3
-   HTML / CSS / JavaScript

## 프로젝트 구조

``` text
cobotender_ui_v4.2
├── app.py
├── database/
│   └── bar.db
├── templates/
│   ├── customer.html
│   ├── admin_login.html
│   ├── admin.html
│   ├── inventory.html
│   └── orders.html
├── static/
│   ├── css/
│   ├── js/
│   └── images/
└── README.md
```

## 주요 기능

### 고객용 UI

-   광고 슬라이드
-   주문 화면
-   장바구니
-   요청사항
-   제조 진행 화면
-   제조 완료 화면

### 관리자용 UI

-   관리자 로그인
-   현재 조인트 각도 표시
-   로봇 상태 표시
-   작업 진행 정보
-   동작 속도 표시
-   비상정지 / 비상정지 해제
-   로그 내역
-   재고 관리
-   주문 내역 조회

## Database

SQLite3

테이블

-   menu
-   orders
-   order_items
-   staff_requests

## ROS2 Interface

### UI → Robot

#### 주문 토픽

-   Topic : `/ui/menu_command`
-   Message : `bartender_interfaces/msg/Menu`

  메뉴                     코드
  ---------------------- ------
  Old Fashioned               0
  Mojito                      1
  Whisky Sour                 2
  Macallan 12                 3
  Glenfiddich 12              4
  Jameson                     5
  Maker's Mark                6
  Ballantine's 17             7
  Johnnie Walker Black        8

#### 긴급 정지

-   Topic : `/ui/emergency_stop`
-   Message : `std_msgs/msg/Bool`
-   Value : `True`

### Robot → UI

Topic : `/robot/process_state`

Message : `bartender_interfaces/msg/Status`

    Status 의미
  -------- -------------------------------
         0 WAITING - 대기 중 / 주문 가능 상태
         1 MAKING - 음료 제조 중
         2 MAKING_DONE - 제조 완료
         3 DELIVERING - 손님한테 서빙 이동 중
         4 DELIVERED - 서빙 완료
         5 RETURNING_HOME - 초기 위치로 복귀 중

관리자 UI의 작업 진행 정보도 위 status 값과 1:1로 표시됩니다.

## 실행 방법

``` bash
source /opt/ros/humble/setup.bash
source ~/your_ws/install/setup.bash
python app.py
```

고객용 UI

    http://localhost:5000/customer

관리자용 UI

    http://localhost:5000/admin

## 향후 개발

-   ROS2 Action 연동
-   제조 진행률 자동 반영
-   Robot Error 표시
-   실제 레시피 적용

------------------------------------------------------------------------

**Doosan Robotics Boot Camp Project**

## 관리자 UI 조인트 게이지 범위

관리자 대시보드의 조인트 게이지 바는 각 축의 실제 허용 범위를 기준으로 표시됩니다.

| Joint | Range |
|---|---:|
| J1 | -360° ~ 360° |
| J2 | -95° ~ 95° |
| J3 | -135° ~ 135° |
| J4 | -360° ~ 360° |
| J5 | -135° ~ 135° |
| J6 | -360° ~ 360° |


## v4.2 Joint Velocity Update

- 관리자 UI의 기존 `Linear / Angular` 속도 표시를 제거했습니다.
- `/dsr01/joint_states` 및 `/dsr01/dynamic_joint_states` 기반 조인트 각속도를 표시합니다.
- 좌측 카드: J1~J6 각 조인트 각속도(`deg/s`).
- 우측 카드: 6개 조인트의 절댓값 기준 평균 각속도(`deg/s`).
- 조인트 각도 게이지 범위를 모든 축 공통 `-360° ~ 360°`로 변경했습니다.
- TCP 속도 조회용 `get_current_velx` 서비스 호출은 관리자 UI 표시값에서 제외했습니다.
