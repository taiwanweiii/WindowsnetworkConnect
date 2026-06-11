"""分流規則引擎。

刻意不依賴 proxy 實作:之後若升級成 WinDivert 全攔截模式,
攔截層一樣呼叫 RuleEngine.decide() 來決定封包走哪張網卡。
"""

import fnmatch
from dataclasses import asdict, dataclass, field

# 特殊介面名稱:不綁定網卡,走系統預設路由
DEFAULT_INTERFACE = "default"


@dataclass
class Rule:
    host: str = "*"        # 目標主機樣式 (fnmatch),例如 *.githubcopilot.com
    port: int = 0          # 目標 port,0 = 任意
    process: str = "*"     # 來源程式名稱樣式,例如 Code.exe / node*.exe
    interface: str = DEFAULT_INTERFACE  # 要走的網路介面名稱
    note: str = ""         # 備註,僅顯示用

    def matches(self, host: str, port: int, process: str | None) -> bool:
        if self.port and port != self.port:
            return False
        if not fnmatch.fnmatch((host or "").lower(), self.host.lower()):
            return False
        if self.process not in ("", "*"):
            if not fnmatch.fnmatch((process or "").lower(), self.process.lower()):
                return False
        return True


class RuleEngine:
    """依序比對規則,回傳第一條命中的規則 (first-match wins)。

    default_interface:所有規則都沒命中時要走的出口。
    設成某張網卡名稱 = 「預設全部走那張卡,例外才寫規則」。
    """

    def __init__(
        self,
        rules: list[Rule] | None = None,
        default_interface: str = DEFAULT_INTERFACE,
    ):
        self.rules: list[Rule] = list(rules or [])
        self.default_interface = default_interface

    def decide(self, host: str, port: int, process: str | None = None) -> Rule | None:
        for rule in self.rules:
            if rule.matches(host, port, process):
                return rule
        return None

    def to_list(self) -> list[dict]:
        return [asdict(r) for r in self.rules]

    @classmethod
    def from_list(cls, data: list[dict]) -> "RuleEngine":
        return cls([Rule(**item) for item in data])
