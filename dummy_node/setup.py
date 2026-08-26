import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'dummy_node'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    # 웹 콘솔의 정적 파일(HTML/CSS/JS)은 파이썬 패키지 안에 함께 설치한다.
    # http_server.py 가 `__file__` 기준 상대 경로로 찾으므로 share/ 로 보내지 않는다.
    package_data={'dummy_node.web': ['static/*']},
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'),
            glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'),
            glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='wntdev99',
    maintainer_email='jeongmin.choi@wattrobotics.ai',
    description='ROS2 Jazzy 더미/테스트 하네스 노드 모음.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'dummy_node = dummy_node.node:main',
            'dummy_web = dummy_node.web.console_node:main',
        ],
    },
)
