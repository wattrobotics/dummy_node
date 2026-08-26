"""웹 콘솔의 HTTP 서버.

Python 표준 라이브러리만 쓴다. `rosbridge_suite`·`flask`·`tornado` 중 어느 것도
설치를 요구하지 않기 위해서다.

핸들러는 executor 가 아닌 **서버 스레드**에서 실행되므로, ROS 호출은 반드시
`DummyWebNode` 가 제공하는 래퍼(`call_service` 등)를 거친다.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dummy_node.web.bridge import BridgeError

STATIC_DIR = Path(__file__).parent / "static"

# POST 본문 상한. 이 콘솔이 받는 요청은 모두 작은 JSON 이다.
MAX_BODY = 64 * 1024

# SSE 최소 전송 간격(초). 상태 토픽이 10Hz 로 오므로 그대로 흘리면 과하다.
SSE_MIN_INTERVAL = 0.2
# 상태가 바뀌지 않아도 이 주기로 한 번은 보낸다 — 경과 시간(age_sec)을 갱신해
# 발행이 끊긴 것을 화면에서 알아볼 수 있게 한다.
SSE_IDLE_INTERVAL = 1.0


class ConsoleHandler(BaseHTTPRequestHandler):
    """정적 파일과 `/api/*` 를 처리한다. `node` 는 서버가 주입한다."""

    protocol_version = "HTTP/1.1"
    server_version = "dummy_web"
    node = None  # ConsoleHttpServer 가 클래스 속성으로 주입한다

    # ------------------------------------------------------------------ #
    # 라우팅
    # ------------------------------------------------------------------ #
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 규약
        path = self.path.split("?", 1)[0]

        if path == "/api/state":
            self._send_json(HTTPStatus.OK, self.node.state.snapshot())
        elif path == "/api/events":
            self._stream_events()
        else:
            self._send_static(path)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 규약
        path = self.path.split("?", 1)[0]
        try:
            body = self._read_json()
            if path.startswith("/api/service/"):
                key = path[len("/api/service/"):]
                result = self.node.call_service(key, body)
            elif path == "/api/action/close":
                result = self.node.send_close_goal(
                    body.get("trays") or [], bool(body.get("force", False))
                )
            elif path == "/api/action/close/cancel":
                result = self.node.cancel_close_goal()
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "없는 경로입니다."})
                return
        except BridgeError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 - 서버가 죽지 않도록 화면에 돌려준다
            self.node.get_logger().error(f"요청 처리 실패 ({path}): {exc}")
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"처리 실패: {exc}"}
            )
            return

        self._send_json(HTTPStatus.OK, {"ok": True, "result": result})

    # ------------------------------------------------------------------ #
    # SSE 스트림
    # ------------------------------------------------------------------ #
    def _stream_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # 본문 길이를 미리 알 수 없는 스트림이므로 연결 종료가 곧 메시지의 끝이다.
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        state = self.node.state
        last_rev = -1
        try:
            while True:
                snapshot = state.snapshot()
                payload = json.dumps(snapshot, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                last_rev = snapshot["rev"]

                # 변경을 기다리되, 변경이 없어도 주기적으로 한 번은 내보낸다.
                state.wait_for_change(last_rev, SSE_IDLE_INTERVAL)
                # 변경이 몰아쳐도 전송 빈도를 제한한다.
                time.sleep(SSE_MIN_INTERVAL)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 브라우저가 탭을 닫았다 — 정상 종료다

    # ------------------------------------------------------------------ #
    # 정적 파일
    # ------------------------------------------------------------------ #
    def _send_static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()

        # 경로 순회 차단: static 디렉터리 밖은 어떤 경우에도 내보내지 않는다.
        if not target.is_file() or not target.is_relative_to(STATIC_DIR.resolve()):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "없는 경로입니다."})
            return

        content = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    # ------------------------------------------------------------------ #
    # 헬퍼
    # ------------------------------------------------------------------ #
    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise BridgeError("요청 본문이 너무 큽니다.")
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError(f"JSON 을 해석하지 못했습니다: {exc}") from exc
        if not isinstance(body, dict):
            raise BridgeError("요청 본문은 JSON 객체여야 합니다.")
        return body

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, fmt: str, *args) -> None:
        """접근 로그를 ROS 로거로 보낸다 — stderr 직접 출력은 노드 로그를 어지럽힌다."""
        if self.node is not None:
            self.node.get_logger().debug(f"[http] {fmt % args}")


class ConsoleHttpServer:
    """HTTP 서버를 데몬 스레드에서 돌린다. ROS executor 를 막지 않기 위함이다."""

    def __init__(self, node, host: str, port: int):
        handler = type("BoundConsoleHandler", (ConsoleHandler,), {"node": node})
        self._httpd = ThreadingHTTPServer((host, port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="dummy_web_http", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
