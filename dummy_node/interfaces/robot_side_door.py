"""사이드 도어 인터페이스.

- Service (SetBool) `robot/open_side_door` -> /dummy/robot/open_side_door
    true  요청 = 문 열기, false 요청 = 문 닫기.
- Publisher (Bool) `robot/side_door/status` -> /dummy/robot/side_door/status
    현재 문 열림 상태(True=열림)를 발행.

문 상태는 로컬 변수(`_door_open`)로 보관하며 기본값은 False(닫힘).
상태 변경 시 즉시 발행하고, 늦게 붙는 구독자를 위해 주기적으로도 발행한다.
"""

from example_interfaces.msg import Bool
from example_interfaces.srv import SetBool

from dummy_node.registry import InterfaceBase, register


@register
class RobotSideDoor(InterfaceBase):
    name = "robot_side_door"

    service = "robot/open_side_door"       # -> /dummy/robot/open_side_door
    status_topic = "robot/side_door/status"  # -> /dummy/robot/side_door/status
    status_period_sec = 1.0

    def setup(self) -> None:
        # 문 열림 상태 (기본값: 닫힘).
        self._door_open = False

        self._status_pub = self.node.create_publisher(
            Bool, self.status_topic, 10, callback_group=self.callback_group
        )
        self._srv = self.node.create_service(
            SetBool,
            self.service,
            self._on_request,
            callback_group=self.callback_group,
        )
        # 주기적으로 현재 상태를 발행 (late-joining 구독자 대응).
        self._timer = self.node.create_timer(
            self.status_period_sec,
            self._publish_status,
            callback_group=self.callback_group,
        )

        # 기동 시 초기 상태 1회 발행.
        self._publish_status()

    def _on_request(
        self, request: SetBool.Request, response: SetBool.Response
    ) -> SetBool.Response:
        self._door_open = request.data
        action = "열림" if self._door_open else "닫힘"
        self.log.info(f"[{self.name}] 사이드 도어 -> {action}")

        # 상태 변경 즉시 발행.
        self._publish_status()

        response.success = True
        response.message = f"side door {'opened' if self._door_open else 'closed'}"
        return response

    def _publish_status(self) -> None:
        self._status_pub.publish(Bool(data=self._door_open))
