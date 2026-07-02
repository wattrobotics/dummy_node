"""예시: Service Server 인터페이스.

`~/add_two_ints` 서비스로 두 정수의 합을 반환한다.
새 service를 만들려면 이 파일을 복사해서 서비스명/타입/콜백을 바꾸면 된다.
"""

from example_interfaces.srv import AddTwoInts

from dummy_node.registry import InterfaceBase, register


@register
class DemoAddTwoIntsService(InterfaceBase):
    name = "demo_add_two_ints_service"

    service = "add_two_ints"  # namespace="dummy" → /dummy/add_two_ints

    def setup(self) -> None:
        self._srv = self.node.create_service(
            AddTwoInts,
            self.service,
            self._on_request,
            callback_group=self.callback_group,
        )

    def _on_request(
        self, request: AddTwoInts.Request, response: AddTwoInts.Response
    ) -> AddTwoInts.Response:
        response.sum = request.a + request.b
        self.log.info(
            f"[{self.name}] 요청: {request.a} + {request.b} = {response.sum}"
        )
        return response
