#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from typing import Any, Mapping

import rclpy
from hey_doopal_msg.srv import GetDbData
from rcl_interfaces.msg import Log
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    QoSProfile,
    ReliabilityPolicy,
    qos_profile_rosout_default,
)
from std_msgs.msg import Bool, String

from redis_store import RedisStore, utc_now
from runtime_log import append_runtime_log


class RedisObjectBridge(Node):
    """ROS 2와 Redis/Flask UI 사이의 단일 Bridge 노드.

    Redis에 저장하는 데이터:
    - 객체 인식/이동 정보
    - /ui_chat_log의 사용자·Assistant 대화 내용
    - VLA의 현재 UI 상태

    Redis에 저장하지 않는 데이터:
    - ROS/M0609/통신 런타임 로그
      runtime_logs.jsonl 파일에만 기록되며 관리자 UI에서 조회한다.
    """

    USER_UI_STATE_KEY = "assistive_robot:user_ui_state"

    def __init__(self) -> None:
        super().__init__("assistive_robot_redis_bridge")

        self.store = RedisStore()
        self.store.ping()
        fixed_result = self.store.initialize_fixed_data()

        self.chat_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
            depth=10,
        )

        # 객체와 VLA 상태 저장용 구독
        self.create_subscription(
            String,
            "/assistive/object_detection",
            self.object_detection_callback,
            10,
        )
        self.create_subscription(
            String,
            "/assistive/object_moved",
            self.object_moved_callback,
            10,
        )
        self.create_subscription(
            String,
            "/assistive/vla_state",
            self.vla_state_callback,
            10,
        )

        # VLA 대화 전용 토픽. 대화만 Redis conversation_history에 저장한다.
        self.create_subscription(
            String,
            "/ui_chat_log",
            self.ui_chat_log_callback,
            self.chat_qos,
        )

        # Hand Tracking -> VLA 신호를 관리자 런타임 로그에도 표시한다.
        self.create_subscription(
            Bool,
            "/hand_tracking_request",
            self.hand_tracking_started_callback,
            10,
        )
        self.create_subscription(
            Bool,
            "/hand_arrived",
            self.hand_arrived_callback,
            10,
        )

        # 외부 노드가 명시적으로 보내는 시스템/통신 로그(JSON String).
        self.create_subscription(
            String,
            "/assistive/system_log",
            self.system_log_callback,
            50,
        )

        # M0609, Robot Control, Hand 노드 등의 ROS 로그를 파일에 기록한다.
        filter_text = os.getenv(
            "ROSOUT_LOG_FILTER",
            "m0609,dsr,robot_control,hand,object_detection,redis_bridge,vla",
        )
        self.rosout_filters = {
            item.strip().lower()
            for item in filter_text.split(",")
            if item.strip()
        }
        self.create_subscription(
            Log,
            "/rosout",
            self.rosout_callback,
            qos_profile_rosout_default,
        )

        # 외부 프로그램의 Redis 조회 서비스
        self.get_db_data_service = self.create_service(
            GetDbData,
            "/assistive/get_db_data",
            self.get_db_data_callback,
        )

        self.get_logger().info(f"fixed data: {fixed_result}")
        self.get_logger().info(
            "Redis bridge started: "
            "chat=/ui_chat_log, state=/assistive/vla_state, "
            "query=/assistive/get_db_data"
        )
        self._runtime_log(
            source="redis_bridge",
            level="INFO",
            category="startup",
            message="Redis Bridge started",
            details={
                "fixed_data": fixed_result,
                "chat_topic": "/ui_chat_log",
                "query_service": "/assistive/get_db_data",
            },
        )

    # ------------------------------------------------------------------
    # Common helpers
    # ------------------------------------------------------------------
    def _runtime_log(
        self,
        *,
        source: str,
        level: str,
        message: str,
        category: str = "system",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            append_runtime_log(
                source=source,
                level=level,
                message=message,
                category=category,
                details=details,
            )
        except OSError as error:
            self.get_logger().warning(
                f"Runtime log file write failed: {error}",
                throttle_duration_sec=5.0,
            )

    def _parse(
        self,
        message: String,
        *,
        source: str,
    ) -> dict[str, Any] | None:
        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError as error:
            self.get_logger().error(f"Invalid JSON from {source}: {error}")
            self._runtime_log(
                source=source,
                level="ERROR",
                category="communication",
                message="Invalid JSON received",
                details={"error": str(error)},
            )
            return None

        if not isinstance(payload, dict):
            self.get_logger().error(f"JSON payload from {source} must be an object")
            return None
        return payload

    @staticmethod
    def _record_name(payload: Mapping[str, Any]) -> str:
        value = (
            payload.get("record_name")
            or payload.get("object_name")
            or payload.get("class_name")
        )
        if value is None:
            raise ValueError(
                "record_name, object_name, or class_name is required"
            )
        return str(value)

    @staticmethod
    def _record_data(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if "data" in payload:
            data = payload.get("data")
            if not isinstance(data, Mapping):
                raise ValueError("data must be a JSON object")
            return data

        return {
            key: value
            for key, value in payload.items()
            if key not in {"record_name", "object_name", "replace"}
        }

    # ------------------------------------------------------------------
    # Redis query service
    # ------------------------------------------------------------------
    def get_db_data_callback(
        self,
        request: GetDbData.Request,
        response: GetDbData.Response,
    ) -> GetDbData.Response:
        data_type = request.data_type.strip().lower()
        name = request.name.strip()

        aliases = {
            "object_list": "objects",
            "list_objects": "objects",
            "fixed": "fixed_point",
            "waypoint": "fixed_point",
            "waypoints": "fixed_points",
            "list_fixed_points": "fixed_points",
            "case": "scan_case",
            "cases": "scan_cases",
            "list_scan_cases": "scan_cases",
            "conversation": "conversations",
        }
        data_type = aliases.get(data_type, data_type)

        try:
            if data_type == "object":
                if not name:
                    raise ValueError("object 조회에는 name이 필요합니다")
                result = self.store.get_object_record(name)
                if result is None:
                    return self._not_found(
                        response,
                        f"객체를 찾을 수 없습니다: {name}",
                    )
            elif data_type == "objects":
                result = self.store.list_objects()
            elif data_type == "fixed_point":
                if not name:
                    raise ValueError("fixed_point 조회에는 name이 필요합니다")
                result = self.store.get_fixed_point(name)
                if result is None:
                    return self._not_found(
                        response,
                        f"고정 좌표를 찾을 수 없습니다: {name}",
                    )
            elif data_type == "fixed_points":
                result = self.store.list_fixed_points()
            elif data_type == "scan_case":
                if not name:
                    raise ValueError("scan_case 조회에는 name이 필요합니다")
                result = self.store.get_scan_case(name)
                if result is None:
                    return self._not_found(
                        response,
                        f"스캔 CASE를 찾을 수 없습니다: {name}",
                    )
            elif data_type == "scan_cases":
                result = self.store.list_scan_cases()
            elif data_type == "conversations":
                limit = 100
                if name:
                    try:
                        limit = int(name)
                    except ValueError as error:
                        raise ValueError(
                            "conversations의 name에는 조회 개수만 입력할 수 있습니다"
                        ) from error
                result = self.store.list_conversations(limit=limit)
            else:
                raise ValueError(
                    "지원하지 않는 data_type입니다. 사용 가능: "
                    "object, objects, fixed_point, fixed_points, "
                    "scan_case, scan_cases, conversations"
                )

            response.success = True
            response.json_data = json.dumps(
                result,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            response.message = "조회 성공"

            label = f"{data_type}:{name}" if name else data_type
            self.get_logger().info(f"DB query success: {label}")
            self._runtime_log(
                source="redis_bridge",
                level="INFO",
                category="communication",
                message=f"DB query success: {label}",
            )
        except (KeyError, TypeError, ValueError) as error:
            response.success = False
            response.json_data = ""
            response.message = str(error)
            self.get_logger().warning(f"DB query failed: {error}")
            self._runtime_log(
                source="redis_bridge",
                level="WARN",
                category="communication",
                message=f"DB query failed: {error}",
            )
        except Exception as error:
            response.success = False
            response.json_data = ""
            response.message = f"DB 조회 중 오류가 발생했습니다: {error}"
            self.get_logger().error(response.message)
            self._runtime_log(
                source="redis_bridge",
                level="ERROR",
                category="communication",
                message=response.message,
            )

        return response

    @staticmethod
    def _not_found(
        response: GetDbData.Response,
        message: str,
    ) -> GetDbData.Response:
        response.success = False
        response.json_data = ""
        response.message = message
        return response

    # ------------------------------------------------------------------
    # Redis save callbacks
    # ------------------------------------------------------------------
    def object_detection_callback(self, message: String) -> None:
        payload = self._parse(message, source="object_detection")
        if payload is None:
            return

        try:
            record_name = self._record_name(payload)
            item = self.store.save_object_record(
                record_name=record_name,
                data=self._record_data(payload),
                replace=bool(payload.get("replace", False)),
            )
            field_count = len(item.get("data", {}))
            log_message = f"Object updated: {record_name} ({field_count} fields)"
            self.get_logger().info(log_message)
            self._runtime_log(
                source="object_detection",
                level="INFO",
                category="communication",
                message=log_message,
            )
        except (KeyError, TypeError, ValueError) as error:
            self.get_logger().error(f"Object payload error: {error}")
            self._runtime_log(
                source="object_detection",
                level="ERROR",
                category="communication",
                message=f"Object payload error: {error}",
            )

    def object_moved_callback(self, message: String) -> None:
        payload = self._parse(message, source="object_moved")
        if payload is None:
            return

        try:
            record_name = self._record_name(payload)
            if isinstance(payload.get("data"), Mapping):
                fields = payload["data"]
            else:
                fields = {
                    "last_moved": {
                        "destination": payload.get("destination"),
                        "position": payload.get("position"),
                        "timestamp": payload.get("timestamp", utc_now()),
                    }
                }

            self.store.update_object_fields(
                record_name=record_name,
                fields=fields,
            )
            log_message = f"Object moved data updated: {record_name}"
            self.get_logger().info(log_message)
            self._runtime_log(
                source="robot_control",
                level="INFO",
                category="communication",
                message=log_message,
            )
        except (KeyError, TypeError, ValueError) as error:
            self.get_logger().error(f"Moved payload error: {error}")
            self._runtime_log(
                source="robot_control",
                level="ERROR",
                category="communication",
                message=f"Moved payload error: {error}",
            )

    def vla_state_callback(self, message: String) -> None:
        """VLA 상태만 저장한다. 대화는 /ui_chat_log에서만 저장한다."""
        payload = self._parse(message, source="vla_state")
        if payload is None:
            return

        state = str(payload.get("state", "idle")).strip().lower() or "idle"
        status_message = str(payload.get("message", ""))

        self.store.redis.hset(
            self.USER_UI_STATE_KEY,
            mapping={
                "state": state,
                "message": status_message,
            },
        )
        self._runtime_log(
            source="vla",
            level="INFO",
            category="state",
            message=f"VLA state changed: {state}",
            details={"message": status_message},
        )

    def ui_chat_log_callback(self, message: String) -> None:
        """VLA 대화 내용만 conversation_history에 저장한다."""
        payload = self._parse(message, source="ui_chat_log")
        if payload is None:
            return

        speaker = str(payload.get("speaker", "")).strip().upper()
        text = str(payload.get("text", "")).strip()
        session_id = str(payload.get("session_id", "default")).strip() or "default"

        role_mapping = {
            "USER": "user",
            "ASSISTANT": "assistant",
        }
        role = role_mapping.get(speaker)
        if role is None:
            self.get_logger().warning(
                "ui_chat_log speaker must be USER or ASSISTANT"
            )
            return
        if not text:
            self.get_logger().warning("ui_chat_log text is empty")
            return

        self.store.append_conversation(
            role=role,
            text=text,
            session_id=session_id,
            source="ui_chat_log",
            state="chat",
        )

        ui_field = "user_text" if role == "user" else "assistant_text"
        self.store.redis.hset(
            self.USER_UI_STATE_KEY,
            mapping={ui_field: text},
        )

        # 채팅 자체는 DB conversation_history에 저장되지만,
        # 관리자 런타임 로그에는 내용이 아닌 수신 여부만 기록한다.
        self._runtime_log(
            source="vla",
            level="INFO",
            category="communication",
            message=f"UI chat received: {speaker}",
        )

    # ------------------------------------------------------------------
    # Runtime-only log callbacks (not Redis)
    # ------------------------------------------------------------------
    def hand_tracking_started_callback(self, message: Bool) -> None:
        if not message.data:
            return
        self._runtime_log(
            source="hand_tracking",
            level="INFO",
            category="communication",
            message="Hand tracking started signal received",
            details={"topic": "/hand_tracking_request", "data": True},
        )

    def hand_arrived_callback(self, message: Bool) -> None:
        if not message.data:
            return
        self._runtime_log(
            source="hand_tracking",
            level="INFO",
            category="communication",
            message="Hand arrived signal received",
            details={"topic": "/hand_arrived", "data": True},
        )

    def system_log_callback(self, message: String) -> None:
        payload = self._parse(message, source="system_log")
        if payload is None:
            return

        self._runtime_log(
            source=str(payload.get("source", "external_node")),
            level=str(payload.get("level", "INFO")),
            category=str(payload.get("category", "communication")),
            message=str(payload.get("message", "")),
            details=(
                payload.get("details")
                if isinstance(payload.get("details"), Mapping)
                else None
            ),
        )

    def rosout_callback(self, message: Log) -> None:
        node_name = str(message.name)
        message_text = str(message.msg)
        searchable = f"{node_name} {message_text}".lower()

        if self.rosout_filters and not any(
            keyword in searchable for keyword in self.rosout_filters
        ):
            return

        level_mapping = {
            10: "DEBUG",
            20: "INFO",
            30: "WARN",
            40: "ERROR",
            50: "FATAL",
        }
        level = level_mapping.get(int(message.level), str(message.level))
        timestamp = (
            f"{int(message.stamp.sec)}.{int(message.stamp.nanosec):09d}"
        )

        try:
            append_runtime_log(
                source=node_name or "rosout",
                level=level,
                category="rosout",
                message=message_text,
                timestamp=timestamp,
                details={
                    "file": str(message.file),
                    "function": str(message.function),
                    "line": int(message.line),
                },
            )
        except OSError:
            pass


def main(args=None) -> None:
    rclpy.init(args=args)
    node = RedisObjectBridge()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
