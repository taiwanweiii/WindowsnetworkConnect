"""列出本機可用的網路介面與其 IPv4 位址。"""

import socket

import psutil


def list_interfaces(include_loopback: bool = False) -> dict[str, str]:
    """回傳 {介面名稱: IPv4 位址},只包含目前啟用 (up) 的介面。

    Windows 上名稱會是「乙太網路」「Wi-Fi」「區域連線* 2」(手機 USB 網路共用
    通常叫「乙太網路 2」或含 Remote NDIS 的介面) 等。
    """
    result: dict[str, str] = {}
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        stat = stats.get(name)
        if stat is not None and not stat.isup:
            continue
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            if not include_loopback and addr.address.startswith("127."):
                continue
            # 排除 APIPA (169.254.x.x) — 代表這張卡其實沒拿到 IP
            if addr.address.startswith("169.254."):
                continue
            result[name] = addr.address
            break
    return result
