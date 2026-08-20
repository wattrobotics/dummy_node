"""더미 알림 서버.

알림 요청을 받아 **수신 경로와 요청 전문을 터미널에 출력**한다. 실제 알림 수단
(운영자 보고 상행·팝업·푸시)이 준비되기 전까지, 알림이 실제로 도달했는지와 무엇이
실렸는지를 사람이 눈으로 확인하는 용도다.

- Service (Notify) `notify` -> /dummy/notify
    알림 수신. 수신할 때마다 순번을 부여해 응답에 담고 로그 블록을 찍는다.
- Service (SetBool) `notify/set_fail` -> /dummy/notify/set_fail
    `data=true` 로 두면 이후 모든 알림 요청을 **거부**한다.
    보고 실패 누적 경로(예: BT 의 report_fail_streak -> 한도 초과 이탈)를 시험하기 위한 것이다.

스펙 대응: `TakeParcelScreen.md` §4.5 (`운영자에게 보고` = §10 D8, `사용자에게 알림`).
문구는 받지 않는다 — 문구 소유자는 수신 측이고 BT 는 사유 코드만 넘긴다(§1.2).
"""

import time

from example_interfaces.srv import SetBool

from dummy_node_interfaces.srv import Notify

from dummy_node.registry import InterfaceBase, register

# channel/level 로 알려진 값. 그 외 값도 거부하지 않고 경고만 남긴다 —
# 더미가 값 목록을 좁게 잡으면 아직 정해지지 않은 채널을 시험할 수 없다.
KNOWN_CHANNELS = ("operator", "user")
KNOWN_LEVELS = ("info", "warn", "error")

LINE = "─" * 58


@register
class NotifyServer(InterfaceBase):
    name = "notify"

    service = "notify"                 # -> /dummy/notify
    srv_set_fail = "notify/set_fail"    # -> /dummy/notify/set_fail

    def setup(self) -> None:
        # config: true 로 두고 기동하면 처음부터 모든 알림을 거부한다.
        self._fail = bool(self.param("notify_fail", False))

        self._seq = 0

        self._srv = self.node.create_service(
            Notify, self.service, self._on_notify, callback_group=self.callback_group
        )
        self._srv_fail = self.node.create_service(
            SetBool,
            self.srv_set_fail,
            self._on_set_fail,
            callback_group=self.callback_group,
        )

        # 로그에 찍을 수신 경로. **FQN 으로 해석해서** 찍는다 — 이 출력의 목적이
        # "어느 경로로 들어왔는가" 이므로 상대 이름(`notify`)으로는 쓸모가 없다.
        # (Service.srv_name 은 생성 시 넘긴 상대 이름을 그대로 돌려준다.)
        self._path = self.node.resolve_service_name(self.service)

        self.log.info(
            f"[{self.name}] 알림 서버 대기: {self._path} "
            f"(거부 모드={'on' if self._fail else 'off'})"
        )

    # ------------------------------------------------------------------ #
    # 알림 수신
    # ------------------------------------------------------------------ #
    def _on_notify(
        self, request: Notify.Request, response: Notify.Response
    ) -> Notify.Response:
        self._seq += 1
        seq = self._seq

        level = request.level or "info"
        self._print(request, level, seq)

        if request.channel not in KNOWN_CHANNELS:
            self.log.warn(
                f"[{self.name}] 알려지지 않은 channel='{request.channel}' "
                f"(알려진 값: {', '.join(KNOWN_CHANNELS)}) — 수신은 계속한다"
            )
        if level not in KNOWN_LEVELS:
            self.log.warn(
                f"[{self.name}] 알려지지 않은 level='{level}' "
                f"(알려진 값: {', '.join(KNOWN_LEVELS)}) — info 로 다룬다"
            )

        response.seq = seq
        if self._fail:
            # 거부 모드: 수신 내용은 출력하되 실패를 돌려준다.
            response.success = False
            response.message = "notify 거부 모드가 켜져 있습니다 (notify/set_fail)"
            self.log.warn(f"[{self.name}] #{seq} 거부 모드로 실패 반환")
            return response

        response.success = True
        response.message = f"notified (#{seq})"
        return response

    def _on_set_fail(
        self, request: SetBool.Request, response: SetBool.Response
    ) -> SetBool.Response:
        self._fail = bool(request.data)
        self.log.info(
            f"[{self.name}] 거부 모드 -> {'on' if self._fail else 'off'}"
        )
        response.success = True
        response.message = f"fail mode {'on' if self._fail else 'off'}"
        return response

    # ------------------------------------------------------------------ #
    # 터미널 출력
    # ------------------------------------------------------------------ #
    def _print(self, request: Notify.Request, level: str, seq: int) -> None:
        now = self.node.get_clock().now().nanoseconds
        stamp = (
            time.strftime("%H:%M:%S", time.localtime(now // 1_000_000_000))
            + f".{(now // 1_000_000) % 1000:03d}"
        )
        block = (
            f"\n[notify] ── 수신 {LINE[:44]}\n"
            f"  경로     : {self._path}\n"
            f"  channel  : {request.channel or '(빈 값)'}\n"
            f"  level    : {level}\n"
            f"  code     : {request.code or '(빈 값)'}\n"
            f"  mission  : {request.mission_id or '(없음)'}\n"
            f"  params   : {request.params_json or '{}'}\n"
            f"  수신시각 : {stamp}  (#{seq})\n"
            f"{LINE}"
        )
        # level 을 로그 심각도에 반영한다 — 터미널에서 색과 필터로 구분된다.
        if level == "error":
            self.log.error(block)
        elif level == "warn":
            self.log.warn(block)
        else:
            self.log.info(block)
