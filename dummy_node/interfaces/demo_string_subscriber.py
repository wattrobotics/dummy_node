"""예시: Topic Subscriber 인터페이스.

`~/echo_in` 토픽을 구독하여 수신한 내용을 로그로 남긴다.
새 subscriber를 만들려면 이 파일을 복사해서 토픽명/타입/콜백을 바꾸면 된다.
"""

from example_interfaces.msg import String

from dummy_node.registry import InterfaceBase, register


@register
class DemoStringSubscriber(InterfaceBase):
    name = "demo_string_subscriber"

    topic = "echo_in"  # namespace="dummy" → /dummy/echo_in

    def setup(self) -> None:
        self._sub = self.node.create_subscription(
            String, self.topic, self._on_msg, 10, callback_group=self.callback_group
        )

    def _on_msg(self, msg: String) -> None:
        self.log.info(f"[{self.name}] 수신: '{msg.data}'")
