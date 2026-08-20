# 로드셀 1개(= tray 1단)의 상태. (TakeParcelScreen.md §3.2)
int32 tray        # 적재 단 인덱스. 0=상단 (PackageRecord.tray 규약)
float32 weight_g  # 측정 무게(g). **진단용** — BT 판정에는 쓰지 않는다(occupied 만 쓴다)
bool occupied     # 최소 감지 무게 초과 여부
bool healthy      # 이 로드셀이 정상 동작하는가. false 는 실패가 아니라 사용자 확인 경로로 우회
