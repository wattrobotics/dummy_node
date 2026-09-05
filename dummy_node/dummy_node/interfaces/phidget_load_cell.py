"""실물 로드셀 노드(phidgets_hw `phidget_load_cell`) 모사 인터페이스.

스크린 BT(TakeParcelScreen · HandOverParcelScreen · CheckScreenLoad)가 쓰는 실물 계약을
**이름·타입 그대로** 낸다. BT 수정 없이 실물을 대체하는 것이 목적이라 이름이 같아야 하고,
그래서 `/dummy/` 네임스페이스 규칙의 예외다(README "네임스페이스 규칙" 참고). 더미 조작용
서비스는 실물에 없는 것이므로 규칙대로 `/dummy/` 아래에 둔다.

기존 `load_cell.py`(dummy_node_interfaces 구계약)는 그대로 두며 서로 간섭하지 않는다.

실물 계약 (phidgets_hw/config/phidget_load_cell_node.yaml):
- Publisher (LoadCellState) `/load_cell_state`                  reliable · depth 1 · volatile
- Service (SetTracking)     `/phidget_load_cell/set_tracking`   변화 인지 켜기/끄기(동결)
- Service (ConfirmLoad)     `/phidget_load_cell/confirm_load`   적재 확정 + 동결
- Service (ConfirmUnload)   `/phidget_load_cell/confirm_unload` 반출 확인 + 동결
- Service (Tare)            `/phidget_load_cell/tare`           영점 재설정

더미 조작 (SetTrayBool, 빈 trays = 전체, 범위 밖 = 거부):
- `/dummy/phidget_load_cell/set_occupied`  판 위에 물건 놓기/빼기
- `/dummy/phidget_load_cell/set_healthy`   로드셀 고장 주입

모사하는 동작 — BT 판정에 영향을 주는 것만:
- **tracking 게이트.** 기동 시 꺼짐(`tracking_on_startup: false`, 실물 기본값). 꺼진 동안
  발행되는 `weight_g`·`occupied` 는 마지막 인지 값으로 **동결**되고 `healthy` 만 실시간이다.
  그래서 tracking 을 켜지 않으면 물건을 놓아도(set_occupied) 발행값은 바뀌지 않는다 —
  실물이 그렇고, BT 가 문을 열 때 SetLoadCellTracking 을 부르는 이유가 이것이다.
  기동 직후의 동결값은 initial 파라미터와 무관하게 0 g / 비점유다(실물과 같다).
- `confirm_load`: 비점유 tray 는 실패(skipped), 점유면 W_in 확정 + 동결.
- `confirm_unload`: 아직 점유면 실패(still occupied), 비점유면 동결.
- `tare`: 지금 하중을 영점으로 잡는다. 물건이 올라간 채 부르면 그 무게가 영점이 되어
  **비점유가 된다** — 실물의 "숨은 tare 는 물건을 지운다" 함정을 그대로 둔다.
- 실물 서비스의 trays 규약: 빈 배열 = 전체. 범위 밖 인덱스는 무시하고 **매칭이 하나도 없을
  때만** 실패한다. 구계약 SetTrayBool 의 "범위 밖 = 거부" 와 다르므로 섞지 않는다.
"""

from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

from dummy_node_interfaces.srv import SetTrayBool
from phidgets_hw.msg import LoadCellState, LoadCellTray
from phidgets_hw.srv import ConfirmLoad, ConfirmUnload, SetTracking, Tare

from dummy_node.registry import InterfaceBase, register


