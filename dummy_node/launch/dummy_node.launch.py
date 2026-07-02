"""dummy_node 실행용 런치 파일.

사용 예:
    ros2 launch dummy_node dummy_node.launch.py
    ros2 launch dummy_node dummy_node.launch.py initial_floor:=3
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description() -> LaunchDescription:
    initial_floor = LaunchConfiguration("initial_floor")

    return LaunchDescription([
        DeclareLaunchArgument(
            "initial_floor",
            default_value="1",
            description="시작 시 로봇의 초기 층 (0층은 존재하지 않음, 지하 1층=-1)",
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
            }],
        ),
    ])
