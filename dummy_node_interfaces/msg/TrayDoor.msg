# 적재함 문 1개의 상태. (TakeParcelScreen.md §3.3.3)
int32 tray        # 0=상단
bool open         # 완전 열림
bool closed       # 완전 닫힘
bool obstructed   # 끼임 감지
# open 과 closed 가 **둘 다 false = 이동 중**이다. 둘 다 true 는 발행자가 내지 않는다.
