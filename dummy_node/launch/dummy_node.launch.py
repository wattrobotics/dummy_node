"""dummy_node 실행용 런치 파일.

사용 예:
    ros2 launch dummy_node dummy_node.launch.py
    ros2 launch dummy_node dummy_node.launch.py initial_floor:=3
    ros2 launch dummy_node dummy_node.launch.py initial_is_person:=true
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description() -> LaunchDescription:
    initial_floor = LaunchConfiguration("initial_floor")
    initial_is_person = LaunchConfiguration("initial_is_person")
    person_presence_period_sec = LaunchConfiguration("person_presence_period_sec")

    return LaunchDescription([
        DeclareLaunchArgument(
            "initial_floor",
            default_value="1",
            description="시작 시 로봇의 초기 층 (0층은 존재하지 않음, 지하 1층=-1)",
        ),
        DeclareLaunchArgument(
            "initial_is_person",
            default_value="false",
            description="시작 시 사람 인식 값 (/dummy/person_presence/set 서비스로 변경 가능)",
        ),
        DeclareLaunchArgument(
            "person_presence_period_sec",
            default_value="0.1",
            description="사람 인식 발행 주기(초). 실기 인식 노드 주기에 맞춰 조정한다",
        ),
        Node(
            package="dummy_node",
            executable="dummy_node",
            name="dummy_node",
            # namespace는 노드 코드에서 'dummy'로 고정되어 있으므로 여기서 지정하지 않는다.
            output="screen",
            emulate_tty=True,
            parameters=[{
                # 런치 인자는 문자열이므로 int로 캐스팅 (코드에서 int로 선언됨).
                "initial_floor": ParameterValue(initial_floor, value_type=int),
                "initial_is_person": ParameterValue(
                    initial_is_person, value_type=bool
                ),
                "person_presence_period_sec": ParameterValue(
                    person_presence_period_sec, value_type=float
                ),
            }],
        ),
    ])
