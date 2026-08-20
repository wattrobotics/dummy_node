cmake_minimum_required(VERSION 3.8)
project(dummy_node_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
# PersonPresence.msg 가 std_msgs/Header 를 사용한다.
find_package(std_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/SetInt.srv"
  "msg/PersonPresence.msg"
  DEPENDENCIES std_msgs
)

ament_package()
