"""本地分流 proxy。

同一個監聽 port 自動辨識三種協定:
  - SOCKS5 (第一個 byte 為 0x05)
  - HTTP CONNECT (https 隧道,VS Code / Copilot 主要走這個)
  - 一般 HTTP proxy 請求 (絕對網址形式)

決定出口時呼叫 RuleEngine;規則指定的介面不是 default 時,
對外 socket 先 bind() 到該網卡的 IP,Windows 就會從那張網卡送出。
"""

import socket
import struct
import threading
import time
from urllib.parse import urlsplit

from .interfaces import list_interfaces
from .process_lookup import process_name_for_port
from .rules import DEFAULT_INTERFACE, RuleEngine

CONNECT_TIMEOUT = 10
RELAY_BUFSIZE = 65536
MAX_HEADER = 65536


class ProxyServer:
    def __init__(
        self,
        engine: RuleEngine,
        host: str = "127.0.0.1",
        port: int = 8080,
        on_event=None,
    ):
        self.engine = engine
        self.host = host
        self.port = port
        # on_event(dict):每條連線建立/失敗時回呼,給 GUI 顯示用
        self.on_event = on_event or (lambda event: None)
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    # ---------- 生命週期 ----------

    def start(self) -> None:
        if self._running:
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(128)
        self._server = server
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None

    @property
    def running(self) -> bool:
        return self._running

    def _serve(self) -> None:
        while self._running:
            try:
                client, addr = self._server.accept()
            except OSError:
                break
            threading.Thread(
                target=self._handle, args=(client, addr), daemon=True
            ).start()

    # ---------- 連線處理 ----------

    def _handle(self, client: socket.socket, addr) -> None:
        try:
            client.settimeout(CONNECT_TIMEOUT)
            first = client.recv(1, socket.MSG_PEEK)
            if not first:
                client.close()
                return
            if first == b"\x05":
                self._handle_socks5(client, addr)
            else:
                self._handle_http(client, addr)
        except (OSError, ValueError, TimeoutError):
            try:
                client.close()
            except OSError:
                pass

    def _handle_socks5(self, client: socket.socket, addr) -> None:
        # 握手:VER NMETHODS METHODS → 回覆「無需驗證」
        header = self._recv_exact(client, 2)
        nmethods = header[1]
        self._recv_exact(client, nmethods)
        client.sendall(b"\x05\x00")

        # 請求:VER CMD RSV ATYP ...
        ver, cmd, _, atyp = self._recv_exact(client, 4)
        if cmd != 1:  # 只支援 CONNECT
            client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            client.close()
            return
        if atyp == 1:  # IPv4
            host = socket.inet_ntoa(self._recv_exact(client, 4))
        elif atyp == 3:  # 網域名稱
            length = self._recv_exact(client, 1)[0]
            # 線路上的網域已是 ASCII (國際網域為 punycode 形式)
            host = self._recv_exact(client, length).decode("ascii", "replace")
        elif atyp == 4:  # IPv6
            host = socket.inet_ntop(socket.AF_INET6, self._recv_exact(client, 16))
        else:
            client.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
            client.close()
            return
        port = struct.unpack(">H", self._recv_exact(client, 2))[0]

        upstream = self._open_upstream(host, port, addr, protocol="socks5")
        if upstream is None:
            client.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            client.close()
            return
        client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        self._relay(client, upstream)

    def _handle_http(self, client: socket.socket, addr) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(4096)
            if not chunk:
                client.close()
                return
            data += chunk
            if len(data) > MAX_HEADER:
                raise ValueError("HTTP header too large")

        head, _, body = data.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ", 2)

        if method.upper() == "CONNECT":
            host, _, port_str = target.rpartition(":")
            port = int(port_str or 443)
            upstream = self._open_upstream(host, port, addr, protocol="https")
            if upstream is None:
                client.sendall(
                    b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"
                )
                client.close()
                return
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if body:
                upstream.sendall(body)
            self._relay(client, upstream)
            return

        # 一般 HTTP proxy 請求:GET http://example.com/path HTTP/1.1
        parts = urlsplit(target)
        host = parts.hostname or ""
        port = parts.port or 80
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query

        upstream = self._open_upstream(host, port, addr, protocol="http")
        if upstream is None:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            client.close()
            return

        # 改寫成對目標主機的相對路徑請求;強制 Connection: close
        # 避免同一條 keep-alive 連線之後跑去要求別的主機
        out_lines = [f"{method} {path} {version}"]
        for line in lines[1:]:
            key = line.split(":", 1)[0].strip().lower()
            if key in ("proxy-connection", "connection"):
                continue
            out_lines.append(line)
        out_lines.append("Connection: close")
        upstream.sendall(
            ("\r\n".join(out_lines) + "\r\n\r\n").encode("latin-1") + body
        )
        self._relay(client, upstream)

    # ---------- 出口選擇 ----------

    def _open_upstream(
        self, host: str, port: int, client_addr, protocol: str
    ) -> socket.socket | None:
        process = process_name_for_port(client_addr[1])
        rule = self.engine.decide(host, port, process)
        iface = rule.interface if rule else self.engine.default_interface

        event = {
            "time": time.strftime("%H:%M:%S"),
            "protocol": protocol,
            "process": process or "?",
            "host": host,
            "port": port,
            "interface": iface,
            "ok": True,
            "error": "",
        }
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(CONNECT_TIMEOUT)
            if iface != DEFAULT_INTERFACE:
                ip = list_interfaces().get(iface)
                if ip is None:
                    raise OSError(f"介面 {iface!r} 不存在或未啟用")
                # 關鍵:綁定來源 IP,讓封包從指定網卡出去
                sock.bind((ip, 0))
            sock.connect((host, port))
            sock.settimeout(None)
        except OSError as exc:
            event["ok"] = False
            event["error"] = str(exc)
            self.on_event(event)
            return None
        self.on_event(event)
        return sock

    # ---------- 工具 ----------

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise OSError("connection closed during handshake")
            buf += chunk
        return buf

    @staticmethod
    def _relay(a: socket.socket, b: socket.socket) -> None:
        a.settimeout(None)
        b.settimeout(None)

        def pipe(src: socket.socket, dst: socket.socket) -> None:
            try:
                while True:
                    data = src.recv(RELAY_BUFSIZE)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    try:
                        s.close()
                    except OSError:
                        pass

        t = threading.Thread(target=pipe, args=(b, a), daemon=True)
        t.start()
        pipe(a, b)
