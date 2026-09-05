"""실물 적재함 문 컨트롤러(w_swing_door_controller, top/bottom 2대) 모사 인터페이스.

스크린 BT 가 쓰는 실물 계약을 **이름·타입 그대로** 낸다 — `/dummy/` 네임스페이스 규칙의
예외다(README 참고). 실물은 문마다 컨트롤러가 따로 뜨므로 더미도 문마다 액션 서버와
status 퍼블리셔를 따로 둔다. 이름의 정본은 BT 의 ScreenDoorDefaults.xml 이고, 이 파일은
`side_door_controllers` 파라미터(인덱스 = tray, 0=상단=top, 1=하단=bottom)로 맞춘다.

기존 `tray_door.py`(dummy_node_interfaces 구계약)는 그대로 두며 서로 간섭하지 않는다.

실물 계약 (문마다):
- Action (DoorCommand) `/<controller>/command`   open / close / unlock / calibration
- Publisher (String)   `/<controller>/status`    값은 `closed` | `open` 둘뿐(위치로만 판정)

더미 조작 (SetTrayBool, trays 0=top 1=bottom, 빈 배열 = 전체, 범위 밖 = 거부):
- `/dummy/side_door/set_obstructed`  끼임 주입. 이동 중이면 그 tick 에 obstructed 로 멈춘다
- `/dummy/side_door/manual_move`     손으로 문을 움직인다. value=true 닫기, false 열기.
                                     물리 제약을 검사하지 않는 **상태 강제** 수단이다

모사하는 동작 (실물 SwingDoorLogic · 액션 서버 기준):
- 상태: closed → unlocking → unlocked (unlock) / opening → open (open) / closing → closed.
  status 토픽은 `closed` 일 때만 "closed", 그 외 전부 "open" 이다 — 실물이 위치가 닫힘
  구간 밖이면 이동 중·끼임·에러를 가리지 않고 "open" 을 내는 것과 같다.
- 트리가 쓰는 명령은 unlock(=열기)과 close 뿐이다. unlock 은 unlocked 또는 open 이면 성공.
- 이미 목표 상태면 이동하지 않고 즉시 성공(멱등).
- 이동 중 새 goal → 기존 goal 은 abort(success=false, error_code=0, "새 명령으로 선점됨").
- 취소 → canceled(success=false, error_code=0). 정지한 자리가 래치 안이면 closed, 밖이면
  unlocked(살짝 열림)로 본다.
- 끼임(`set_obstructed`): 이동 중이면 obstructed 로 정지. `side_door_obstruction_timeout_sec`
  뒤 재시도하고, 재시도가 `side_door_obstruction_max_retries` 를 넘으면
  ERROR_OBSTRUCTION_RETRIES_EXHAUSTED(2). timeout 이 0 이면 재시도 없이 머문다.
  끼임은 실패가 아니라 진행 중이므로 feedback.obstruction_retries 로만 알린다.
- goal 이 `side_door_goal_timeout_sec` 를 넘기면 ERROR_GOAL_TIMEOUT(21).
- calibration 은 즉시 성공한다(위치 모사가 없다). feedback.position/target 도 0 고정이다.
"""

import functools
import threading
import time

from rclpy.action import ActionServer, CancelResponse, GoalResponse
from std_msgs.msg import String

from dummy_node_interfaces.srv import SetTrayBool
from w_ros2_controller_interfaces.action import DoorCommand

from dummy_node.registry import InterfaceBase, register

Goal = DoorCommand.Goal
Result = DoorCommand.Result

# 상태 문자열은 실물 SwingDoorState 의 to_string 과 같다.
CLOSED = "closed"
UNLOCKING = "unlocking"
UNLOCKED = "unlocked"
OPENING = "opening"
OPEN = "open"
CLOSING = "closing"
OBSTRUCTED = "obstructed"
ERROR = "error"

MOVING = {UNLOCKING, OPENING, CLOSING}
ARRIVAL = {UNLOCKING: UNLOCKED, OPENING: OPEN, CLOSING: CLOSED}
COMMANDS = (Goal.OPEN, Goal.CLOSE, Goal.UNLOCK, Goal.CALIBRATION)
ERROR_TEXT = {
    Result.ERROR_OBSTRUCTION_RETRIES_EXHAUSTED: "끼임 재시도 예산 소진",
}


