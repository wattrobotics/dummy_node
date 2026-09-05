"""웹 제어 콘솔 노드(`dummy_web`).

`dummy_node` 의 상태 토픽을 구독해 화면에 그리고, 화면의 조작을 **기존 ROS 서비스·액션
호출**로 바꿔 보낸다. 더미의 내부 상태를 직접 만지지 않는 것이 핵심이다 — 그래야 BT 가
쓰는 경로와 같은 경로가 검증되고, 기존 인터페이스 코드를 수정할 필요가 없다.

`dummy_node` 와 **별도 프로세스**로 뜬다. 같은 노드가 자기 서비스를 호출하는 형태를
피하기 위함이며, 덕분에 `dummy_node` 가 죽어도 웹은 살아남아 연결 끊김을 표시한다.
"""

from __future__ import annotations

import errno
import functools
import re
import sys
import threading

import rclpy
from rcl_interfaces.msg import ParameterType
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

from action_msgs.msg import GoalStatus
from example_interfaces.msg import Bool, Int32
from example_interfaces.srv import SetBool
from std_msgs.msg import String

from dummy_node_interfaces.action import CloseTrayDoor
from dummy_node_interfaces.msg import LoadCellState, PersonPresence, TrayDoorState
from dummy_node_interfaces.srv import OpenTrayDoor, SetInt, SetTrayBool
# 실물 모사 인터페이스(interfaces/phidget_load_cell.py · side_door.py)가 내는 실물 계약.
from phidgets_hw.msg import LoadCellState as PhidgetLoadCellState
from phidgets_hw.srv import ConfirmLoad, ConfirmUnload, SetTracking, Tare
from w_ros2_controller_interfaces.action import DoorCommand

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

# DoorCommand.Result.error_code -> 이름. .action 의 ERROR_* 상수에서 만들어 두 곳이 갈리지 않게 한다.
DOOR_ERROR_NAMES = {
    getattr(DoorCommand.Result, name): name
    for name in dir(DoorCommand.Result)
    if name.startswith("ERROR_")
}
DOOR_COMMANDS = (
    DoorCommand.Goal.OPEN,
    DoorCommand.Goal.CLOSE,
    DoorCommand.Goal.UNLOCK,
    DoorCommand.Goal.CALIBRATION,
)
GOAL_STATUS_NAMES = {
    GoalStatus.STATUS_SUCCEEDED: "SUCCEEDED",
    GoalStatus.STATUS_CANCELED: "CANCELED",
    GoalStatus.STATUS_ABORTED: "ABORTED",
}
# 응답 message 로 tracking 표시를 갱신하는 서비스. tracking 은 토픽에 없다.
TRACKING_KEYS = ("phidget_set_tracking", "phidget_confirm_load", "phidget_confirm_unload")


