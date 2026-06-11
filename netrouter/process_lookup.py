"""從 proxy 收到的本機連線反查是哪個程式連進來的。

原理:client 連到 proxy 時,我們知道它的來源 port;
用系統的 TCP 連線表 (Windows 上即 GetExtendedTcpTable) 找到
laddr.port 相同的連線,取得 PID 再轉成程式名稱。
"""

import time

import psutil

# 短暫快取,避免每條連線都掃一次整張 TCP 表
_cache: dict[int, tuple[float, str | None]] = {}
_CACHE_TTL = 3.0


def list_network_processes() -> list[str]:
    """列出目前有 TCP 連線的所有程式名稱 (給規則編輯的下拉選單用)。"""
    names: set[str] = set()
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if not conn.pid:
                continue
            try:
                names.add(psutil.Process(conn.pid).name())
            except psutil.Error:
                continue
    except (psutil.Error, OSError):
        pass
    return sorted(names)


def process_name_for_port(client_port: int) -> str | None:
    now = time.monotonic()
    hit = _cache.get(client_port)
    if hit is not None and now - hit[0] < _CACHE_TTL:
        return hit[1]

    name: str | None = None
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr and conn.laddr.port == client_port and conn.pid:
                try:
                    name = psutil.Process(conn.pid).name()
                except psutil.Error:
                    name = None
                break
    except (psutil.Error, OSError):
        pass

    _cache[client_port] = (now, name)
    # 簡單防止快取無限長大
    if len(_cache) > 512:
        cutoff = now - _CACHE_TTL
        for port in [p for p, (t, _) in _cache.items() if t < cutoff]:
            _cache.pop(port, None)
    return name
