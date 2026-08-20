"""적재함 문(tray door) 스텁 인터페이스.

`TakeParcelScreen.md` §3.3 규격의 더미다. 기존 `robot_side_door`(단일 문)는 tray 개념이
없어 이 스펙을 만족하지 못하므로 tray 단위로 새로 만든다. BT 는 이 인터페이스만 쓴다 —
두 경로를 동시에 지원하면 문 상태의 정본이 둘이 된다.

- Service (OpenTrayDoor) `robot/tray_door/open`  -> /dummy/robot/tray_door/open
    열기 **개시**만 지시한다. `success` 는 수락 여부이며 열렸다는 뜻이 아니다.
- Action  (CloseTrayDoor) `robot/tray_door/close` -> /dummy/robot/tray_door/close
    닫기. 진행 중 취소 가능, 끼임 감지 시 중단(`force=false` 기본).
- Publisher (TrayDoorState) `robot/tray_door/state` -> /dummy/robot/tray_door/state
    전 tray 문 상태를 주기 발행. QoS 는 스펙대로 reliable · depth 1 · **transient_local**.

문 동작 모형: 각 문은 `closed -> opening -> open -> closing -> closed` 를 오간다.
`opening`/`closing` 중에는 `open` 과 `closed` 가 **둘 다 false**(= 이동 중)다.
끼임(`obstructed`) 중에는 닫힘이 진행되지 않는다 — `force=true` 로 계속 시도하면
`door_close_timeout_sec` 에 걸려 `ERROR_TIMEOUT` 이 된다. 이는 실기의 "끼임이 해소되지
않으면 결국 닫지 못한다" 를 그대로 모사한 것이다.
"""

import time

from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.duration import Duration
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

from dummy_node_interfaces.action import CloseTrayDoor
from dummy_node_interfaces.msg import TrayDoor, TrayDoorState
from dummy_node_interfaces.srv import OpenTrayDoor, SetTrayBool

from dummy_node.registry import InterfaceBase, register

CLOSED = "closed"
OPENING = "opening"
OPEN = "open"
CLOSING = "closing"


