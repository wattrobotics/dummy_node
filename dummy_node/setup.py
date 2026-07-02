from setuptools import find_packages, setup

package_name = 'dummy_node'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
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
        ],
    },
)
