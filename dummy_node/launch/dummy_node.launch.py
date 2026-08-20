"""dummy_node 실행용 런치 파일.

파라미터는 모두 YAML 파일(`config/dummy_node.yaml`)로 관리한다. 인터페이스가 파라미터를
추가해도 이 런치 파일은 수정하지 않는다 — YAML에만 항목을 넣으면 된다.

사용 예:
    # 설치된 기본 config 사용
    ros2 launch dummy_node dummy_node.launch.py

    # 다른 config 파일로 실행 (기본 파일을 복사해 수정한 뒤 경로 지정)
    ros2 launch dummy_node dummy_node.launch.py config_file:=/path/to/my_dummy.yaml

단일 값만 임시로 바꾸려면 run 쪽 override가 더 간단하다:
    ros2 run dummy_node dummy_node --ros-args \
        --params-file <config> -p initial_floor:=3
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    config_file = LaunchConfiguration("config_file")

    default_config = PathJoinSubstitution([
        FindPackageShare("dummy_node"), "config", "dummy_node.yaml",
    ])

    return LaunchDescription([
        DeclareLaunchArgument(
            "config_file",
            default_value=default_config,
            description="파라미터 YAML 파일 경로 (기본: 패키지에 설치된 config/dummy_node.yaml)",
        ),
        Node(
            package="dummy_node",
            executable="dummy_node",
            name="dummy_node",
            # namespace는 노드 코드에서 'dummy'로 고정되어 있으므로 여기서 지정하지 않는다.
            # 따라서 YAML의 키도 FQN인 '/dummy/dummy_node' 여야 한다.
            output="screen",
            emulate_tty=True,
            parameters=[config_file],
        ),
    ])
