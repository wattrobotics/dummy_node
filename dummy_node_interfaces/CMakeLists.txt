cmake_minimum_required(VERSION 3.8)
project(dummy_node_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
# PersonPresence/LoadCellState/TrayDoorState 가 std_msgs/Header 를 사용한다.
find_package(std_msgs REQUIRED)
# CloseTrayDoor 액션 생성에 필요(goal/result/feedback 래핑 타입).
find_package(action_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/SetInt.srv"
  "srv/SetTrayBool.srv"
  "srv/OpenTrayDoor.srv"
  "msg/PersonPresence.msg"
  "msg/LoadCellTray.msg"
  "msg/LoadCellState.msg"
  "msg/TrayDoor.msg"
  "msg/TrayDoorState.msg"
  "action/CloseTrayDoor.action"
  DEPENDENCIES std_msgs
)

ament_package()
