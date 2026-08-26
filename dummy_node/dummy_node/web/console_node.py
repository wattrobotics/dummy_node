"""웹 제어 콘솔 노드(`dummy_web`).

`dummy_node` 의 상태 토픽을 구독해 화면에 그리고, 화면의 조작을 **기존 ROS 서비스·액션
호출**로 바꿔 보낸다. 더미의 내부 상태를 직접 만지지 않는 것이 핵심이다 — 그래야 BT 가
쓰는 경로와 같은 경로가 검증되고, 기존 인터페이스 코드를 수정할 필요가 없다.

`dummy_node` 와 **별도 프로세스**로 뜬다. 같은 노드가 자기 서비스를 호출하는 형태를
피하기 위함이며, 덕분에 `dummy_node` 가 죽어도 웹은 살아남아 연결 끊김을 표시한다.
"""

from __future__ import annotations

import threading

import rclpy
from rcl_interfaces.srv import GetParameters
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

from example_interfaces.msg import Bool, Int32
from example_interfaces.srv import SetBool

from dummy_node_interfaces.action import CloseTrayDoor
from dummy_node_interfaces.msg import LoadCellState, PersonPresence, TrayDoorState
from dummy_node_interfaces.srv import OpenTrayDoor, SetInt, SetTrayBool

from dummy_node.web.bridge import (
    INTENT_CLOSE,
    INTENT_OPEN,
    BridgeError,
    StateStore,
)
from dummy_node.web.http_server import ConsoleHttpServer

NS = "dummy"

# CloseTrayDoor.Result.error 코드 -> 사람이 읽는 이름.
CLOSE_ERROR_NAMES = {
    CloseTrayDoor.Result.ERROR_NONE: "ERROR_NONE",
    CloseTrayDoor.Result.ERROR_OBSTRUCTED: "ERROR_OBSTRUCTED",
    CloseTrayDoor.Result.ERROR_TIMEOUT: "ERROR_TIMEOUT",
    CloseTrayDoor.Result.ERROR_ABORTED: "ERROR_ABORTED",
}


