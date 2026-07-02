"""로봇 층(floor) 인터페이스.

- Publisher (Int32) `robot/current_floor` -> /dummy/robot/current_floor
    현재 층을 1Hz로 계속 발행.
- Service (SetInt) `robot/set_current_floor` -> /dummy/robot/set_current_floor
    즉시 해당 층으로 변경 후 발행. (진행 중이던 이동은 중단)
- Service (SetInt) `robot/set_target_floor` -> /dummy/robot/set_target_floor
    목표 층 설정. 1초에 한 칸씩 목표를 향해 이동하며 계속 발행.

층 규칙:
- 지하 1층 = -1, 0층은 존재하지 않는다. (이동 시 0을 건너뛴다: 1 <-> -1)
- 초기 층은 ROS 파라미터 `initial_floor`(config)로 관리하며 기본값은 1.
"""

from example_interfaces.msg import Int32

from dummy_node_interfaces.srv import SetInt

from dummy_node.registry import InterfaceBase, register


@register
class RobotFloor(InterfaceBase):
    name = "robot_floor"

    topic = "robot/current_floor"               # -> /dummy/robot/current_floor
    srv_set_current = "robot/set_current_floor"  # -> /dummy/robot/set_current_floor
    srv_set_target = "robot/set_target_floor"    # -> /dummy/robot/set_target_floor
    tick_period_sec = 1.0                        # 이동/발행 주기 (1초에 한 칸)

    def setup(self) -> None:
        # config: 초기 층 (0은 유효하지 않으므로 검증).
        initial = int(
            self.node.declare_parameter("initial_floor", 1).value
        )
        if initial == 0:
            self.log.warn(
                f"[{self.name}] initial_floor=0 은 존재하지 않는 층 -> 1로 대체"
            )
            initial = 1

        self._current = initial   # 현재 층
        self._target = initial    # 목표 층 (같으면 이동 없음)

        self._pub = self.node.create_publisher(
            Int32, self.topic, 10, callback_group=self.callback_group
        )
        self._srv_current = self.node.create_service(
            SetInt,
            self.srv_set_current,
            self._on_set_current,
            callback_group=self.callback_group,
        )
        self._srv_target = self.node.create_service(
            SetInt,
            self.srv_set_target,
            self._on_set_target,
            callback_group=self.callback_group,
        )
        # 1Hz: 목표를 향해 한 칸 이동 후 현재 층을 발행.
        self._timer = self.node.create_timer(
            self.tick_period_sec, self._on_tick, callback_group=self.callback_group
        )

        self.log.info(f"[{self.name}] 초기 층 = {self._current}")
        self._publish()

    # ------------------------------------------------------------------ #
    # 서비스 콜백
    # ------------------------------------------------------------------ #
    def _on_set_current(self, request: SetInt.Request, response: SetInt.Response):
        if request.value == 0:
            return self._reject(response, "0층은 존재하지 않습니다.")

        self._current = int(request.value)
        self._target = self._current  # 이동 중단.
        self.log.info(f"[{self.name}] 현재 층 즉시 변경 -> {self._current}")
        self._publish()  # 즉시 발행.
        return self._ok(response, f"current floor set to {self._current}")

    def _on_set_target(self, request: SetInt.Request, response: SetInt.Response):
        if request.value == 0:
            return self._reject(response, "0층은 존재하지 않습니다.")

        self._target = int(request.value)
        self.log.info(
            f"[{self.name}] 목표 층 설정 -> {self._target} (현재 {self._current})"
        )
        return self._ok(
            response, f"target floor set to {self._target}, moving from {self._current}"
        )

    # ------------------------------------------------------------------ #
    # 주기 콜백: 이동 + 발행
    # ------------------------------------------------------------------ #
    def _on_tick(self) -> None:
        if self._current != self._target:
            self._current = self._step_toward(self._current, self._target)
        self._publish()

    @staticmethod
    def _step_toward(current: int, target: int) -> int:
        """current에서 target 방향으로 한 칸 이동. 0층은 건너뛴다."""
        step = 1 if target > current else -1
        nxt = current + step
        if nxt == 0:  # 0층은 존재하지 않으므로 한 칸 더.
            nxt += step
        return nxt

    # ------------------------------------------------------------------ #
    # 헬퍼
    # ------------------------------------------------------------------ #
    def _publish(self) -> None:
        self._pub.publish(Int32(data=self._current))

    def _ok(self, response: SetInt.Response, message: str) -> SetInt.Response:
        response.success = True
        response.message = message
        response.current_floor = self._current
        return response

    def _reject(self, response: SetInt.Response, message: str) -> SetInt.Response:
        self.log.warn(f"[{self.name}] 거부: {message}")
        response.success = False
        response.message = message
        response.current_floor = self._current
        return response
