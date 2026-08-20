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
- 패키지 2개: `dummy_node`(ament_python, 노드), `dummy_node_interfaces`(ament_cmake, 커스텀 srv/msg)

## 구조

```
dummy_node/                        # git repo 루트 (멀티패키지)
├── dummy_node/                    # ament_python 패키지 (노드 본체)
│   ├── dummy_node/
│   │   ├── node.py                # 코어 노드. interfaces/ 를 스캔해 자동 로드 (수정 불필요)
│   │   ├── registry.py            # InterfaceBase + @register 데코레이터 + 자동 탐색
│   │   └── interfaces/            # 인터페이스 구현 (여기에 파일만 추가하면 확장 완료)
│   │       ├── demo_string_publisher.py     # Topic Publisher 예시
│   │       ├── demo_string_subscriber.py    # Topic Subscriber 예시
│   │       ├── demo_add_two_ints_service.py # Service Server 예시
│   │       ├── demo_fibonacci_action.py     # Action Server 예시
│   │       ├── robot_side_door.py           # SetBool 서비스 + Bool status 퍼블리셔
│   │       ├── robot_floor.py               # 층 이동 (SetInt 서비스 2종 + Int32 퍼블리셔)
│   │       └── person_presence.py           # 사람 인식 (PersonPresence 퍼블 + SetBool 토글)
│   ├── config/
│   │   └── dummy_node.yaml        # 파라미터 기본값 (모든 파라미터는 여기서 관리)
│   └── launch/
│       └── dummy_node.launch.py   # config_file 인자로 YAML 경로 지정
└── dummy_node_interfaces/         # ament_cmake 패키지 (커스텀 인터페이스)
    ├── srv/SetInt.srv             # 정수 값 설정용 범용 서비스
    └── msg/PersonPresence.msg     # 사람 인식 결과 (Header + is_person)
```

### 새 인터페이스 추가법

1. `interfaces/` 안에 새 `.py` 파일 생성.
2. `InterfaceBase`를 상속한 클래스에 `@register` 데코레이터를 붙인다.
3. `setup()` 안에서 원하는 ROS 엔티티(publisher/subscriber/service/action)를 생성.
4. 파라미터가 필요하면 `setup()` 에서 `declare_parameter()` 로 선언하고,
   기본값 항목을 `config/dummy_node.yaml` 에 추가한다.

코어 노드는 기동 시 `interfaces/` 폴더를 자동 스캔하므로 `node.py`는 절대 손대지 않는다.
런치 파일도 파라미터 파일 경로만 넘기므로 파라미터가 늘어도 수정하지 않는다.

> **네임스페이스 규칙:** 모든 topic/service/action 이름은 `dummy/` 하위에 있어야 한다.
> 코어 노드가 `namespace="dummy"`로 생성되므로, 인터페이스는 **상대 이름**(예: `"my_topic"`)만
> 쓰면 자동으로 `/dummy/my_topic`이 된다. `~/`나 절대경로(`/...`)는 규칙을 깨므로 쓰지 않는다.

```python
from example_interfaces.msg import String
from dummy_node.registry import InterfaceBase, register

@register
class MyPublisher(InterfaceBase):
    name = "my_publisher"

    def setup(self):
        # 상대 이름 → /dummy/my_topic
        self._pub = self.node.create_publisher(String, "my_topic", 10)
        self.node.create_timer(1.0, self._tick)

    def _tick(self):
        self._pub.publish(String(data="hi"))
```

## 파라미터 (config)

모든 파라미터는 **`config/dummy_node.yaml`** 한 곳에서 관리한다. 인터페이스가 파라미터를
추가할 때 이 파일에만 항목을 넣으면 되고, **런치 파일은 수정하지 않는다.**

| 파라미터 | 타입 | 기본값 | 소유 인터페이스 | 의미 |
|---|---|---|---|---|
| `initial_floor` | int | `1` | `robot_floor` | 시작 층 (0층 부재, 지하 1층=-1) |
| `initial_is_person` | bool | `false` | `person_presence` | 시작 시 사람 인식 값 |
| `person_presence_period_sec` | double | `0.1` | `person_presence` | 사람 인식 발행 주기(초) |