@register
class PhidgetLoadCell(InterfaceBase):
    name = "phidget_load_cell"

    # 실물 계약 — 절대 이름 (네임스페이스 규칙 예외).
    topic = "/load_cell_state"
    srv_set_tracking = "/phidget_load_cell/set_tracking"
    srv_confirm_load = "/phidget_load_cell/confirm_load"
    srv_confirm_unload = "/phidget_load_cell/confirm_unload"
    srv_tare = "/phidget_load_cell/tare"
    # 더미 조작 — 상대 이름 -> /dummy/phidget_load_cell/...
    srv_set_occupied = "phidget_load_cell/set_occupied"
    srv_set_healthy = "phidget_load_cell/set_healthy"

    def setup(self) -> None:
        # 아래 다섯 개는 구계약 load_cell 과 공유하는 파라미터다 -> param() 헬퍼 필수.
        self._tray_count = int(self.param("tray_count", 2))
        self._period_sec = float(self.param("load_cell_period_sec", 0.1))
        self._occupied_weight_g = float(self.param("occupied_weight_g", 1000.0))
        initial_occupied = bool(self.param("initial_load_cell_occupied", False))
        initial_healthy = bool(self.param("initial_load_cell_healthy", True))
        tracking_on_startup = bool(self.param("tracking_on_startup", False))

        # tray -> 상태.
        #   occupied  판 위의 실제 상황(더미 조작값). 실물의 raw 하중에 해당한다.
        #   frozen_*  tracking 이 꺼진 동안 발행되는 참값. 실물 초기값은 0 g / 비점유.
        self._trays = {
            tray: {
                "occupied": initial_occupied,
                "healthy": initial_healthy,
                "tracking": tracking_on_startup,
                "frozen_weight": 0.0,
                "frozen_occupied": False,
            }
            for tray in range(self._tray_count)
        }

        # 실물과 같은 QoS: reliable, depth 1, volatile (실시간 값이라 latch 금지).
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self._pub = self.node.create_publisher(
            LoadCellState, self.topic, qos, callback_group=self.callback_group
        )
        cg = self.callback_group
        for srv_type, srv_name, callback in (
            (SetTracking, self.srv_set_tracking, self._on_set_tracking),
            (ConfirmLoad, self.srv_confirm_load, self._on_confirm_load),
            (ConfirmUnload, self.srv_confirm_unload, self._on_confirm_unload),
            (Tare, self.srv_tare, self._on_tare),
            (SetTrayBool, self.srv_set_occupied, self._on_set_occupied),
            (SetTrayBool, self.srv_set_healthy, self._on_set_healthy),
        ):
            self.node.create_service(srv_type, srv_name, callback, callback_group=cg)
        self._timer = self.node.create_timer(self._period_sec, self._publish, callback_group=cg)

        self.log.info(
            f"[{self.name}] tray {self._tray_count}단, occupied={initial_occupied}, "
            f"healthy={initial_healthy}, tracking={tracking_on_startup}, "
            f"발행 주기={self._period_sec}s (실물 이름 {self.topic})"
        )
        self._publish()

    # ------------------------------------------------------------------ #
    # 실물 계약 서비스
    # ------------------------------------------------------------------ #
    def _on_set_tracking(self, request: SetTracking.Request, response: SetTracking.Response):
        report, ok = [], True
        for tray in self._real_targets(request.trays):
            state = self._trays[tray]
            if state["tracking"] == bool(request.enable):
                report.append(f"tray_{tray}: unchanged")
                continue
            if request.enable:
                state["tracking"] = True
                report.append(f"tray_{tray}: tracking resumed")
            else:
                self._freeze(state)
                report.append(f"tray_{tray}: value frozen")
        return self._finish(response, "set_tracking", report, ok)

    def _on_confirm_load(self, request: ConfirmLoad.Request, response: ConfirmLoad.Response):
        report, ok = [], True
        for tray in self._real_targets(request.trays):
            state = self._trays[tray]
            if not self._sensed_occupied(state):
                ok = False
                report.append(f"tray_{tray}: not occupied — skipped")
                continue
            # 적재 확정과 동시에 보관 구간으로 넘어간다 — 참값을 동결한다.
            self._freeze(state)
            report.append(f"tray_{tray}: W_in {self._occupied_weight_g:.1f} g, frozen")
        return self._finish(response, "confirm_load", report, ok)

    def _on_confirm_unload(self, request: ConfirmUnload.Request, response: ConfirmUnload.Response):
        report, ok = [], True
        for tray in self._real_targets(request.trays):
            state = self._trays[tray]
            if self._sensed_occupied(state):
                # 상태를 바꾸지 않는다 — "뺐다는데 아직 있음" 신호.
                ok = False
                report.append(f"tray_{tray}: still occupied")
                continue
            self._freeze(state)
            report.append(f"tray_{tray}: unload confirmed, frozen")
        return self._finish(response, "confirm_unload", report, ok)

    def _on_tare(self, request: Tare.Request, response: Tare.Response):
        report, ok = [], True
        for tray in self._real_targets(request.trays):
            state = self._trays[tray]
            if not state["healthy"]:
                ok = False
                report.append(f"tray_{tray}: not attached — skipped")
                continue
            # 지금 하중이 영점이 된다. 물건이 있었다면 그 무게가 영점에 흡수되어 사라진다.
            removed = self._occupied_weight_g if state["occupied"] else 0.0
            state["occupied"] = False
            report.append(f"tray_{tray}: tared away {removed:.1f} g")
        return self._finish(response, "tare", report, ok)

    # ------------------------------------------------------------------ #
    # 더미 조작 서비스 (SetTrayBool)
    # ------------------------------------------------------------------ #
    def _on_set_occupied(self, request, response):
        return self._apply(request, response, "occupied")

    def _on_set_healthy(self, request, response):
        return self._apply(request, response, "healthy")

    def _apply(self, request: SetTrayBool.Request, response: SetTrayBool.Response, field: str):
        targets, invalid = self._resolve_trays(request.trays)
        if invalid:
            response.success = False
            response.message = f"존재하지 않는 tray: {invalid} (유효 범위 0..{self._tray_count - 1})"
            response.applied = []
            self.log.warn(f"[{self.name}] 거부: {response.message}")
            return response

        for tray in targets:
            self._trays[tray][field] = bool(request.value)
        frozen = [t for t in targets if not self._trays[t]["tracking"]]
        note = f" (tracking 꺼짐 — 발행값 동결 유지: {frozen})" if field == "occupied" and frozen else ""
        self.log.info(f"[{self.name}] tray {targets} {field} -> {request.value}{note}")
        self._publish()

        response.success = True
        response.message = f"{field} set to {request.value} for {targets}"
        response.applied = targets
        return response

    # ------------------------------------------------------------------ #
    # 헬퍼
    # ------------------------------------------------------------------ #
    def _current(self, state) -> tuple[float, bool]:
        """추적(tracking) 중의 발행값. 실물처럼 판정 불가(healthy=false)면 0 g / 비점유다."""
        if not state["healthy"]:
            return 0.0, False
        weight = self._occupied_weight_g if state["occupied"] else 0.0
        return weight, state["occupied"]

    def _sensed_occupied(self, state) -> bool:
        """실물 `tray.occupied` 에 해당 — tracking 중에만 판 위의 상황을 따라간다."""
        return self._current(state)[1] if state["tracking"] else state["frozen_occupied"]

    def _freeze(self, state) -> None:
        """지금 값을 동결하고 tracking 을 끈다."""
        state["frozen_weight"], state["frozen_occupied"] = self._current(state)
        state["tracking"] = False

    def _real_targets(self, trays) -> list[int]:
        """실물 서비스의 trays 해석. 빈 배열 = 전체, 범위 밖은 무시(매칭 없음은 _finish 가 실패로)."""
        if len(trays) == 0:
            return list(range(self._tray_count))
        requested = {int(t) for t in trays}
        return [t for t in range(self._tray_count) if t in requested]

    def _finish(self, response, label: str, report: list[str], ok: bool):
        if not report:
            response.success = False
            response.message = "no matching tray for the requested indices"
        else:
            response.success = ok
            response.message = "; ".join(report)
        self.log.info(f"[{self.name}] {label}: {response.message}")
        self._publish()
        return response

    def _resolve_trays(self, trays) -> tuple[list[int], list[int]]:
        """더미 조작 SetTrayBool 의 trays 해석. 빈 배열 = 전체. 두 번째 값은 범위 밖 목록."""
        if len(trays) == 0:
            return list(range(self._tray_count)), []
        requested = [int(t) for t in trays]
        invalid = [t for t in requested if t not in self._trays]
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
            entry.healthy = state["healthy"]
            if state["tracking"]:
                weight, occupied = self._current(state)
                state["frozen_weight"], state["frozen_occupied"] = weight, occupied
            else:
                # 동결 구간: 참값은 마지막 인지 값으로 고정. healthy 만 실시간이다.
                weight, occupied = state["frozen_weight"], state["frozen_occupied"]
            entry.weight_g = float(weight)
            entry.occupied = bool(occupied)
            msg.trays.append(entry)
        self._pub.publish(msg)
