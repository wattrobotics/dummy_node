# dummy_node

ROS2 Jazzy 기반 **더미 / 테스트 하네스 노드** 모음 패키지.

실제 비즈니스 로직 없이, 외부 도구·테스트가 다양한 방식으로 상호작용할 수 있는
**테스트 대상(test target/fixture)** 노드들을 제공합니다.

## 목적

외부에서 이 노드들을 상대로 다양한 ROS2 상호작용을 시험할 수 있게 한다.

**현재 지원**

- Topic publish / subscribe (다양한 메시지 타입, QoS)
- Service 요청 / 응답
- Action goal / feedback / result / cancel
- Parameter **읽기 전용** — 기동 시 config YAML로 초기값 주입 (아래 [파라미터](#파라미터-config) 참고)

**미지원**

- **동적 파라미터 (파라미터 쓰기)** — 각 인터페이스는 `setup()` 에서 값을 1회만 읽으므로
  `ros2 param set` 은 반영되지 않는다. 런타임 상태 변경은 서비스로 한다.
- TF 프레임 발행
- Lifecycle 상태 전이
- 의도적 지연 / 에러 / 타임아웃 주입 (장애 시뮬레이션)

미지원 항목은 필요해지는 시점에 인터페이스를 추가해 지원한다.

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
│   │       ├── person_presence.py           # 사람 인식 (PersonPresence 퍼블 + SetBool 토글)
│   │       ├── load_cell.py                 # 로드셀 (LoadCellState 퍼블 + occupied/healthy 토글)
│   │       └── tray_door.py                 # 적재함 문 (열기 srv + 닫기 action + 상태 토픽)
│   ├── config/
│   │   └── dummy_node.yaml        # 파라미터 기본값 (모든 파라미터는 여기서 관리)
│   └── launch/
│       └── dummy_node.launch.py   # config_file 인자로 YAML 경로 지정
└── dummy_node_interfaces/         # ament_cmake 패키지 (커스텀 인터페이스)
    ├── srv/SetInt.srv             # 정수 값 설정용 범용 서비스
    ├── srv/SetTrayBool.srv        # 더미 제어용 tray 단위 bool 설정
    ├── srv/OpenTrayDoor.srv       # 적재함 문 열기(개시)
    ├── action/CloseTrayDoor.action# 적재함 문 닫기(취소·끼임 지원)
    ├── msg/PersonPresence.msg     # 사람 인식 결과 (Header + is_person)
    ├── msg/LoadCellState.msg      # 로드셀 상태 (Header + LoadCellTray[])
    ├── msg/LoadCellTray.msg       # tray 1단 로드셀 (weight_g/occupied/healthy)
    ├── msg/TrayDoorState.msg      # 문 상태 (Header + TrayDoor[])
    └── msg/TrayDoor.msg           # 문 1개 (open/closed/obstructed)
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
| `tray_count` | int | `2` | **공유** (`load_cell` · `tray_door`) | 적재함 단 개수 (0=상단) |
| `load_cell_period_sec` | double | `0.1` | `load_cell` | 로드셀 상태 발행 주기(초) |
| `initial_load_cell_occupied` | bool | `false` | `load_cell` | 시작 시 물건 감지 여부 |
| `initial_load_cell_healthy` | bool | `true` | `load_cell` | 시작 시 로드셀 정상 여부 |
| `occupied_weight_g` | double | `1000.0` | `load_cell` | `occupied` 일 때 보고할 무게(g). 진단용 |
| `initial_doors_open` | bool | `false` | `tray_door` | 시작 시 문 열림 여부 |
| `tray_door_state_period_sec` | double | `0.2` | `tray_door` | 문 상태 발행 주기(초) |
| `door_open_duration_sec` | double | `2.0` | `tray_door` | 열기 개시 → 완전 열림 소요 시간 |
| `door_close_duration_sec` | double | `2.0` | `tray_door` | 닫기 개시 → 완전 닫힘 소요 시간 |
| `door_close_timeout_sec` | double | `10.0` | `tray_door` | `CloseTrayDoor` 1회 goal 제한 시간 |

> **YAML 키는 FQN(`/dummy/dummy_node`)이어야 한다.** 코어 노드가 코드에서
> `namespace="dummy"` 로 생성되므로 완전한 노드 이름이 `/dummy/dummy_node` 다.
> `dummy_node:` 만 쓰면 **적용되지 않고 조용히 무시된다**(실측 확인).

> **둘 이상의 인터페이스가 같은 파라미터를 쓸 때는 `InterfaceBase.param()` 을 쓴다.**
> 모든 인터페이스가 하나의 노드를 공유하므로 `declare_parameter` 를 두 번 호출하면
> `ParameterAlreadyDeclaredException` 이 나고, 로드가 개별 try/except 로 감싸여 있어
> **해당 인터페이스만 조용히 빠진다.** `param()` 은 이미 선언돼 있으면 기존 값을 읽으므로
> 로드 순서에 무관하다 (`tray_count` 가 그 예다).

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
| Publisher | `/dummy/load_cell_state` | `dummy_node_interfaces/msg/LoadCellState` (기본 10Hz, volatile) |
| Service | `/dummy/load_cell/set_occupied` | `dummy_node_interfaces/srv/SetTrayBool` (물건 감지 토글) |
| Service | `/dummy/load_cell/set_healthy` | `dummy_node_interfaces/srv/SetTrayBool` (로드셀 정상여부 토글) |
| Publisher | `/dummy/robot/tray_door/state` | `dummy_node_interfaces/msg/TrayDoorState` (기본 5Hz, transient_local) |
| Service | `/dummy/robot/tray_door/open` | `dummy_node_interfaces/srv/OpenTrayDoor` (열기 개시) |
| **Action** | `/dummy/robot/tray_door/close` | `dummy_node_interfaces/action/CloseTrayDoor` (닫기, 취소 가능) |
| Service | `/dummy/robot/tray_door/set_obstructed` | `dummy_node_interfaces/srv/SetTrayBool` (끼임 감지 주입) |

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

### 로드셀(load_cell)

`docs/spec/tasks/TakeParcelScreen.md` §3.2 규격의 스텁이다.

- 토픽 `/dummy/load_cell_state` — `Header` + `LoadCellTray[]`(`tray`/`weight_g`/`occupied`/`healthy`).
  QoS 는 **reliable · depth 1 · volatile**(스펙 지정).
- `weight_g` 는 **진단용**이며 BT 판정에는 쓰이지 않는다(스펙). 따라서 별도 설정 수단 없이
  `occupied` 로부터 파생시킨다(`occupied` → `occupied_weight_g`, 아니면 `0.0`).
  값이 두 곳에서 관리되어 서로 어긋나는 상태를 만들지 않기 위함이다.
- `healthy=false` 는 "로드셀로 확인 불가" 를 뜻하며 실패가 아니다 — BT는 사용자 확인 경로로 우회한다.

```bash
# tray 0 만 물건 감지
ros2 service call /dummy/load_cell/set_occupied dummy_node_interfaces/srv/SetTrayBool "{trays: [0], value: true}"
# 전 tray 로드셀 고장 시뮬레이션 (빈 배열 = 전체)
ros2 service call /dummy/load_cell/set_healthy dummy_node_interfaces/srv/SetTrayBool "{trays: [], value: false}"
```

### 적재함 문(tray_door)

`docs/spec/tasks/TakeParcelScreen.md` §3.3 규격의 스텁이다. **열기 = Service, 닫기 = Action,
상태 = Topic** 이라는 형태 배분이 스펙에서 확정된 것이며, 기존 `robot_side_door`(단일 문)는
tray 개념이 없어 이 스펙을 만족하지 못한다. BT는 이 인터페이스만 쓴다.

문 동작 모형: `closed → opening → open → closing → closed`.
`opening`/`closing` 중에는 `open` 과 `closed` 가 **둘 다 false**(= 이동 중)다.

| 상황 | 결과 |
|---|---|
| 정상 닫기 | `success=true`, `error=ERROR_NONE(0)` |
| 끼임 감지 + `force=false` | 중단 후 **열림 방향 복귀**, `error=ERROR_OBSTRUCTED(1)` |
| 끼임 지속 + `force=true` | 진행되지 않아 `door_close_timeout_sec` 에 걸림, `error=ERROR_TIMEOUT(2)` |
| 진행 중 goal 취소 | **열림 방향 복귀**, status `CANCELED`, `error=ERROR_ABORTED(3)` |
| 범위 밖 tray | goal 즉시 abort, `error=ERROR_ABORTED(3)` |

> **취소·끼임 시 열림으로 복귀시키는 이유**: 중간에서 멈춘 문을 "완전 열림" 으로 단정하지 않기
> 위해 실제로 열림 동작을 수행한다. 이동 중 상태(`open=false, closed=false`)로 방치하면
> 문 상태가 영구히 "불명" 이 되어 BT의 판정(§3.3.4)이 성립하지 않는다.

```bash
# 열기 (빈 배열 = 전체 tray). success 는 수락 여부이며 열렸다는 뜻이 아니다
ros2 service call /dummy/robot/tray_door/open dummy_node_interfaces/srv/OpenTrayDoor "{trays: []}"

# 닫기 (완료까지 feedback 수신)
ros2 action send_goal /dummy/robot/tray_door/close \
    dummy_node_interfaces/action/CloseTrayDoor "{trays: [], force: false}" -f

# 끼임 주입 → 닫기 시 ERROR_OBSTRUCTED 확인
ros2 service call /dummy/robot/tray_door/set_obstructed dummy_node_interfaces/srv/SetTrayBool "{trays: [1], value: true}"

# 상태 확인
ros2 topic echo /dummy/robot/tray_door/state
```
