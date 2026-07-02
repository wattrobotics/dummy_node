# dummy_node

ROS2 Jazzy 기반 **더미 / 테스트 하네스 노드** 모음 패키지.

실제 비즈니스 로직 없이, 외부 도구·테스트가 다양한 방식으로 상호작용할 수 있는
**테스트 대상(test target/fixture)** 노드들을 제공합니다.

## 목적

- 외부에서 이 노드들을 상대로 다양한 ROS2 상호작용을 시험할 수 있게 한다.
  - Topic publish / subscribe (다양한 메시지 타입, QoS)
  - Service 요청 / 응답
  - Action goal / feedback / result / cancel
  - Parameter 읽기 / 쓰기 (동적 파라미터 콜백)
  - TF 프레임 발행
  - Lifecycle 상태 전이
  - 의도적 지연 / 에러 / 타임아웃 주입 (장애 시뮬레이션)

## 환경

- ROS2 Jazzy
- 빌드 타입: `ament_python`

## 구조

```
dummy_node/
├── node.py            # 코어 노드. interfaces/ 를 스캔해 자동 로드 (수정 불필요)
├── registry.py        # InterfaceBase + @register 데코레이터 + 자동 탐색
└── interfaces/        # 인터페이스 구현 (여기에 파일만 추가하면 확장 완료)
    ├── demo_string_publisher.py     # Topic Publisher 예시
    ├── demo_string_subscriber.py    # Topic Subscriber 예시
    ├── demo_add_two_ints_service.py # Service Server 예시
    └── demo_fibonacci_action.py     # Action Server 예시
```

### 새 인터페이스 추가법

1. `interfaces/` 안에 새 `.py` 파일 생성.
2. `InterfaceBase`를 상속한 클래스에 `@register` 데코레이터를 붙인다.
3. `setup()` 안에서 원하는 ROS 엔티티(publisher/subscriber/service/action)를 생성.

코어 노드는 기동 시 `interfaces/` 폴더를 자동 스캔하므로 `node.py`는 절대 손대지 않는다.

```python
from example_interfaces.msg import String
from dummy_node.registry import InterfaceBase, register

@register
class MyPublisher(InterfaceBase):
    name = "my_publisher"

    def setup(self):
        self._pub = self.node.create_publisher(String, "~/my_topic", 10)
        self.node.create_timer(1.0, self._tick)

    def _tick(self):
        self._pub.publish(String(data="hi"))
```

## 빌드 & 실행

```bash
cd ~/ros2_ws
colcon build --packages-select dummy_node
source install/setup.bash
ros2 run dummy_node dummy_node
```

기본 제공 인터페이스 (노드명 `dummy_node` 기준):

| 종류 | 이름 | 타입 |
|------|------|------|
| Publisher | `/dummy_node/chatter` | `example_interfaces/msg/String` (1Hz) |
| Subscriber | `/dummy_node/echo_in` | `example_interfaces/msg/String` |
| Service | `/dummy_node/add_two_ints` | `example_interfaces/srv/AddTwoInts` |
| Action | `/dummy_node/fibonacci` | `example_interfaces/action/Fibonacci` |