def _tracking_from_message(message: str) -> dict[int, bool]:
    """실물 로드셀 서비스 응답에서 tray 별 tracking 상태를 읽는다.

    형식은 실물 노드와 같다: "tray_0: tracking resumed; tray_1: value frozen".
    confirm_* 는 "…, frozen" 으로 끝난다. "unchanged" 는 정보가 없어 건너뛴다.
    """
    out: dict[int, bool] = {}
    for segment in message.split(";"):
        match = re.match(r"\s*tray_(\d+):\s*(.*)", segment)
        if not match:
            continue
        text = match.group(2)
        if "frozen" in text:
            out[int(match.group(1))] = False
        elif "resumed" in text:
            out[int(match.group(1))] = True
    return out


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
        # 희망 포트가 쓰이고 있을 때 다음 포트로 몇 번까지 옮겨 볼지. 1 = 옮기지 않는다.
        self._web_port_tries = int(self.declare_parameter("web_port_max_tries", 10).value)
        initial_tray_count = int(self.declare_parameter("tray_count", 2).value)
        # 실물 문 컨트롤러 이름(인덱스 = tray). status 구독과 command 클라이언트를 이 이름으로
        # 만들므로 dummy_node 쪽 side_door_controllers 와 같아야 한다.
        self._web_side_door_names = [
            str(n) for n in self.declare_parameter(
                "side_door_controllers",
                ["side_door_top_controller", "side_door_bottom_controller"],
            ).value
        ]

        self.state = StateStore(initial_tray_count, self._web_side_door_names)
        self._web_cbg = ReentrantCallbackGroup()

        self._setup_subscriptions()
        self._setup_clients()

        # 액션 goal 은 한 번에 하나만 다룬다 — 화면에도 진행 중인 닫기는 하나만 보인다.
        self._web_goal_lock = threading.Lock()
        self._web_goal_handle = None
        # 실물 문 goal 은 문마다 하나씩 추적한다. 진행 중에 새 goal 을 보내면 컨트롤러가
        # 기존 goal 을 선점(abort)한다 — 실물 계약이므로 막지 않고 그 결과를 그대로 보여 준다.
        self._web_door_handles = [None] * len(self._web_side_door_names)

        # `notify_fail` 은 토픽으로 관찰할 수 없다. dummy_node 의 파라미터 서비스로
        # 초기값을 한 번 읽어 화면의 초기 표시를 맞춘다(실패해도 무방 -> '알 수 없음').
        #
        # 이 조회를 **별도 스레드에서 하지 않는다.** 스레드가 rclpy 객체를 잡고 있는
        # 사이에 기동이 실패해 인터프리터가 종료되면, C 확장 객체가 파괴되는 것과
        # 겹쳐 SIGSEGV 로 죽는다(포트 점유로 기동에 실패했을 때 실제로 겪었다).
        # executor 타이머로 옮기면 스레드 경계가 사라져 그 경합 자체가 없어진다.
        self._web_param_attempts = 0
        self._web_param_timer = self.create_timer(
            1.0, self._poll_notify_fail, callback_group=self._web_cbg
        )

        self._assert_no_attr_collision(base_attrs)

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

        # 실물 모사 인터페이스는 실물 이름(절대 이름)을 낸다 — README 의 네임스페이스 예외.
        self.create_subscription(
            PhidgetLoadCellState, "/load_cell_state", self._on_phidget_load_cell,
            volatile_qos, callback_group=self._web_cbg,
        )
        for idx, ctrl in enumerate(self._web_side_door_names):
            # 실물 컨트롤러는 SystemDefaultsQoS(reliable · volatile · depth 10)로 낸다.
            self.create_subscription(
                String, f"/{ctrl}/status",
                functools.partial(self._on_side_door_status, idx),
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

    def _on_phidget_load_cell(self, msg: PhidgetLoadCellState) -> None:
        trays = [
            {
                "tray": t.tray,
                "occupied": t.occupied,
                "healthy": t.healthy,
                "weight_g": round(float(t.weight_g), 1),
            }
            for t in msg.trays
        ]
        self.state.set_phidget_load_cell(trays, self._stamp_age_ms(msg.header.stamp))

    def _on_side_door_status(self, idx: int, msg: String) -> None:
        self.state.set_side_door_status(idx, msg.data)

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
            # 실물 모사 — 실물 계약은 절대 이름, 더미 조작은 규칙대로 /dummy/ 아래.
            "phidget_set_tracking": client(SetTracking, "/phidget_load_cell/set_tracking"),
            "phidget_confirm_load": client(ConfirmLoad, "/phidget_load_cell/confirm_load"),
            "phidget_confirm_unload": client(ConfirmUnload, "/phidget_load_cell/confirm_unload"),
            "phidget_tare": client(Tare, "/phidget_load_cell/tare"),
            "phidget_occupied": client(SetTrayBool, "phidget_load_cell/set_occupied"),
            "phidget_healthy": client(SetTrayBool, "phidget_load_cell/set_healthy"),
            "side_door_obstructed": client(SetTrayBool, "side_door/set_obstructed"),
            "side_door_manual": client(SetTrayBool, "side_door/manual_move"),
        }
        self._web_close_client = ActionClient(
            self, CloseTrayDoor, "robot/tray_door/close", callback_group=self._web_cbg
        )
        # 실물 문은 컨트롤러(문)마다 액션 서버가 따로다.
        self._web_door_clients = [
            ActionClient(self, DoorCommand, f"/{ctrl}/command", callback_group=self._web_cbg)
            for ctrl in self._web_side_door_names
        ]
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

        if key in (
            "load_cell_occupied", "load_cell_healthy", "tray_door_obstructed",
            "phidget_occupied", "phidget_healthy", "side_door_obstructed", "side_door_manual",
        ):
            request = SetTrayBool.Request()
            request.trays = _int_list(body.get("trays"), "trays")
            request.value = _bool(body.get("value"), "value")
            return request

        if key == "tray_door_open":
            request = OpenTrayDoor.Request()
            request.trays = _int_list(body.get("trays"), "trays")
            return request

        if key == "phidget_set_tracking":
            request = SetTracking.Request()
            request.trays = _int_list(body.get("trays"), "trays")
            request.enable = _bool(body.get("value"), "value")
            return request

        if key in ("phidget_confirm_load", "phidget_confirm_unload", "phidget_tare"):
            srv_type = {
                "phidget_confirm_load": ConfirmLoad,
                "phidget_confirm_unload": ConfirmUnload,
                "phidget_tare": Tare,
            }[key]
            request = srv_type.Request()
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
        if key in TRACKING_KEYS:
            # 부분 성공(일부 tray 만 동결)이 있으므로 성공 여부와 무관하게 응답 전문을 읽는다.
            self.state.set_tracking(_tracking_from_message(response.message))

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
    # 액션 (실물 문 명령 DoorCommand — 문마다 서버가 따로다)
    # ------------------------------------------------------------------ #
    def send_door_goal(self, idx: int, command: str) -> dict:
        """실물 문 하나에 DoorCommand goal 을 보낸다. 완료·실패는 결과 콜백이 화면에 남긴다."""
        client = self._door_client(idx)
        if command not in DOOR_COMMANDS:
            raise BridgeError(
                f"계약에 없는 문 명령: {command!r} (open / close / unlock / calibration)"
            )
        name = self._web_side_door_names[idx]
        if not client.wait_for_server(timeout_sec=1.0):
            raise BridgeError(f"액션 서버 미연결: /{name}/command")

        goal = DoorCommand.Goal()
        goal.command = command
        send_future = client.send_goal_async(
            goal, feedback_callback=functools.partial(self._on_door_feedback, idx)
        )
        goal_handle = self._wait(send_future, f"{name} goal 전송")
        if not goal_handle.accepted:
            self.state.add_log("warn", f"{name}: '{command}' goal 이 거부되었습니다.")
            raise BridgeError(f"{name}: goal 이 거부되었습니다.")

        with self._web_goal_lock:
            preempting = self._web_door_handles[idx] is not None
            self._web_door_handles[idx] = goal_handle
        self.state.update_door_job(
            idx, active=True, command=command, state=None, elapsed=0.0, retries=0,
            last_result=None,
        )
        goal_handle.get_result_async().add_done_callback(
            functools.partial(self._on_door_result, idx, goal_handle)
        )
        self.state.add_log(
            "info", f"{name}: '{command}' 개시" + (" (진행 중 goal 선점)" if preempting else "")
        )
        return {"accepted": True, "door": idx, "command": command}

    def cancel_door_goal(self, idx: int) -> dict:
        self._door_client(idx)
        with self._web_goal_lock:
            handle = self._web_door_handles[idx]
        if handle is None:
            raise BridgeError("진행 중인 goal 이 없습니다.")
        name = self._web_side_door_names[idx]
        self._wait(handle.cancel_goal_async(), f"{name} goal 취소")
        self.state.add_log("info", f"{name}: 취소 요청을 보냈습니다.")
        return {"canceled": True, "door": idx}

    def _door_client(self, idx):
        if isinstance(idx, bool) or not isinstance(idx, int) or not (
            0 <= idx < len(self._web_door_clients)
        ):
            raise BridgeError(f"없는 문 인덱스: {idx!r} (0..{len(self._web_door_clients) - 1})")
        return self._web_door_clients[idx]

    def _on_door_feedback(self, idx: int, feedback_msg) -> None:
        fb = feedback_msg.feedback
        self.state.update_door_job(
            idx, state=fb.state, elapsed=round(float(fb.elapsed), 1),
            retries=int(fb.obstruction_retries),
        )

    def _on_door_result(self, idx: int, goal_handle, future) -> None:
        with self._web_goal_lock:
            is_current = self._web_door_handles[idx] is goal_handle
            if is_current:
                self._web_door_handles[idx] = None
        name = self._web_side_door_names[idx]

        try:
            wrapped = future.result()
            result = wrapped.result
        except Exception as exc:  # noqa: BLE001 - 결과 수신 실패도 화면에 남긴다
            if is_current:
                self.state.update_door_job(idx, active=False)
            self.state.add_log("error", f"{name}: 결과 수신 실패: {exc}")
            return

        last = {
            "status": GOAL_STATUS_NAMES.get(wrapped.status, str(wrapped.status)),
            "success": bool(result.success),
            "error_code": int(result.error_code),
            "error_name": DOOR_ERROR_NAMES.get(result.error_code, str(result.error_code)),
            "message": result.message,
        }
        # 선점된 옛 goal 의 결과는 이력으로만 남긴다 — 진행 표시는 새 goal 의 것이다.
        fields = {"last_result": last}
        if is_current:
            fields["active"] = False
        self.state.update_door_job(idx, **fields)
        level = "info" if result.success else "warn"
        self.state.add_log(
            level, f"{name}: {last['status']} · {last['error_name']} — {result.message}"
        )

    # ------------------------------------------------------------------ #
    # 파라미터 초기값 조회
    # ------------------------------------------------------------------ #
    # 파라미터 서비스는 dummy_node 가 늦게 뜨면 한동안 준비되지 않는다. 준비될
    # 때까지 짧게 폴링하되 **한 번도 블록하지 않는다** — 블록하면 executor 스레드를
    # 붙잡아 그 사이 들어온 요청이 밀린다.
    PARAM_POLL_LIMIT = 15  # 초. 이 시간까지 못 읽으면 '알 수 없음' 으로 둔다

    def _poll_notify_fail(self) -> None:
        """파라미터 서비스가 준비되면 `notify_fail` 을 한 번 읽고 폴링을 끝낸다."""
        self._web_param_attempts += 1

        if not self._web_param_client.service_is_ready():
            if self._web_param_attempts >= self.PARAM_POLL_LIMIT:
                self._web_param_timer.cancel()
                self.get_logger().warn(
                    "notify_fail 초기값을 읽지 못했다(파라미터 서비스 미준비). "
                    "화면에는 '알 수 없음' 으로 표시되며, 한 번 토글하면 정확해진다."
                )
            return

        self._web_param_timer.cancel()
        request = GetParameters.Request()
        # tracking_on_startup 도 함께 읽는다 — 실물 로드셀 모사의 tracking 은 토픽에 없어
        # 초기 표시를 파라미터로만 맞출 수 있다(notify_fail 과 같은 사정).
        request.names = ["notify_fail", "tracking_on_startup"]
        self._web_param_client.call_async(request).add_done_callback(
            self._on_notify_fail_param
        )

    def _on_notify_fail_param(self, future) -> None:
        """파라미터 응답을 화면 표시값에 반영한다. 실패해도 노드는 계속 간다."""
        try:
            values = future.result().values
        except Exception as exc:  # noqa: BLE001 - 초기 표시용이라 실패해도 계속 간다
            self.get_logger().warn(f"notify_fail 초기값 조회 실패: {exc}")
            return

        if values and values[0].type == ParameterType.PARAMETER_BOOL:
            self.state.set_notify_fail(values[0].bool_value)
        if len(values) > 1 and values[1].type == ParameterType.PARAMETER_BOOL:
            count = self.state.snapshot()["tray_count"]
            self.state.set_tracking({i: values[1].bool_value for i in range(count)})

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
    def http_bind(self) -> tuple[str, int, int]:
        return self._web_host, self._web_port, self._web_port_tries


