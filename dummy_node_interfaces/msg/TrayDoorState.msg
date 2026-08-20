# 전 tray 문 상태. (TakeParcelScreen.md §3.3.3)
#
# QoS 는 transient_local + 주기 재발행이다. 문은 지속 상태이므로 BT 가 기동 직후
# '문이 열려있는가' 를 판정할 수 있어야 한다. 단 latch 라도 header.stamp 로
# staleness 를 판정한다(초과 시 '상태 불명' — 열림·닫힘 어느 쪽으로도 단정하지 않는다).
std_msgs/Header header
TrayDoor[] doors
