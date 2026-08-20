# 전 tray 로드셀 상태. (TakeParcelScreen.md §3.2)
#
# header.stamp 는 staleness 판정에 쓴다:
#   now - header.stamp > load_cell_stale_ms  =>  "로드셀 동작하지 않음"
std_msgs/Header header
LoadCellTray[] trays