def _report_bind_failure(logger, exc: OSError, host: str, port: int, tries: int) -> None:
    """웹 콘솔 소켓을 열지 못한 이유를 사람이 읽고 바로 조치할 수 있게 남긴다.

    raw traceback 만 남기면 "무엇을 해야 하는지" 를 알 수 없다. 특히 포트 점유는
    이전 dummy_web 이 남아 있는 흔한 상황이라 조치까지 함께 적는다.
    """
    if exc.errno == errno.EADDRINUSE:
        logger.error(
            f"웹 콘솔을 열 수 없다 — {port} 부터 {port + tries - 1} 까지 모두 사용 중이다.\n"
            f"  무엇이 쓰는지 확인 : ss -ltnp 'sport >= :{port}'\n"
            f"  이전 dummy_web 정리: pkill -f dummy_web\n"
            f"  다른 포트 대역 사용: ros2 run dummy_node dummy_web "
            f"--ros-args -p web_port:=9000\n"
            f"  기본 포트 변경     : config/dummy_node.yaml 의 web_port\n"
            f"  웹 없이 실행       : ros2 launch dummy_node dummy_node.launch.py "
            f"enable_web:=false"
        )
    elif exc.errno == errno.EACCES:
        logger.error(
            f"웹 콘솔을 열 수 없다 — 포트 {port} 에 접근할 권한이 없다.\n"
            f"  1024 미만 포트는 관리자 권한이 필요하다. web_port 를 1024 이상으로 바꾼다."
        )
    elif exc.errno == errno.EADDRNOTAVAIL:
        logger.error(
            f"웹 콘솔을 열 수 없다 — '{host}' 는 이 머신의 주소가 아니다.\n"
            f"  config 의 web_host 를 0.0.0.0(전체 개방) 또는 127.0.0.1(로컬 전용)로 바꾼다."
        )
    else:
        logger.error(f"웹 콘솔을 열 수 없다 — {host}:{port} 바인딩 실패: {exc}")