def _int_list(value, field: str) -> list[int]:
    """화면이 보낸 tray 목록을 검증한다. 빈 배열 = 전체(각 서비스의 공통 규약)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise BridgeError(f"'{field}' 는 배열이어야 합니다.")
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise BridgeError(f"'{field}' 의 원소는 정수여야 합니다: {item!r}")
        out.append(item)
    return out


def _bool(value, field: str) -> bool:
    if not isinstance(value, bool):
        raise BridgeError(f"'{field}' 는 true/false 여야 합니다.")
    return value


class DummyWebNode(Node):
    """상태 구독 + 서비스/액션 클라이언트를 보유한 ROS 노드.

    [속성 이름 규칙] 이 클래스가 만드는 비공개 속성에는 반드시 `_web_` 접두사를
    붙인다. `rclpy.node.Node` 는 `_clients`·`_services`·`_publishers` 같은 흔한
    이름을 내부 장부로 이미 쓰고 있어서, 접두사 없이 쓰면 그것을 덮어써 노드가
    기동 중에 죽는다(실제로 `_clients` 를 dict 로 덮어써 겪은 고장이다).
    `__init__` 끝의 `_assert_no_attr_collision()` 이 이 규칙을 강제한다.
    """

    def __init__(self):
        super().__init__("dummy_web", namespace=NS)
        base_attrs = set(vars(self))

        self._web_host = str(self.declare_parameter("web_host", "0.0.0.0").value)
        self._web_port = int(self.declare_parameter("web_port", 8080).value)
        self._web_timeout = float(self.declare_parameter("service_timeout_sec", 5.0).value)
        initial_tray_count = int(self.declare_parameter("tray_count", 2).value)

        self.state = StateStore(initial_tray_count)
        self._web_cbg = ReentrantCallbackGroup()

        self._setup_subscriptions()
        self._setup_clients()

        # 액션 goal 은 한 번에 하나만 다룬다 — 화면에도 진행 중인 닫기는 하나만 보인다.
        self._web_goal_lock = threading.Lock()
        self._web_goal_handle = None

        self._assert_no_attr_collision(base_attrs)

        # `notify_fail` 은 토픽으로 관찰할 수 없다. dummy_node 의 파라미터 서비스로
        # 초기값을 한 번 읽어 화면의 초기 표시를 맞춘다(실패해도 무방 -> '알 수 없음').
        threading.Thread(target=self._fetch_notify_fail, daemon=True).start()

    def _assert_no_attr_collision(self, base_attrs: set) -> None:
        """이 클래스가 만든 비공개 속성이 이름 규칙을 지키는지 기동 시 확인한다.

        `rclpy.Node` 는 `_clients`·`_services`·`_publishers` 등을 내부 장부로 쓴다.
        접두사 없이 같은 이름을 쓰면 그것을 덮어쓰는데, 노드는 그 자리에서 죽지 않고
        나중의 `create_client()` 같은 호출에서 엉뚱한 예외로 터져 원인을 찾기 어렵다.
        그래서 값 비교 대신 **이름 규칙**을 강제한다 — 규칙만 지키면 충돌은 원천적으로
        일어나지 않으며, 새 속성을 추가할 때 자동으로 검사된다.
        """
        illegal = sorted(
            name for name in vars(self)
            if name not in base_attrs
            and name.startswith("_")
            and not name.startswith("_web_")
        )
        if illegal:
            raise RuntimeError(
                "DummyWebNode 의 비공개 속성에는 `_web_` 접두사가 필요하다"
                f" (rclpy.Node 의 내부 속성과 충돌할 수 있다): {illegal}"
            )

    # ------------------------------------------------------------------ #
    # 구독 (QoS 는 발행자와 정확히 일치해야 한다 — 어긋나면 조용히 수신되지 않는다)
    # ------------------------------------------------------------------ #
    def _setup_subscriptions(self) -> None:
        volatile_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        latched_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        self.create_subscription(
            PersonPresence, "person_presence", self._on_person,
            volatile_qos, callback_group=self._web_cbg,
        )
        self.create_subscription(
            LoadCellState, "load_cell_state", self._on_load_cell,
            volatile_qos, callback_group=self._web_cbg,
        )
        self.create_subscription(
            TrayDoorState, "robot/tray_door/state", self._on_tray_door,
            latched_qos, callback_group=self._web_cbg,
        )
        self.create_subscription(
            Int32, "robot/current_floor", self._on_floor,
            10, callback_group=self._web_cbg,
        )
        self.create_subscription(
            Bool, "robot/side_door/status", self._on_side_door,
            10, callback_group=self._web_cbg,
        )

    def _stamp_age_ms(self, stamp) -> float:
        """`header.stamp` 로부터 지난 시간(ms). BT 의 staleness 판정과 같은 계산이다."""
        now_ns = self.get_clock().now().nanoseconds
        stamp_ns = stamp.sec * 1_000_000_000 + stamp.nanosec
        return (now_ns - stamp_ns) / 1_000_000.0

    def _on_person(self, msg: PersonPresence) -> None:
        self.state.set_person(msg.is_person, self._stamp_age_ms(msg.header.stamp))

    def _on_load_cell(self, msg: LoadCellState) -> None:
        trays = [
            {
                "tray": t.tray,
                "occupied": t.occupied,
                "healthy": t.healthy,
                "weight_g": round(float(t.weight_g), 1),
            }
            for t in msg.trays
        ]
        self.state.set_load_cell(trays, self._stamp_age_ms(msg.header.stamp))

    def _on_tray_door(self, msg: TrayDoorState) -> None:
        doors = [
            {
                "tray": d.tray,
                "open": d.open,
                "closed": d.closed,
                "obstructed": d.obstructed,
            }
            for d in msg.doors
        ]
        self.state.set_tray_door(doors, self._stamp_age_ms(msg.header.stamp))

    def _on_floor(self, msg: Int32) -> None:
        self.state.set_floor(msg.data)

    def _on_side_door(self, msg: Bool) -> None:
        self.state.set_side_door(msg.data)

    # ------------------------------------------------------------------ #
    # 서비스 클라이언트 (화이트리스트)
    # ------------------------------------------------------------------ #
    def _setup_clients(self) -> None:
        def client(srv_type, name):
            return self.create_client(srv_type, name, callback_group=self._web_cbg)

        self._web_service_clients = {
            "person_set": client(SetBool, "person_presence/set"),
            "load_cell_occupied": client(SetTrayBool, "load_cell/set_occupied"),
            "load_cell_healthy": client(SetTrayBool, "load_cell/set_healthy"),
            "tray_door_open": client(OpenTrayDoor, "robot/tray_door/open"),
            "tray_door_obstructed": client(SetTrayBool, "robot/tray_door/set_obstructed"),
            "floor_current": client(SetInt, "robot/set_current_floor"),
            "floor_target": client(SetInt, "robot/set_target_floor"),
            "side_door": client(SetBool, "robot/open_side_door"),
            "notify_fail": client(SetBool, "notify/set_fail"),
        }
        self._web_close_client = ActionClient(
            self, CloseTrayDoor, "robot/tray_door/close", callback_group=self._web_cbg
        )
        self._web_param_client = client(GetParameters, "dummy_node/get_parameters")

    def call_service(self, key: str, body: dict) -> dict:
        """화면의 조작 하나를 서비스 호출로 바꾼다.

        `key` 는 이 노드가 가진 목록으로만 해석한다. 브라우저가 임의의 ROS 서비스
        이름을 넘겨 호출하는 것을 막기 위함이다(웹이 0.0.0.0 으로 열려 있다).
        """
        client = self._web_service_clients.get(key)
        if client is None:
            raise BridgeError(f"알 수 없는 제어 대상: {key}")

        request = self._build_request(key, body)
        response = self._call_sync(client, request)
        self._after_call(key, body, response)

        out = {"success": bool(response.success), "message": response.message}
        for field in ("applied", "accepted", "current_floor"):
            if hasattr(response, field):
                value = getattr(response, field)
                out[field] = list(value) if hasattr(value, "__iter__") else value
        return out

    def _build_request(self, key: str, body: dict):
        if key in ("person_set", "side_door", "notify_fail"):
            request = SetBool.Request()
            request.data = _bool(body.get("value"), "value")
            return request

        if key in ("load_cell_occupied", "load_cell_healthy", "tray_door_obstructed"):
            request = SetTrayBool.Request()
            request.trays = _int_list(body.get("trays"), "trays")
            request.value = _bool(body.get("value"), "value")
            return request

        if key == "tray_door_open":
            request = OpenTrayDoor.Request()
            request.trays = _int_list(body.get("trays"), "trays")
            return request

        if key in ("floor_current", "floor_target"):
            value = body.get("value")
            if isinstance(value, bool) or not isinstance(value, int):
                raise BridgeError("'value' 는 정수여야 합니다.")
            if value == 0:
                # 서버도 막는다 — 화면을 거치지 않은 요청이 들어올 수 있다.
                raise BridgeError("0층은 존재하지 않습니다.")
            request = SetInt.Request()
            request.value = value
            return request

        raise BridgeError(f"요청을 만들 수 없습니다: {key}")

    def _after_call(self, key: str, body: dict, response) -> None:
        """호출이 성공했을 때 화면 표시에만 쓰는 부수 상태를 갱신한다."""
        if not response.success:
            self.state.add_log("warn", f"{key} 거부됨: {response.message}")
            return

        if key == "tray_door_open":
            self.state.set_door_intent(list(response.accepted), INTENT_OPEN)
        elif key == "notify_fail":
            self.state.set_notify_fail(bool(body.get("value")))

    # ------------------------------------------------------------------ #
    # 액션 (문 닫기)
    # ------------------------------------------------------------------ #
    def send_close_goal(self, trays: list[int], force: bool) -> dict:
        if not self._web_close_client.wait_for_server(timeout_sec=1.0):
            raise BridgeError("액션 서버 미연결: robot/tray_door/close")

        with self._web_goal_lock:
            if self._web_goal_handle is not None:
                raise BridgeError("이미 진행 중인 닫기 goal 이 있습니다. 먼저 취소하세요.")

        goal = CloseTrayDoor.Goal()
        goal.trays = trays
        goal.force = force

        # 대상이 빈 배열이면 전체 tray 다(액션 규약). 화면 표시를 위해 풀어 둔다.
        targets = trays or list(range(self.state.snapshot()["tray_count"]))
        self.state.set_door_intent(targets, INTENT_CLOSE)
        self.state.update_close_job(
            active=True, trays=targets, force=force,
            remaining=targets, obstructed=False, last_result=None,
        )

        send_future = self._web_close_client.send_goal_async(
            goal, feedback_callback=self._on_close_feedback
        )
        goal_handle = self._wait(send_future, "goal 전송")

        if not goal_handle.accepted:
            self.state.update_close_job(active=False, remaining=[])
            self.state.add_log("warn", "닫기 goal 이 거부되었습니다.")
            raise BridgeError("닫기 goal 이 거부되었습니다.")

        with self._web_goal_lock:
            self._web_goal_handle = goal_handle
        goal_handle.get_result_async().add_done_callback(self._on_close_result)

        self.state.add_log("info", f"닫기 개시: tray {targets}, force={force}")
        return {"accepted": True, "trays": targets, "force": force}

    def cancel_close_goal(self) -> dict:
        with self._web_goal_lock:
            handle = self._web_goal_handle
        if handle is None:
            raise BridgeError("진행 중인 닫기 goal 이 없습니다.")

        self._wait(handle.cancel_goal_async(), "goal 취소")
        self.state.add_log("info", "닫기 취소 요청을 보냈습니다.")
        return {"canceled": True}

    def _on_close_feedback(self, feedback_msg) -> None:
        feedback = feedback_msg.feedback
        self.state.update_close_job(
            remaining=list(feedback.remaining),
            obstructed=bool(feedback.obstructed),
        )

    def _on_close_result(self, future) -> None:
        with self._web_goal_lock:
            self._web_goal_handle = None

        try:
            result = future.result().result
        except Exception as exc:  # noqa: BLE001 - 결과 수신 실패도 화면에 남긴다
            self.state.update_close_job(active=False, remaining=[])
            self.state.add_log("error", f"닫기 결과 수신 실패: {exc}")
            return

        error_name = CLOSE_ERROR_NAMES.get(result.error, str(result.error))
        self.state.update_close_job(
            active=False,
            remaining=list(result.failed),
            last_result={
                "success": bool(result.success),
                "error": int(result.error),
                "error_name": error_name,
                "closed": list(result.closed),
                "failed": list(result.failed),
                "message": result.message,
            },
        )

        if not result.success and result.error in (
            CloseTrayDoor.Result.ERROR_OBSTRUCTED,
            CloseTrayDoor.Result.ERROR_ABORTED,
        ):
            # 더미는 끼임·취소 시 문을 **열림 방향으로 복귀**시킨다. 화면의 이동 방향
            # 표시도 그에 맞춰 뒤집어야 `closing` 으로 잘못 보이지 않는다.
            self.state.set_door_intent(list(result.failed), INTENT_OPEN)

        level = "info" if result.success else "warn"
        self.state.add_log(level, f"닫기 결과: {error_name} — {result.message}")

    # ------------------------------------------------------------------ #
    # 파라미터 초기값 조회
    # ------------------------------------------------------------------ #
    def _fetch_notify_fail(self) -> None:
        """`notify_fail` 은 서비스 서버라 토픽으로 관찰할 수 없다.

        dummy_node 의 파라미터 서비스로 기동 시 값을 한 번 읽어 초기 표시를 맞춘다.
        읽지 못하면 `None` 으로 두고 화면에 '알 수 없음' 으로 표시한다.
        """
        try:
            if not self._web_param_client.wait_for_service(timeout_sec=10.0):
                return
            request = GetParameters.Request()
            request.names = ["notify_fail"]
            response = self._call_sync(self._web_param_client, request)
            values = response.values
            if values and values[0].type == 1:  # PARAMETER_BOOL
                self.state.set_notify_fail(values[0].bool_value)
        except Exception as exc:  # noqa: BLE001 - 초기 표시용이라 실패해도 계속 간다
            self.get_logger().warn(f"notify_fail 초기값 조회 실패: {exc}")

    # ------------------------------------------------------------------ #
    # 호출 헬퍼
    # ------------------------------------------------------------------ #
    def _call_sync(self, client, request):
        """HTTP 핸들러 스레드에서 서비스 응답을 기다린다.

        `spin_until_future_complete()` 를 쓰면 안 된다 — executor 가 이미 다른
        스레드에서 spin 중이므로 교착하거나 예외가 난다. future 완료 콜백에서
        Event 를 세우고 그것만 기다리면, 응답 처리는 executor 가 대신 해 준다.
        """
        if not client.wait_for_service(timeout_sec=1.0):
            raise BridgeError(f"서비스 미연결: {client.srv_name}")
        return self._wait(client.call_async(request), client.srv_name)

    def _wait(self, future, what: str):
        done = threading.Event()
        future.add_done_callback(lambda _f: done.set())
        if not done.wait(self._web_timeout):
            future.cancel()
            raise BridgeError(f"응답 시간 초과({self._web_timeout}s): {what}")
        return future.result()

    # ------------------------------------------------------------------ #
    @property
    def http_bind(self) -> tuple[str, int]:
        return self._web_host, self._web_port


def main(args=None):
    rclpy.init(args=args)
    node = DummyWebNode()

    host, port = node.http_bind
    server = ConsoleHttpServer(node, host, port)
    server.start()
    node.get_logger().info(
        f"웹 콘솔 대기: http://{'localhost' if host == '0.0.0.0' else host}:{port}"
        + (" (같은 망의 다른 기기에서도 접속 가능)" if host == "0.0.0.0" else "")
    )

    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
