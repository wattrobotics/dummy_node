"""로드셀(load cell) 스텁 인터페이스.

`TakeParcelScreen.md` §3.2 규격의 더미다. 실제 로드셀 노드가 준비되기 전까지
BT 의 로드셀 판정 노드를 시험하는 데 쓴다.

- Publisher (LoadCellState) `load_cell_state` -> /dummy/load_cell_state
    전 tray 의 무게·감지·정상여부를 주기 발행. QoS 는 스펙대로 reliable · depth 1 · volatile.
- Service (SetTrayBool) `load_cell/set_occupied` -> /dummy/load_cell/set_occupied
    대상 tray 의 `occupied` 설정. `trays` 빈 배열 = 전체.
- Service (SetTrayBool) `load_cell/set_healthy` -> /dummy/load_cell/set_healthy
    대상 tray 의 `healthy` 설정. `healthy=false` 로 "로드셀 동작하지 않음" 경로를 시험한다.

`weight_g` 는 스펙상 **진단용**이며 BT 판정에는 쓰이지 않는다. 따라서 더미는 별도
설정 수단을 두지 않고 `occupied` 로부터 파생시킨다 — 값이 두 곳에서 관리되어
서로 어긋나는 상태를 애초에 만들지 않기 위함이다.
"""

from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

from dummy_node_interfaces.msg import LoadCellState, LoadCellTray
from dummy_node_interfaces.srv import SetTrayBool

from dummy_node.registry import InterfaceBase, register


@register
class LoadCell(InterfaceBase):
    name = "load_cell"

    topic = "load_cell_state"                  # -> /dummy/load_cell_state
    srv_set_occupied = "load_cell/set_occupied"  # -> /dummy/load_cell/set_occupied
    srv_set_healthy = "load_cell/set_healthy"    # -> /dummy/load_cell/set_healthy

    def setup(self) -> None:
        # tray_count 는 tray_door 와 공유하는 파라미터다 -> param() 헬퍼 필수.
        self._tray_count = int(self.param("tray_count", 2))
        self._period_sec = float(self.param("load_cell_period_sec", 0.1))
        initial_occupied = bool(self.param("initial_load_cell_occupied", False))
        initial_healthy = bool(self.param("initial_load_cell_healthy", True))
        # occupied 일 때 보고할 무게(g). 진단용 파생값이다.
        self._occupied_weight_g = float(self.param("occupied_weight_g", 1000.0))

        # tray -> {"occupied": bool, "healthy": bool}
        self._trays = {
            tray: {"occupied": initial_occupied, "healthy": initial_healthy}
            for tray in range(self._tray_count)
        }

        # 스펙 §3.2: reliable, depth 1, volatile (실시간 값이라 latch 금지).
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self._pub = self.node.create_publisher(
            LoadCellState, self.topic, qos, callback_group=self.callback_group
        )
        self._srv_occupied = self.node.create_service(
            SetTrayBool,
            self.srv_set_occupied,
            self._on_set_occupied,
            callback_group=self.callback_group,
        )
        self._srv_healthy = self.node.create_service(
            SetTrayBool,
            self.srv_set_healthy,
            self._on_set_healthy,
            callback_group=self.callback_group,
        )
        self._timer = self.node.create_timer(
            self._period_sec, self._publish, callback_group=self.callback_group
        )

        self.log.info(
            f"[{self.name}] tray {self._tray_count}단, "
            f"occupied={initial_occupied}, healthy={initial_healthy}, "
            f"발행 주기={self._period_sec}s"
        )
        self._publish()

    # ------------------------------------------------------------------ #
    # 서비스 콜백
    # ------------------------------------------------------------------ #
    def _on_set_occupied(self, request, response):
        return self._apply(request, response, "occupied")

    def _on_set_healthy(self, request, response):
        return self._apply(request, response, "healthy")

    def _apply(
        self, request: SetTrayBool.Request, response: SetTrayBool.Response, field: str
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
            self._trays[tray][field] = bool(request.value)
        self.log.info(f"[{self.name}] tray {targets} {field} -> {request.value}")

        # 상태 변경 즉시 발행 (다음 주기까지 기다리지 않는다).
        self._publish()

        response.success = True
        response.message = f"{field} set to {request.value} for {targets}"
        response.applied = targets
        return response

    # ------------------------------------------------------------------ #
    # 헬퍼
    # ------------------------------------------------------------------ #
    def _resolve_trays(self, trays) -> tuple[list[int], list[int]]:
        """요청 tray 목록을 해석한다. 빈 배열 = 전체. 두 번째 값은 범위 밖 목록."""
        if len(trays) == 0:
            return list(range(self._tray_count)), []
        requested = [int(t) for t in trays]
        invalid = [t for t in requested if t not in self._trays]
        # 중복 제거하되 요청 순서를 유지한다.
        seen: set[int] = set()
        valid = [t for t in requested if t in self._trays and not (t in seen or seen.add(t))]
        return valid, invalid

    def _publish(self) -> None:
        msg = LoadCellState()
        # staleness 판정 기준이므로 발행 시점 시계로 매번 갱신한다.
        msg.header.stamp = self.node.get_clock().now().to_msg()
        for tray in range(self._tray_count):
            state = self._trays[tray]
            entry = LoadCellTray()
            entry.tray = tray
            entry.occupied = state["occupied"]
            entry.healthy = state["healthy"]
            entry.weight_g = self._occupied_weight_g if state["occupied"] else 0.0
            msg.trays.append(entry)
        self._pub.publish(msg)