def main(args=None) -> int:
    rclpy.init(args=args)

    node = None
    server = None
    exit_code = 0
    try:
        node = DummyWebNode()
        host, port, tries = node.http_bind

        try:
            server = ConsoleHttpServer(node, host, port, tries)
        except OSError as exc:
            # 여기서 그냥 예외를 올리면 정리 없이 인터프리터가 끝나고, rclpy 객체가
            # 파괴되는 것과 겹쳐 SIGSEGV 로 죽는다. 반드시 finally 를 거쳐 나간다.
            _report_bind_failure(node.get_logger(), exc, host, port, tries)
            return 1

        server.start()

        if server.port != port:
            # 접속할 주소가 달라졌으므로 조용히 넘어가면 안 된다. 특히 점유의 원인이
            # 이전 dummy_web 이라면, 희망 포트로 접속했을 때 **옛 인스턴스**에
            # 붙게 되므로 그 사실까지 알린다.
            node.get_logger().warn(
                f"포트 {port} 이(가) 사용 중이어서 {server.port} 로 열었다. "
                f"{port} 에는 다른 프로세스(이전 dummy_web 일 수 있다)가 붙어 있으니 "
                f"접속 주소를 혼동하지 않도록 주의한다."
            )

        shown_host = "localhost" if host == "0.0.0.0" else host
        node.get_logger().info(
            f"웹 콘솔 대기: http://{shown_host}:{server.port}"
            + (" (같은 망의 다른 기기에서도 접속 가능)" if host == "0.0.0.0" else "")
        )

        executor = MultiThreadedExecutor()
        executor.add_node(node)
        executor.spin()
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001 - 어떤 실패든 정리를 거쳐 나간다
        exit_code = 1
        if node is not None:
            node.get_logger().error(f"웹 콘솔 노드가 실패했다: {exc}")
        else:
            print(f"[dummy_web] 노드를 만들지 못했다: {exc}", file=sys.stderr)
    finally:
        # 정리 순서가 중요하다. HTTP 스레드를 먼저 멈춰야 그 스레드가 이미 파괴된
        # 노드로 ROS 호출을 시도하는 상황이 생기지 않는다.
        if server is not None:
            server.stop()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
