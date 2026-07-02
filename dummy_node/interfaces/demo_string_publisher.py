"""예시: Topic Publisher 인터페이스.

`~/chatter` 토픽으로 1Hz 주기의 String 메시지를 발행한다.
새 publisher를 만들려면 이 파일을 복사해서 토픽명/타입/주기를 바꾸면 된다.
"""

from example_interfaces.msg import String

from dummy_node.registry import InterfaceBase, register


@register
class DemoStringPublisher(InterfaceBase):
    name = "demo_string_publisher"

    # 필요 시 조정할 파라미터.
    topic = "~/chatter"
    period_sec = 1.0

    def setup(self) -> None:
        self._count = 0
        self._pub = self.node.create_publisher(
            String, self.topic, 10, callback_group=self.callback_group
        )
        self._timer = self.node.create_timer(
            self.period_sec, self._on_timer, callback_group=self.callback_group
        )

    def _on_timer(self) -> None:
        msg = String()
        msg.data = f"dummy hello #{self._count}"
        self._pub.publish(msg)
        self._count += 1