@register
class TrayDoorInterface(InterfaceBase):
    name = "tray_door"

    srv_open = "robot/tray_door/open"                  # -> /dummy/robot/tray_door/open
    action_close = "robot/tray_door/close"             # -> /dummy/robot/tray_door/close
    state_topic = "robot/tray_door/state"              # -> /dummy/robot/tray_door/state
    srv_obstruct = "robot/tray_door/set_obstructed"    # -> /dummy/robot/tray_door/set_obstructed

    poll_sec = 0.05  # 액션 진행 폴링 주기.

    def setup(self) -> None:
        # tray_count 는 load_cell 과 공유하는 파라미터다 -> param() 헬퍼 필수.
        self._tray_count = int(self.param("tray_count", 2))
        self._state_period_sec = float(self.param("tray_door_state_period_sec", 0.2))
        self._open_duration_sec = float(self.param("door_open_duration_sec", 2.0))
        self._close_duration_sec = float(self.param("door_close_duration_sec", 2.0))
        self._close_timeout_sec = float(self.param("door_close_timeout_sec", 10.0))
        initial_open = bool(self.param("initial_doors_open", False))

        # tray -> {"state": str, "deadline": Time|None, "obstructed": bool}
        self._doors = {
            tray: {
                "state": OPEN if initial_open else CLOSED,
                "deadline": None,
                "obstructed": False,
            }
            for tray in range(self._tray_count)
        }

        # 스펙 §3.3.3: reliable, depth 1, transient_local + 주기 재발행.
        # 문은 지속 상태이므로 BT 가 기동 직후 판정할 수 있어야 한다.
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self._pub = self.node.create_publisher(
            TrayDoorState, self.state_topic, qos, callback_group=self.callback_group
        )
        self._srv_open = self.node.create_service(
            OpenTrayDoor,
            self.srv_open,
            self._on_open,
            callback_group=self.callback_group,
        )
        self._srv_obstruct = self.node.create_service(
            SetTrayBool,
            self.srv_obstruct,
            self._on_set_obstructed,
            callback_group=self.callback_group,
        )
        self._server = ActionServer(
            self.node,
            CloseTrayDoor,
            self.action_close,
            execute_callback=self._execute_close,
            goal_callback=self._on_goal,
            cancel_callback=self._on_cancel,
            callback_group=self.callback_group,
        )
        self._timer = self.node.create_timer(
            self._state_period_sec, self._on_tick, callback_group=self.callback_group
        )

        self.log.info(
            f"[{self.name}] tray {self._tray_count}단, "
            f"초기 상태={'열림' if initial_open else '닫힘'}, "
            f"열기 {self._open_duration_sec}s / 닫기 {self._close_duration_sec}s, "
            f"닫기 타임아웃 {self._close_timeout_sec}s"
        )
        self._publish()

    # ------------------------------------------------------------------ #
    # 주기 콜백: 이동 진행 + 상태 발행
    # ------------------------------------------------------------------ #
    def _on_tick(self) -> None:
        now = self.node.get_clock().now()
        for door in self._doors.values():
            if door["state"] == OPENING:
                if door["deadline"] is not None and now >= door["deadline"]:
                    door["state"] = OPEN
                    door["deadline"] = None
            elif door["state"] == CLOSING:
                if door["obstructed"]:
                    # 끼임 중에는 진행하지 않는다. 남은 시간을 그대로 유지한다.
                    door["deadline"] = now + Duration(
                        seconds=self._state_period_sec
                    )
                elif door["deadline"] is not None and now >= door["deadline"]:
                    door["state"] = CLOSED
                    door["deadline"] = None
        self._publish()

    # ------------------------------------------------------------------ #
    # 문 열기 (Service)
    # ------------------------------------------------------------------ #
    def _on_open(
        self, request: OpenTrayDoor.Request, response: OpenTrayDoor.Response
    ) -> OpenTrayDoor.Response:
        targets, invalid = self._resolve_trays(request.trays)
        if invalid:
            response.success = False
            response.message = (
                f"존재하지 않는 tray: {invalid} (유효 범위 0..{self._tray_count - 1})"
            )
            response.accepted = []
            self.log.warn(f"[{self.name}] 열기 거부: {response.message}")
            return response

        now = self.node.get_clock().now()
        for tray in targets:
            door = self._doors[tray]
            if door["state"] == OPEN:
                continue  # 이미 완전 열림 — 재개시하지 않는다.
            door["state"] = OPENING
            door["deadline"] = now + Duration(seconds=self._open_duration_sec)

        self.log.info(f"[{self.name}] 열기 개시: tray {targets}")
        self._publish()

        # success 는 **수락 여부**다. 열림 완료는 TrayDoorState 로만 판정한다.
        response.success = True
        response.message = f"opening started for {targets}"
        response.accepted = targets
        return response

    # ------------------------------------------------------------------ #
    # 끼임 감지 주입 (Service, 더미 제어용)
    # ------------------------------------------------------------------ #
    def _on_set_obstructed(
        self, request: SetTrayBool.Request, response: SetTrayBool.Response
    ) -> SetTrayBool.Response:
        targets, invalid = self._resolve_trays(request.trays)
        if invalid:
            response.success = False
            response.message = (
                f"존재하지 않는 tray: {invalid} (유효 범위 0..{self._tray_count - 1})"
            )
            response.applied = []
            self.log.warn(f"[{self.name}] 거부: {response.message}")
            return response

        for tray in targets:
            self._doors[tray]["obstructed"] = bool(request.value)
        self.log.info(f"[{self.name}] tray {targets} obstructed -> {request.value}")
        self._publish()

        response.success = True
        response.message = f"obstructed set to {request.value} for {targets}"
        response.applied = targets
        return response

    # ------------------------------------------------------------------ #
    # 문 닫기 (Action)
    # ------------------------------------------------------------------ #
    def _on_goal(self, goal_request) -> GoalResponse:
        self.log.info(
            f"[{self.name}] 닫기 goal 수신: trays={list(goal_request.trays)}, "
            f"force={goal_request.force}"
        )
        return GoalResponse.ACCEPT

    def _on_cancel(self, goal_handle) -> CancelResponse:
        self.log.info(f"[{self.name}] 닫기 cancel 요청 수신")
        return CancelResponse.ACCEPT

    def _execute_close(self, goal_handle):
        goal = goal_handle.request
        targets, invalid = self._resolve_trays(goal.trays)

        if invalid:
            goal_handle.abort()
            return self._close_result(
                success=False,
                closed=[],
                failed=[int(t) for t in goal.trays],
                error=CloseTrayDoor.Result.ERROR_ABORTED,
                message=(
                    f"존재하지 않는 tray: {invalid} "
                    f"(유효 범위 0..{self._tray_count - 1})"
                ),
            )

        # 닫기 개시. 이미 닫힌 문은 그대로 둔다.
        now = self.node.get_clock().now()
        for tray in targets:
            door = self._doors[tray]
            if door["state"] == CLOSED:
                continue
            door["state"] = CLOSING
            door["deadline"] = now + Duration(seconds=self._close_duration_sec)

        started = self.node.get_clock().now()
        while True:
            obstructed_now = any(self._doors[t]["obstructed"] for t in targets)
            remaining = [t for t in targets if self._doors[t]["state"] != CLOSED]
            closed = [t for t in targets if self._doors[t]["state"] == CLOSED]

            if goal_handle.is_cancel_requested:
                # 취소 = "닫지 말라". 중간에서 멈춘 문을 완전 열림으로 단정하지 않고
                # 열림 방향으로 복귀시킨다 (사용자가 다시 열려는 경우가 취소의 동기다).
                self._reopen(remaining)
                goal_handle.canceled()
                self.log.info(f"[{self.name}] 닫기 취소됨. 열림 복귀: {remaining}")
                return self._close_result(
                    success=False,
                    closed=closed,
                    failed=remaining,
                    error=CloseTrayDoor.Result.ERROR_ABORTED,
                    message="canceled by client",
                )

            if obstructed_now and not goal.force:
                # 안전 기본값: 끼임 감지 시 멈추고 열림 방향으로 되돌린다.
                self._reopen(remaining)
                goal_handle.abort()
                self.log.warn(f"[{self.name}] 끼임 감지로 중단. 열림 복귀: {remaining}")
                return self._close_result(
                    success=False,
                    closed=closed,
                    failed=remaining,
                    error=CloseTrayDoor.Result.ERROR_OBSTRUCTED,
                    message="obstruction detected (force=false)",
                )

            feedback = CloseTrayDoor.Feedback()
            feedback.remaining = remaining
            feedback.obstructed = obstructed_now
            goal_handle.publish_feedback(feedback)

            if not remaining:
                goal_handle.succeed()
                self.log.info(f"[{self.name}] 닫기 완료: tray {closed}")
                return self._close_result(
                    success=True,
                    closed=closed,
                    failed=[],
                    error=CloseTrayDoor.Result.ERROR_NONE,
                    message=f"closed {closed}",
                )

            elapsed = self.node.get_clock().now() - started
            if elapsed > Duration(seconds=self._close_timeout_sec):
                goal_handle.abort()
                self.log.warn(f"[{self.name}] 닫기 타임아웃. 미완료: {remaining}")
                return self._close_result(
                    success=False,
                    closed=closed,
                    failed=remaining,
                    error=CloseTrayDoor.Result.ERROR_TIMEOUT,
                    message=f"timeout after {self._close_timeout_sec}s",
                )

            time.sleep(self.poll_sec)

    def _reopen(self, trays: list[int]) -> None:
        """닫기를 중단하고 열림 방향으로 복귀시킨다."""
        now = self.node.get_clock().now()
        for tray in trays:
            door = self._doors[tray]
            door["state"] = OPENING
            door["deadline"] = now + Duration(seconds=self._open_duration_sec)
        self._publish()

    @staticmethod
    def _close_result(success, closed, failed, error, message):
        result = CloseTrayDoor.Result()
        result.success = success
        result.closed = closed
        result.failed = failed
        result.error = error
        result.message = message
        return result

    # ------------------------------------------------------------------ #
    # 헬퍼
    # ------------------------------------------------------------------ #
    def _resolve_trays(self, trays) -> tuple[list[int], list[int]]:
        """요청 tray 목록을 해석한다. 빈 배열 = 전체. 두 번째 값은 범위 밖 목록."""
        if len(trays) == 0:
            return list(range(self._tray_count)), []
        requested = [int(t) for t in trays]
        invalid = [t for t in requested if t not in self._doors]
        seen: set[int] = set()
        valid = [t for t in requested if t in self._doors and not (t in seen or seen.add(t))]
        return valid, invalid

    def _publish(self) -> None:
        msg = TrayDoorState()
        # latch 라도 staleness 판정이 필요하므로 발행 시점 시계로 매번 갱신한다.
        msg.header.stamp = self.node.get_clock().now().to_msg()
        for tray in range(self._tray_count):
            door = self._doors[tray]
            entry = TrayDoor()
            entry.tray = tray
            entry.open = door["state"] == OPEN
            entry.closed = door["state"] == CLOSED
            entry.obstructed = door["obstructed"]
            msg.doors.append(entry)
        self._pub.publish(msg)