> **YAML 키는 FQN(`/dummy/dummy_node`)이어야 한다.** 코어 노드가 코드에서
> `namespace="dummy"` 로 생성되므로 완전한 노드 이름이 `/dummy/dummy_node` 다.
> `dummy_node:` 만 쓰면 **적용되지 않고 조용히 무시된다**(실측 확인).

> **동적 파라미터는 미지원이다.** 각 인터페이스는 `setup()` 에서 값을 1회만 읽으므로
> `ros2 param set` 은 반영되지 않는다. 런타임 상태 변경은 파라미터가 아니라
> 서비스(`/dummy/robot/set_current_floor`, `/dummy/person_presence/set` 등)로 한다.

## 빌드 & 실행

```bash
cd ~/ros2_ws
colcon build --packages-select dummy_node_interfaces dummy_node
source install/setup.bash

# 직접 실행
ros2 run dummy_node dummy_node

# 런치 파일로 실행 (권장). 파라미터는 config/dummy_node.yaml 에서 읽는다.
ros2 launch dummy_node dummy_node.launch.py

# 다른 config 파일로 실행
ros2 launch dummy_node dummy_node.launch.py config_file:=/path/to/my_dummy.yaml
```

기본 제공 인터페이스 (모두 `dummy/` 네임스페이스 하위):

| 종류 | 이름 | 타입 |
|------|------|------|
| Publisher | `/dummy/chatter` | `example_interfaces/msg/String` (1Hz) |
| Subscriber | `/dummy/echo_in` | `example_interfaces/msg/String` |
| Service | `/dummy/add_two_ints` | `example_interfaces/srv/AddTwoInts` |
| Action | `/dummy/fibonacci` | `example_interfaces/action/Fibonacci` |
| Service | `/dummy/robot/open_side_door` | `example_interfaces/srv/SetBool` (문 열기/닫기) |
| Publisher | `/dummy/robot/side_door/status` | `example_interfaces/msg/Bool` (문 상태) |
| Service | `/dummy/robot/set_current_floor` | `dummy_node_interfaces/srv/SetInt` (즉시 층 변경) |
| Service | `/dummy/robot/set_target_floor` | `dummy_node_interfaces/srv/SetInt` (1층/초 이동) |
| Publisher | `/dummy/robot/current_floor` | `example_interfaces/msg/Int32` (현재 층, 1Hz) |
| Publisher | `/dummy/person_presence` | `dummy_node_interfaces/msg/PersonPresence` (사람 인식, 기본 10Hz, volatile) |
| Service | `/dummy/person_presence/set` | `example_interfaces/srv/SetBool` (`is_person` 토글) |

### 층(floor) 규칙

- 지하 1층 = `-1`, **0층은 존재하지 않음** (이동 시 `1 ↔ -1`로 0을 건너뜀).
- 초기 층은 파라미터 `initial_floor`(config)로 설정, 기본값 `1`.

```bash
# 단일 값만 임시로 바꾸는 경우
ros2 run dummy_node dummy_node --ros-args -p initial_floor:=3
```

### 사람 인식(person_presence)

`docs/spec/tasks/WaitNewTask.md` §3.6 규격의 스텁이다. 실제 인식 노드가 없는 단계에서
BT의 사람인식 조건 노드를 시험하는 데 쓴다.

- 토픽 `/dummy/person_presence` — `Header header` + `bool is_person`.
  QoS는 **reliable · depth 1 · volatile**(스펙 지정). latch가 아니므로 과거의
  `is_person=true` 가 재구독 시 되살아나지 않는다.
- `header.stamp` 는 발행 시점 시계로 매번 갱신한다. BT는
  `now - header.stamp > person_stale_ms` 면 사람 없음으로 간주한다.
- 값은 서비스로 토글한다.

```bash
# 사람 있음으로 전환
ros2 service call /dummy/person_presence/set example_interfaces/srv/SetBool "{data: true}"
# 사람 없음으로 전환
ros2 service call /dummy/person_presence/set example_interfaces/srv/SetBool "{data: false}"

# 발행 확인
ros2 topic echo /dummy/person_presence
```

파라미터: `initial_is_person`(기본 `false`), `person_presence_period_sec`(기본 `0.1`).
발행 주기는 실기 인식 노드 주기가 정해지면 그에 맞춰 조정한다(스펙 T1과 연동).
