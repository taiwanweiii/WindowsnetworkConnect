"""啟動入口。

  python main.py            # GUI 模式
  python main.py --nogui    # 無介面模式,讀 config.json 直接跑
"""

import argparse
import time

from netrouter import config as cfg
from netrouter.gui import run as run_gui
from netrouter.proxy import ProxyServer
from netrouter.rules import DEFAULT_INTERFACE


def run_headless() -> None:
    config = cfg.load()
    engine = cfg.engine_from_config(config)

    def log(event: dict) -> None:
        if event["ok"]:
            mark = "→" if event["interface"] != DEFAULT_INTERFACE else "·"
            print(f"{event['time']} {mark} [{event['process']}] "
                  f"{event['host']}:{event['port']} 走 {event['interface']}")
        else:
            print(f"{event['time']} ✗ {event['host']}:{event['port']} "
                  f"失敗: {event['error']}")

    proxy = ProxyServer(
        engine,
        host=config["listen_host"],
        port=config["listen_port"],
        on_event=log,
    )
    proxy.start()
    print(f"Proxy 監聽 {config['listen_host']}:{config['listen_port']} "
          f"(共 {len(engine.rules)} 條規則,Ctrl+C 停止)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        proxy.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP 網路分流器")
    parser.add_argument("--nogui", action="store_true", help="無 GUI 模式")
    args = parser.parse_args()
    if args.nogui:
        run_headless()
    else:
        run_gui()


if __name__ == "__main__":
    main()
