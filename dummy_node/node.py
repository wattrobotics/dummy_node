"""dummy_node 코어 노드.

`interfaces/` 폴더에 등록된 모든 더미 인터페이스를 자동으로 로드한다.
인터페이스를 추가/삭제해도 이 파일은 수정할 필요가 없다.
"""

from __future__ import annotations

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from dummy_node.registry import discover_interfaces, get_registered


class DummyNode(Node):
    """등록된 더미 인터페이스들을 호스팅하는 단일 노드."""

    def __init__(self):
        super().__init__("dummy_node")

        # interfaces/ 하위 모듈을 모두 import 하여 레지스트리를 채운다.
        discover_interfaces()

        self._interfaces = []
        for cls in get_registered():
            try:
                instance = cls(self)
                self._interfaces.append(instance)
                self.get_logger().info(
                    f"인터페이스 로드 완료: [{cls.name}] ({cls.__name__})"
                )
            except Exception as exc:  # noqa: BLE001 - 하나 실패해도 나머지는 계속 로드
                self.get_logger().error(
                    f"인터페이스 로드 실패: {cls.__name__} -> {exc}"
                )

        self.get_logger().info(
            f"총 {len(self._interfaces)}개 인터페이스 활성화됨."
        )


def main(args=None):
    rclpy.init(args=args)
    node = DummyNode()

    # 여러 인터페이스(특히 action + service)를 동시에 처리하기 위해
    # MultiThreadedExecutor 사용. 각 인터페이스는 자체 ReentrantCallbackGroup 보유.
    executor = MultiThreadedExecutor()
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
