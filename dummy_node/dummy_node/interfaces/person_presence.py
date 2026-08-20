"""사람 인식(person presence) 스텁 인터페이스.

`WaitNewTask.md` §3.6 "사람 인식 — 신규 정의" 규격을 그대로 구현한 더미다.
실제 인식 노드가 준비되기 전까지 BT의 사람인식 조건 노드를 시험하는 데 쓴다.

- Publisher (PersonPresence) `person_presence` -> /dummy/person_presence
    현재 `is_person` 값을 주기적으로 발행. QoS는 스펙대로 reliable · depth 1 · volatile.
- Service (SetBool) `person_presence/set` -> /dummy/person_presence/set
    `data=true` 요청 = 사람 있음, `data=false` 요청 = 사람 없음. 즉시 1회 발행한다.

`volatile` 인 이유(스펙): 사람 인식은 실시간 값이므로 latch로 두면 재구독 시 과거의
`is_person=true` 를 현재 상태로 오인한다. 따라서 late-joining 구독자를 위한 latch 대신
주기 발행으로만 값을 전달한다.

`header.stamp` 는 발행 시점의 노드 시계로 매번 갱신한다. BT가
`now - header.stamp > person_stale_ms` 로 stale 판정을 하기 때문이다.
"""

from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

from example_interfaces.srv import SetBool

from dummy_node_interfaces.msg import PersonPresence as PersonPresenceMsg

from dummy_node.registry import InterfaceBase, register


@register
class PersonPresence(InterfaceBase):
    name = "person_presence"

    topic = "person_presence"            # -> /dummy/person_presence
    service = "person_presence/set"      # -> /dummy/person_presence/set

    def setup(self) -> None:
        # config: 초기 사람 인식 값.
        self._is_person = bool(
            self.node.declare_parameter("initial_is_person", False).value
        )
        # config: 발행 주기. 스펙 §9 T1(person_stale_ms)이 이 주기가 정해진 뒤
        # 확정되므로, 실기 인식 노드의 주기에 맞춰 조정할 수 있도록 파라미터로 둔다.
        self._period_sec = float(
            self.node.declare_parameter("person_presence_period_sec", 0.1).value
        )

        # 스펙 §3.6: reliable, depth 1, volatile.
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self._pub = self.node.create_publisher(
            PersonPresenceMsg, self.topic, qos, callback_group=self.callback_group
        )
        self._srv = self.node.create_service(
            SetBool,
            self.service,
            self._on_request,
            callback_group=self.callback_group,
        )
        self._timer = self.node.create_timer(
            self._period_sec, self._publish, callback_group=self.callback_group
        )

        self.log.info(
            f"[{self.name}] 초기 is_person = {self._is_person}, "
            f"발행 주기 = {self._period_sec}s"
        )
        self._publish()

    def _on_request(
        self, request: SetBool.Request, response: SetBool.Response
    ) -> SetBool.Response:
        self._is_person = bool(request.data)
        state = "사람 있음" if self._is_person else "사람 없음"
        self.log.info(f"[{self.name}] is_person -> {self._is_person} ({state})")

        # 값 변경 즉시 발행 (다음 주기까지 기다리지 않는다).
        self._publish()

        response.success = True
        response.message = f"is_person set to {self._is_person}"
        return response

    def _publish(self) -> None:
        msg = PersonPresenceMsg()
        # stale 판정 기준이므로 발행 시점 시계로 매번 갱신한다.
        msg.header.stamp = self.node.get_clock().now().to_msg()
        msg.is_person = self._is_person
        self._pub.publish(msg)
