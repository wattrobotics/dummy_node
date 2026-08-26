"""dummy_node 실행용 런치 파일.

파라미터는 모두 YAML 파일(`config/dummy_node.yaml`)로 관리한다. 인터페이스가 파라미터를
추가해도 이 런치 파일은 수정하지 않는다 — YAML에만 항목을 넣으면 된다.

사용 예:
    # 설치된 기본 config 사용
    ros2 launch dummy_node dummy_node.launch.py

    # 다른 config 파일로 실행 (기본 파일을 복사해 수정한 뒤 경로 지정)
    ros2 launch dummy_node dummy_node.launch.py config_file:=/path/to/my_dummy.yaml

    # 웹 제어 콘솔 없이 기존 동작만 실행
    ros2 launch dummy_node dummy_node.launch.py enable_web:=false

단일 값만 임시로 바꾸려면 run 쪽 override가 더 간단하다:
    ros2 run dummy_node dummy_node --ros-args \
        --params-file <config> -p initial_floor:=3
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    config_file = LaunchConfiguration("config_file")
    enable_web = LaunchConfiguration("enable_web")

    default_config = PathJoinSubstitution([
        FindPackageShare("dummy_node"), "config", "dummy_node.yaml",
    ])

    return LaunchDescription([
        DeclareLaunchArgument(
            "config_file",
            default_value=default_config,
            description="파라미터 YAML 파일 경로 (기본: 패키지에 설치된 config/dummy_node.yaml)",
        ),
        DeclareLaunchArgument(
            "enable_web",
            default_value="true",
            description="웹 제어 콘솔(dummy_web) 을 함께 띄울지 여부",
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
        # 웹 제어 콘솔. dummy_node 와 **별도 프로세스**로 띄운다 — 같은 노드가 자기
        # 서비스를 호출하는 형태를 피하고, dummy_node 가 죽어도 웹은 살아남아
        # '연결 끊김' 을 화면에 표시할 수 있다.
        Node(
            package="dummy_node",
            executable="dummy_web",
            name="dummy_web",
            # 네임스페이스는 노드 코드에서 'dummy' 로 고정된다.
            # YAML 키도 FQN 인 '/dummy/dummy_web' 이어야 한다.
            output="screen",
            emulate_tty=True,
            parameters=[config_file],
            condition=IfCondition(enable_web),
        ),
    ])
