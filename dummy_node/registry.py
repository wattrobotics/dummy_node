"""더미 인터페이스 등록/자동탐색 인프라.

새 인터페이스(topic publisher/subscriber, service server, action server 등)를
추가하려면 `interfaces/` 패키지 안에 파일을 하나 만들고, `InterfaceBase`를
상속한 클래스에 `@register` 데코레이터를 붙이기만 하면 된다.
코어 노드(`node.py`)는 수정할 필요가 없다.
"""

from __future__ import annotations

import importlib
import pkgutil

from rclpy.node import Node

# 등록된 인터페이스 클래스들이 쌓이는 전역 레지스트리.
_REGISTRY: list[type["InterfaceBase"]] = []


def register(cls: type["InterfaceBase"]) -> type["InterfaceBase"]:
    """더미 인터페이스 클래스를 전역 레지스트리에 등록하는 데코레이터."""
    _REGISTRY.append(cls)
    return cls


def get_registered() -> list[type["InterfaceBase"]]:
    """등록 순서대로 인터페이스 클래스 목록을 반환한다."""
    return list(_REGISTRY)


def discover_interfaces(package: str = "dummy_node.interfaces") -> None:
    """`interfaces` 패키지 하위의 모든 모듈을 import 하여 `@register`를 트리거한다.

    모듈을 import 하는 순간 데코레이터가 실행되어 `_REGISTRY`에 등록된다.
    따라서 새 파일을 폴더에 넣기만 하면 자동으로 로드 대상이 된다.
    """
    pkg = importlib.import_module(package)
    for _, module_name, _ in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{package}.{module_name}")


class InterfaceBase:
    """모든 더미 인터페이스의 공통 베이스 클래스.

    서브클래스가 지켜야 할 계약:
      - 클래스 속성 `name`: 로그/식별용 사람이 읽는 이름.
      - `setup()`: 이 안에서 자신의 ROS 엔티티(publisher/subscriber/service/
        action server 등)를 생성한다. `self.node`(코어 노드)와
        `self.log`(로거)를 자유롭게 사용할 수 있다.

    콜백 그룹이 필요하면 `self.callback_group`(인터페이스별 ReentrantCallbackGroup)을
    사용하면 서로 다른 인터페이스 간 동시 실행이 격리된다.

    [네임스페이스 규칙] 코어 노드가 namespace="dummy"로 생성되므로, 인터페이스는
    항상 **상대 이름**(예: "chatter")만 사용한다. 그러면 자동으로 /dummy/... 아래에
    놓인다. `~/` 접두사나 절대경로("/...")는 규칙을 깨뜨리므로 사용하지 않는다.
    """

    name: str = "unnamed_interface"

    def __init__(self, node: Node):
        from rclpy.callback_groups import ReentrantCallbackGroup

        self.node = node
        self.log = node.get_logger()
        self.callback_group = ReentrantCallbackGroup()
        self.setup()

    def setup(self) -> None:
        raise NotImplementedError(
            f"{type(self).__name__}는 setup()을 구현해야 합니다."
        )
