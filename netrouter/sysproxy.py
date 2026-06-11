"""一鍵設定/還原系統 Proxy。

Windows:寫 WinINET registry (目前使用者,不需管理員權限),
        Chrome/Edge/VS Code 等都吃這個設定。
macOS  :用 networksetup 設定各網路服務的 HTTP/HTTPS proxy
        (可能會要求一次系統授權)。
"""

import platform
import subprocess

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

PROXY_OVERRIDE = "localhost;127.*;<local>"  # 本機連線不走 proxy


def enable(host: str, port: int) -> None:
    """把系統 proxy 指到 host:port。失敗時丟 OSError。"""
    if IS_WINDOWS:
        _win_set(enabled=True, server=f"{host}:{port}")
    elif IS_MAC:
        _mac_set(enabled=True, host=host, port=port)
    else:
        raise OSError(f"不支援的平台: {platform.system()}")


def disable() -> None:
    """關閉系統 proxy(保留原本填的位址,只切換開關)。"""
    if IS_WINDOWS:
        _win_set(enabled=False)
    elif IS_MAC:
        _mac_set(enabled=False)


# ---------- Windows ----------

def _win_set(enabled: bool, server: str | None = None) -> None:
    import ctypes
    import winreg

    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        0,
        winreg.KEY_SET_VALUE,
    )
    with key:
        winreg.SetValueEx(
            key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enabled else 0
        )
        if server:
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
            winreg.SetValueEx(
                key, "ProxyOverride", 0, winreg.REG_SZ, PROXY_OVERRIDE
            )

    # 通知 WinINET 設定變了,讓瀏覽器立即生效
    INTERNET_OPTION_SETTINGS_CHANGED = 39
    INTERNET_OPTION_REFRESH = 37
    wininet = ctypes.windll.Wininet
    wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
    wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)


# ---------- macOS ----------

def _mac_services() -> list[str]:
    out = subprocess.run(
        ["networksetup", "-listallnetworkservices"],
        capture_output=True, text=True, timeout=15,
    ).stdout
    # 第一行是說明文字;開頭 * 代表停用的服務
    return [
        line for line in out.splitlines()[1:]
        if line.strip() and not line.startswith("*")
    ]


def _mac_set(enabled: bool, host: str = "", port: int = 0) -> None:
    errors = []
    for svc in _mac_services():
        if enabled:
            cmds = [
                ["networksetup", "-setwebproxy", svc, host, str(port)],
                ["networksetup", "-setsecurewebproxy", svc, host, str(port)],
            ]
        else:
            cmds = [
                ["networksetup", "-setwebproxystate", svc, "off"],
                ["networksetup", "-setsecurewebproxystate", svc, "off"],
            ]
        for cmd in cmds:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                errors.append(f"{svc}: {result.stderr.strip()}")
    if errors:
        raise OSError("; ".join(errors))