@register
class SideDoors(InterfaceBase):
    name = "side_door"

    # 더미 조작 — 상대 이름 -> /dummy/side_door/...
    srv_obstruct = "side_door/set_obstructed"
    srv_manual = "side_door/manual_move"

    poll_sec = 0.05  # 액션 진행 폴링 주기.

    def setup(self) -> None:
        names = list(self.param(
            "side_door_controllers",
            ["side_door_top_controller", "side_door_bottom_controller"],
        ))
        self._status_period_sec = float(self.param("side_door_status_period_sec", 0.1))
        self._goal_timeout_sec = float(self.param("side_door_goal_timeout_sec", 30.0))
        self._obstruction_timeout_sec = float(self.param("side_door_obstruction_timeout_sec", 1.0))
        self._max_retries = int(self.param("side_door_obstruction_max_retries", 0))
        # 아래 셋은 구계약 tray_door 와 공유하는 파라미터다 -> param() 헬퍼 필수.
        self._open_duration_sec = float(self.param("door_open_duration_sec", 2.0))  # unlock · open
        self._close_duration_sec = float(self.param("door_close_duration_sec", 2.0))
        initial_open = bool(self.param("initial_doors_open", False))

        # 상태는 timer(이동 진행) · 서비스 · 액션 실행 루프가 서로 다른 스레드에서 만지므로 잠근다.
        self._lock = threading.Lock()
        self._doors = []
        for tray, ctrl in enumerate(names):
            door = {
                "tray": tray,
                "name": str(ctrl),
                "state": OPEN if initial_open else CLOSED,
                "deadline": None,        # 이동 완료 시각 (time.monotonic)
                "obstructed": False,     # 끼임 주입 플래그
                "obstructed_since": None,
                "resume_to": None,       # 끼임에서 재시도할 이동 상태
                "retries": 0,
                "error": Result.ERROR_NONE,
                "calibrated": False,
                "active": None,          # 활성 goal handle
            }
            door["pub"] = self.node.create_publisher(
                String, f"/{door['name']}/status", 10, callback_group=self.callback_group
            )
            door["server"] = ActionServer(
                self.node,
                DoorCommand,
                f"/{door['name']}/command",
                execute_callback=functools.partial(self._execute, door),
                goal_callback=self._on_goal,
                handle_accepted_callback=functools.partial(self._on_accepted, door),
                cancel_callback=self._on_cancel,
                callback_group=self.callback_group,
            )
            self._doors.append(door)

        cg = self.callback_group
        self.node.create_service(
            SetTrayBool, self.srv_obstruct, self._on_set_obstructed, callback_group=cg
        )
        self.node.create_service(
            SetTrayBool, self.srv_manual, self._on_manual_move, callback_group=cg
        )
        self._timer = self.node.create_timer(
            self._status_period_sec, self._on_tick, callback_group=cg
        )

        self.log.info(
            f"[{self.name}] 문 {len(self._doors)}개 {names}, "
            f"초기 상태={'열림' if initial_open else '닫힘'}, "
            f"unlock/open {self._open_duration_sec}s / close {self._close_duration_sec}s, "
            f"goal 상한 {self._goal_timeout_sec}s, "
            f"끼임 재시도 {self._obstruction_timeout_sec}s × {self._max_retries}회"
        )
        self._publish_status()

    # ------------------------------------------------------------------ #
    # 주기 콜백: 이동 진행 + 끼임 재시도 + 상태 발행
    # ------------------------------------------------------------------ #
    def _on_tick(self) -> None:
        now = time.monotonic()
        with self._lock:
            for door in self._doors:
                self._advance(door, now)
        self._publish_status()

    def _advance(self, door, now: float) -> None:
        state = door["state"]
        if state in MOVING:
            if door["obstructed"]:
                # 구간 밖 과전류 = 끼임. 정지 + 힘 빼기. 재시도는 obstruction_timeout 뒤.
                door["resume_to"] = state
                door["state"] = OBSTRUCTED
                door["obstructed_since"] = now
                door["deadline"] = None
                self.log.warn(f"[{self.name}] {door['name']}: 끼임 감지 — 정지 ({state} 중)")
            elif now >= door["deadline"]:
                door["state"] = ARRIVAL[state]
                door["deadline"] = None
                self.log.info(f"[{self.name}] {door['name']}: {state} -> {door['state']}")
            return

        if state == OBSTRUCTED and door["resume_to"] is not None:
            if self._obstruction_timeout_sec <= 0.0:
                return  # 재시도 없음. goal_timeout 이 끊을 때까지 머문다.
            if now - door["obstructed_since"] < self._obstruction_timeout_sec:
                return
            if door["retries"] >= self._max_retries:
                door["state"] = ERROR
                door["error"] = Result.ERROR_OBSTRUCTION_RETRIES_EXHAUSTED
                door["resume_to"] = None
                self.log.warn(f"[{self.name}] {door['name']}: 끼임 재시도 예산 소진 -> error")
                return
            door["retries"] += 1
            self.log.info(
                f"[{self.name}] {door['name']}: 끼임 재시도 {door['retries']}/{self._max_retries} "
                f"({door['resume_to']})"
            )
            self._begin_motion(door, door["resume_to"], now)
            door["resume_to"] = None

    # ------------------------------------------------------------------ #
    # 액션 (DoorCommand)
    # ------------------------------------------------------------------ #
    def _on_goal(self, goal_request) -> GoalResponse:
        if goal_request.command not in COMMANDS:
            self.log.warn(f"[{self.name}] 알 수 없는 명령 거부: '{goal_request.command}'")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _on_cancel(self, goal_handle) -> CancelResponse:
        # 취소는 항상 받는다. 실제 정지는 실행 루프가 처리한다.
        return CancelResponse.ACCEPT

    def _on_accepted(self, door, goal_handle) -> None:
        command = goal_handle.request.command
        with self._lock:
            if door["active"] is not None:
                # 이동 중 새 goal — 기존 goal 을 선점한다. 기존 실행 루프가 abort 로 마감한다.
                self.log.info(f"[{self.name}] {door['name']}: 새 goal '{command}' 로 기존 goal 선점")
            door["active"] = goal_handle
            self._start(door, command, time.monotonic())
            self._log_goal(door, command, f"시작 (상태 {door['state']})")
        self._publish_status()
        goal_handle.execute()

    def _start(self, door, command: str, now: float) -> None:
        """새 명령 적용. 에러·재시도 예산은 새 명령에서 다시 찬다(실물과 같다)."""
        door["error"] = Result.ERROR_NONE
        door["retries"] = 0
        door["resume_to"] = None
        if command == Goal.CALIBRATION:
            door["calibrated"] = True
            return
        if self._satisfied(door, command):
            self.log.info(f"[{self.name}] {door['name']}: 이미 목표 상태 — 이동하지 않음 ({door['state']})")
            return
        motion = {Goal.CLOSE: CLOSING, Goal.UNLOCK: UNLOCKING, Goal.OPEN: OPENING}[command]
        self._begin_motion(door, motion, now)

    def _begin_motion(self, door, motion: str, now: float) -> None:
        duration = self._close_duration_sec if motion == CLOSING else self._open_duration_sec
        door["state"] = motion
        door["deadline"] = now + duration

    @staticmethod
    def _satisfied(door, command: str) -> bool:
        state = door["state"]
        if command == Goal.CLOSE:
            return state == CLOSED
        if command == Goal.OPEN:
            return state == OPEN
        if command == Goal.UNLOCK:
            # unlock 의 목표는 "래치를 통과했다" 다. 완전 개방은 그보다 더 나아간 상태라 함께 만족한다.
            return state in (UNLOCKED, OPEN)
        return door["calibrated"]  # calibration

    def _execute(self, door, goal_handle):
        command = goal_handle.request.command
        started = time.monotonic()
        while True:
            with self._lock:
                now = time.monotonic()
                if door["active"] is not goal_handle:
                    goal_handle.abort()
                    return self._result(False, Result.ERROR_NONE, "새 명령으로 선점됨")  # 실패가 아니다

                if goal_handle.is_cancel_requested:
                    self._stop_here(door)
                    door["active"] = None
                    goal_handle.canceled()
                    self._log_goal(door, command, f"취소 -> {door['state']}")
                    return self._result(False, Result.ERROR_NONE, f"취소됨 — 상태 {door['state']}")

                if door["state"] == ERROR:
                    door["active"] = None
                    goal_handle.abort()
                    text = ERROR_TEXT.get(door["error"], f"error_code {door['error']}")
                    return self._result(False, door["error"], f"실패 — {text}")

                if now - started >= self._goal_timeout_sec:
                    # 실물처럼 goal 만 abort 한다. 문은 그 자리에 머문다(끼임이면 힘 빠진 채).
                    door["active"] = None
                    goal_handle.abort()
                    self._log_goal(door, command, f"시간 상한 초과 ({door['state']})", warn=True)
                    return self._result(
                        False, Result.ERROR_GOAL_TIMEOUT, f"goal 시간 상한 초과 — 상태 {door['state']}"
                    )

                if self._satisfied(door, command):
                    door["active"] = None
                    goal_handle.succeed()
                    self._log_goal(door, command, f"완료 ({door['state']})")
                    return self._result(True, Result.ERROR_NONE, f"완료 — 상태 {door['state']}")

                feedback = DoorCommand.Feedback()
                feedback.state = door["state"]
                feedback.position = 0.0  # 위치는 모사하지 않는다.
                feedback.target = 0.0
                feedback.elapsed = now - started
                feedback.obstruction_retries = door["retries"]

            goal_handle.publish_feedback(feedback)
            time.sleep(self.poll_sec)

    def _stop_here(self, door) -> None:
        """정지 + 힘 빼기. 래치를 통과하기 전이면 closed, 통과한 뒤면 unlocked(살짝 열림)로 본다."""
        motion = door["resume_to"] if door["state"] == OBSTRUCTED else door["state"]
        if motion in MOVING:
            door["state"] = CLOSED if motion == UNLOCKING else UNLOCKED
        door["deadline"] = None
        door["resume_to"] = None

    def _log_goal(self, door, command: str, what: str, warn: bool = False) -> None:
        line = f"[{self.name}] {door['name']}: goal '{command}' {what}"
        (self.log.warn if warn else self.log.info)(line)

    @staticmethod
    def _result(success: bool, error_code: int, message: str):
        result = DoorCommand.Result()
        result.success = success
        result.error_code = int(error_code)
        result.message = message
        return result

    # ------------------------------------------------------------------ #
    # 더미 조작 서비스 (SetTrayBool)
    # ------------------------------------------------------------------ #
    def _on_set_obstructed(self, request: SetTrayBool.Request, response: SetTrayBool.Response):
        targets, invalid = self._resolve_trays(request.trays)
        if invalid:
            return self._reject(response, invalid)
        with self._lock:
            for tray in targets:
                self._doors[tray]["obstructed"] = bool(request.value)
        self.log.info(f"[{self.name}] 문 {targets} obstructed -> {request.value}")
        response.success = True
        response.message = f"obstructed set to {request.value} for {targets}"
        response.applied = targets
        return response

    def _on_manual_move(self, request: SetTrayBool.Request, response: SetTrayBool.Response):
        targets, invalid = self._resolve_trays(request.trays)
        if invalid:
            return self._reject(response, invalid)
        target_state = CLOSED if request.value else OPEN
        with self._lock:
            for tray in targets:
                door = self._doors[tray]
                door["state"] = target_state
                door["deadline"] = None
                door["resume_to"] = None
        how = "닫음" if request.value else "열음"
        self.log.info(f"[{self.name}] 문 {targets} 손으로 {how} -> {target_state}")
        self._publish_status()
        response.success = True
        response.message = f"moved by hand to {target_state}: {targets}"
        response.applied = targets
        return response

    def _reject(self, response: SetTrayBool.Response, invalid: list[int]):
        response.success = False
        response.message = f"존재하지 않는 문: {invalid} (유효 범위 0..{len(self._doors) - 1})"
        response.applied = []
        self.log.warn(f"[{self.name}] 거부: {response.message}")
        return response

    # ------------------------------------------------------------------ #
    # 헬퍼
    # ------------------------------------------------------------------ #
    def _resolve_trays(self, trays) -> tuple[list[int], list[int]]:
        """요청 tray 목록을 해석한다. 빈 배열 = 전체. 두 번째 값은 범위 밖 목록."""
        count = len(self._doors)
        if len(trays) == 0:
            return list(range(count)), []
        requested = [int(t) for t in trays]
        invalid = [t for t in requested if not 0 <= t < count]
        seen: set[int] = set()
        valid = [t for t in requested if 0 <= t < count and not (t in seen or seen.add(t))]
        return valid, invalid

    def _publish_status(self) -> None:
        # 실물 status 는 어디 있는지만 알린다 — 닫힘 구간 안이면 closed, 아니면 open.
        for door in self._doors:
            door["pub"].publish(String(data=CLOSED if door["state"] == CLOSED else OPEN))
