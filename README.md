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

## 빌드

```bash
cd ~/ros2_ws
colcon build --packages-select dummy_node
source install/setup.bash
```

## 상태

초기 git 세팅 단계. 노드 구현은 이후 추가 예정.
