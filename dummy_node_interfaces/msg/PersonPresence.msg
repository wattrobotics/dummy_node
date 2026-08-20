# 사람 인식 결과. (WaitNewTask.md §3.6 규격)
#
# header.stamp 는 BT 측 staleness 판정(now - stamp > person_stale_ms => 사람 없음)에
# 사용되므로 반드시 인식 시점을 담아야 한다.
std_msgs/Header header
bool is_person
