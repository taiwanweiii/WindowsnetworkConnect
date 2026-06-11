"""設定檔讀寫 (config.json,放在專案根目錄)。"""

import json
from pathlib import Path

from .rules import Rule, RuleEngine

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULT_CONFIG = {
    "listen_host": "127.0.0.1",
    "listen_port": 8080,
    # 所有規則都沒命中時走的出口:
    # "default" = 系統預設路由;改成網卡名稱 = 預設全走那張卡
    "default_interface": "default",
    "rules": [
        # 範例:Copilot 相關網域走手機網路 (介面名稱請在 GUI 確認後修改)
        {
            "host": "*.githubcopilot.com",
            "port": 0,
            "process": "*",
            "interface": "default",
            "note": "Copilot API",
        },
        {
            "host": "copilot-proxy.githubusercontent.com",
            "port": 0,
            "process": "*",
            "interface": "default",
            "note": "Copilot 補全",
        },
        {
            "host": "api.github.com",
            "port": 0,
            "process": "*",
            "interface": "default",
            "note": "GitHub API (Copilot 登入/聊天)",
        },
    ],
}


def load(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    if not path.exists():
        save(DEFAULT_CONFIG, path)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    return json.loads(path.read_text(encoding="utf-8"))


def save(config: dict, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def engine_from_config(config: dict) -> RuleEngine:
    engine = RuleEngine.from_list(config.get("rules", []))
    engine.default_interface = config.get("default_interface", "default")
    return engine


def config_from_engine(config: dict, engine: RuleEngine) -> dict:
    config["rules"] = engine.to_list()
    config["default_interface"] = engine.default_interface
    return config
