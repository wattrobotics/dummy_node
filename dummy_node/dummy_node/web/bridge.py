"""웹 콘솔의 상태 보관소.

ROS 구독 콜백(executor 스레드)과 HTTP 핸들러(서버 스레드)가 **서로 다른 스레드**에서
같은 데이터를 만지므로, 모든 접근을 하나의 `Condition` 으로 직렬화한다.

`rev`(개정 번호)는 상태가 바뀔 때마다 1씩 오른다. SSE 스트림은 이 값만 감시하므로,
값이 바뀌지 않으면 브라우저로 아무것도 보내지 않는다.
"""

from __future__ import annotations

import threading
import time


class BridgeError(Exception):
    """웹 콘솔이 요청을 수행하지 못했을 때 던진다. HTTP 400/504 로 변환된다."""


# 문의 이동 방향. 토픽만으로는 `opening` 과 `closing` 을 구분할 수 없어
# (둘 다 open=false, closed=false) 마지막으로 지시한 명령을 기억한다.
INTENT_OPEN = "open"
INTENT_CLOSE = "close"

# 최근 이벤트 보관 개수. 화면 하단 로그에 쓴다.
LOG_LIMIT = 30


class StateStore:
    """스레드 안전한 상태 스냅샷 보관소."""

    def __init__(self, tray_count: int, door_names: list[str] | None = None):
        """`tray_count` 는 토픽 수신 전에 화면에 그릴 칸 수의 초기값이다.

        `door_names` 는 실물 문 컨트롤러 이름 목록(인덱스 = tray)이다. 문마다 status 토픽과
        command 액션이 따로 있으므로 문 수만큼 칸을 만든다.
        """
        self._cv = threading.Condition()
        self._rev = 0
        self._tray_count = int(tray_count)

        # 토픽 수신값. `None` 은 "아직 한 번도 받지 못했다" 를 뜻한다.
        self._person: dict | None = None
        self._load_cell: dict | None = None
        self._tray_door: dict | None = None
        self._floor: dict | None = None
        self._side_door: dict | None = None

        # 실물 모사 인터페이스(phidget_load_cell · side_door)의 수신값.
        self._phidget_load_cell: dict | None = None
        self._door_names = [str(n) for n in (door_names or [])]
        self._side_doors: list[dict | None] = [None] * len(self._door_names)

        # 토픽만으로 알 수 없는 값들.
        self._door_intent: dict[int, str] = {}
        self._notify_fail: bool | None = None   # 파라미터 조회 실패 시 None
        self._close_job: dict = {
            "active": False,
            "trays": [],
            "force": False,
            "remaining": [],
            "obstructed": False,
            "last_result": None,
        }
        # 실물 로드셀의 tracking 은 토픽에 없다. 기동 시 파라미터와 웹에서 보낸 서비스
        # 응답으로만 알 수 있으므로 "웹 기준 추정값" 이다(터미널 조작은 반영되지 않는다).
        self._tracking: dict[int, bool] = {}
        # 실물 문 goal 진행 상황. 문마다 하나씩.
        self._door_jobs: list[dict] = [
            {
                "active": False,
                "command": None,
                "state": None,
                "elapsed": 0.0,
                "retries": 0,
                "last_result": None,
            }
            for _ in self._door_names
        ]
        self._log: list[dict] = []

    # ------------------------------------------------------------------ #
    # 변경 (executor 스레드 / HTTP 스레드 양쪽에서 호출)
    # ------------------------------------------------------------------ #
    def _bump(self) -> None:
        """호출자는 반드시 `self._cv` 를 잡은 상태여야 한다."""
        self._rev += 1
        self._cv.notify_all()

    def set_person(self, is_person: bool, stamp_age_ms: float) -> None:
        """사람 인식 토픽 수신값을 갱신한다."""
        with self._cv:
            self._person = {
                "is_person": bool(is_person),
                "stamp_age_ms": round(stamp_age_ms, 1),
                "at": time.monotonic(),
            }
            self._bump()

    def set_load_cell(self, trays: list[dict], stamp_age_ms: float) -> None:
        """로드셀 토픽 수신값을 갱신한다."""
        with self._cv:
            self._load_cell = {
                "trays": trays,
                "stamp_age_ms": round(stamp_age_ms, 1),
                "at": time.monotonic(),
            }
            self._sync_tray_count(len(trays))
            self._bump()

    def set_tray_door(self, doors: list[dict], stamp_age_ms: float) -> None:
        """문 상태 토픽 수신값을 갱신하고, 각 문의 이동 방향까지 판정해 넣는다."""
        with self._cv:
            for door in doors:
                tray = door["tray"]
                if door["open"] or door["closed"]:
                    # 이동이 끝났으므로 방향 기억을 지운다.
                    self._door_intent.pop(tray, None)
                door["phase"] = self._phase(door)
            self._tray_door = {
                "doors": doors,
                "stamp_age_ms": round(stamp_age_ms, 1),
                "at": time.monotonic(),
            }
            self._sync_tray_count(len(doors))
            self._bump()

    def set_floor(self, current: int) -> None:
        """현재 층 토픽 수신값을 갱신한다."""
        with self._cv:
            self._floor = {"current": int(current), "at": time.monotonic()}
            self._bump()

    def set_side_door(self, is_open: bool) -> None:
        """측면 문 상태 토픽 수신값을 갱신한다."""
        with self._cv:
            self._side_door = {"open": bool(is_open), "at": time.monotonic()}
            self._bump()

    def set_door_intent(self, trays: list[int], intent: str) -> None:
        """열기/닫기 명령을 보낸 직후에 호출한다."""
        with self._cv:
            for tray in trays:
                self._door_intent[int(tray)] = intent
            self._bump()

    def set_notify_fail(self, value: bool | None) -> None:
        """알림 거부 모드 표시값을 갱신한다. `None` 은 아직 알지 못한다는 뜻이다."""
        with self._cv:
            self._notify_fail = None if value is None else bool(value)
            self._bump()

    def update_close_job(self, **fields) -> None:
        """진행 중인 닫기 goal 의 진행 상황·결과를 갱신한다."""
        with self._cv:
            self._close_job.update(fields)
            self._bump()

    def set_phidget_load_cell(self, trays: list[dict], stamp_age_ms: float) -> None:
        """실물 로드셀 모사(/load_cell_state) 수신값을 갱신한다."""
        with self._cv:
            self._phidget_load_cell = {
                "trays": trays,
                "stamp_age_ms": round(stamp_age_ms, 1),
                "at": time.monotonic(),
            }
            self._sync_tray_count(len(trays))
            self._bump()

    def set_side_door_status(self, idx: int, status: str) -> None:
        """실물 문 status(closed|open) 수신값을 갱신한다."""
        with self._cv:
            if 0 <= idx < len(self._side_doors):
                self._side_doors[idx] = {"status": str(status), "at": time.monotonic()}
                self._bump()

    def set_tracking(self, values: dict[int, bool]) -> None:
        """각 tray 의 tracking 표시값을 갱신한다(웹 기준 추정값)."""
        if not values:
            return
        with self._cv:
            self._tracking.update({int(k): bool(v) for k, v in values.items()})
            self._bump()

    def update_door_job(self, idx: int, **fields) -> None:
        """실물 문 goal 의 진행 상황·결과를 갱신한다."""
        with self._cv:
            if 0 <= idx < len(self._door_jobs):
                self._door_jobs[idx].update(fields)
                self._bump()

    def add_log(self, level: str, message: str) -> None:
        """화면 하단에 보일 이벤트를 한 줄 남긴다."""
        with self._cv:
            self._log.append({
                "level": level,
                "message": message,
                "at": time.time(),
            })
            del self._log[:-LOG_LIMIT]
            self._bump()

    # ------------------------------------------------------------------ #
    # 조회
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        """현재 상태를 JSON 직렬화 가능한 dict 로 만든다.

        `age_sec`(마지막 수신 후 경과)은 **직렬화 시점**에 계산한다. 그래야 발행이
        멈춘 순간부터 화면의 경과 시간이 계속 늘어나 연결 끊김을 알아볼 수 있다.
        """
        now = time.monotonic()
        with self._cv:
            return {
                "rev": self._rev,
                "tray_count": self._tray_count,
                "person": self._aged(self._person, now, ("is_person", "stamp_age_ms")),
                "load_cell": self._aged(self._load_cell, now, ("trays", "stamp_age_ms")),
                "tray_door": self._aged(self._tray_door, now, ("doors", "stamp_age_ms")),
                "floor": self._aged(self._floor, now, ("current",)),
                "side_door": self._aged(self._side_door, now, ("open",)),
                "close_job": dict(self._close_job),
                "notify_fail": self._notify_fail,
                # 실물 모사 인터페이스
                "phidget_load_cell": self._aged(
                    self._phidget_load_cell, now, ("trays", "stamp_age_ms")
                ),
                "tracking": [self._tracking.get(i) for i in range(self._tray_count)],
                "door_names": list(self._door_names),
                "side_doors": [self._aged(d, now, ("status",)) for d in self._side_doors],
                "door_jobs": [dict(j) for j in self._door_jobs],
                "log": list(self._log),
            }

    def rev(self) -> int:
        """현재 개정 번호. 상태가 바뀔 때마다 1씩 오른다."""
        with self._cv:
            return self._rev

    def wait_for_change(self, last_rev: int, timeout: float) -> int:
        """`rev` 가 `last_rev` 를 넘을 때까지 기다린다. 시간이 지나면 현재 값을 돌려준다."""
        with self._cv:
            if self._rev <= last_rev:
                self._cv.wait(timeout)
            return self._rev

    # ------------------------------------------------------------------ #
    # 헬퍼
    # ------------------------------------------------------------------ #
    def _sync_tray_count(self, observed: int) -> None:
        """토픽에 실린 배열 길이가 tray 개수의 정본이다.

        `dummy_web` 은 별도 프로세스라 `dummy_node` 의 `tray_count` 파라미터를 그대로
        물려받지 못한다. 파라미터로 받은 초기값이 어긋나 있어도 첫 수신에서 교정된다.
        """
        if observed > 0 and observed != self._tray_count:
            self._tray_count = observed

    def _phase(self, door: dict) -> str:
        """`open`/`closed` 두 bool 에서 사람이 읽는 상태 이름을 만든다."""
        if door["open"]:
            return "open"
        if door["closed"]:
            return "closed"
        # 둘 다 false = 이동 중. 방향은 마지막 명령으로만 알 수 있다.
        intent = self._door_intent.get(door["tray"])
        if intent == INTENT_CLOSE:
            return "closing"
        if intent == INTENT_OPEN:
            return "opening"
        return "moving"

    @staticmethod
    def _aged(section: dict | None, now: float, keys: tuple) -> dict:
        if section is None:
            return {"seen": False}
        out = {"seen": True, "age_sec": round(now - section["at"], 2)}
        for key in keys:
            out[key] = section[key]
        return out
