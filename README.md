# Network Router — Windows TCP 分流器

讓不同的連線走不同的網路介面:例如 **VS Code Copilot 走手機網路、資料庫連線走有線網路**。

## 原理

```
VS Code (http.proxy = 127.0.0.1:8080)
        │
        ▼
┌──────────────────────────────┐
│  本程式 (本地 Proxy)          │
│  規則引擎:依 目標主機 /      │
│  Port / 來源程式 決定出口     │
└──────┬───────────────┬───────┘
       │ bind(手機網卡IP)│ bind(有線網卡IP)
       ▼               ▼
   手機網路          有線網路

資料庫連線(沒設 proxy)──────► 系統預設路由(有線)
```

Windows 上對外 socket 只要 `bind()` 到某張網卡的 IPv4,封包就會從那張網卡送出。
本程式是一個本地 proxy(同一個 port 同時支援 **HTTP CONNECT / HTTP / SOCKS5**),
每條連線進來時:

1. 反查來源程式名稱(TCP 連線表 → PID → 程式名)
2. 用規則引擎比對「目標主機 / Port / 程式名」
3. 命中規則 → 出口 socket 綁定該網卡 IP;沒命中 → 走系統預設路由

## 安裝與啟動

```bat
pip install -r requirements.txt
python main.py          :: GUI 模式
python main.py --nogui  :: 無介面模式
```

## 使用步驟(Copilot 走手機網路的範例)

1. 把手機接上電腦並開 **USB 網路共用**(或連手機熱點 Wi-Fi)
2. 啟動本程式,在「網路介面」清單確認手機網卡的名稱
   (USB 共用通常是「乙太網路 2」之類,看 IP 判斷,手機共用常是 `192.168.42.x` / `172.20.10.x`)
3. 新增規則,出口介面選手機網卡:
   | 目標主機 | 出口介面 |
   |---|---|
   | `*.githubcopilot.com` | 手機網卡 |
   | `copilot-proxy.githubusercontent.com` | 手機網卡 |
   | `api.github.com` | 手機網卡 |
   | `*.github.com` | 手機網卡(登入用,可選) |
4. 按「啟動 Proxy」
5. VS Code `settings.json` 加上:

   ```json
   {
     "http.proxy": "http://127.0.0.1:8080",
     "http.proxySupport": "on"
   }
   ```

   Copilot 會跟著 VS Code 的 proxy 設定走。
6. **資料庫連線不用做任何事** — DB client 不走 proxy,自然走預設路由(有線)。

連線記錄視窗會即時顯示每條連線走了哪張網卡,藍色代表有被分流。

## 規則說明

- 規則**由上而下**比對,第一條符合者生效;都沒中 → 走 `default`(系統預設路由)
- 三個條件都支援萬用字元 (`*`、`?`):
  - **主機**:`*.githubcopilot.com`
  - **Port**:`0` = 任意
  - **程式**:`Code.exe`、`node*.exe`(Copilot 的請求可能來自 VS Code 的 extension host)
- 設定存在 `config.json`,可直接手動編輯

## 注意事項

- **預設路由要是有線網路**:確認方式 `route print 0.0.0.0`,metric 較小者優先。
  若手機網卡搶走預設路由,到「介面卡選項 → IPv4 → 進階」把有線的介面 metric 設小一點。
- **DNS 解析走預設路由**:主機名稱解析仍由系統 DNS 處理(通常走有線),
  解析完之後的 TCP 連線才會走規則指定的網卡,功能上沒有影響。
- 只有「會吃 proxy 設定的程式」才會經過本程式(VS Code、瀏覽器、git、curl 等都支援);
  不吃 proxy 的程式走預設路由。
- macOS / Linux 上也能跑(開發測試用),綁網卡 IP 的行為相同。

## 架構(預留 WinDivert 全攔截擴充)

```
netrouter/
├── rules.py            # 規則引擎(獨立模組,不依賴 proxy)
├── interfaces.py       # 網卡列舉
├── process_lookup.py   # 來源 port → 程式名稱
├── proxy.py            # 本地 proxy(HTTP/HTTPS/SOCKS5)+ 出口綁定
├── config.py           # config.json 讀寫
└── gui.py              # tkinter 介面
```

之後若要升級成「所有 TCP 都強制經過」(程式無感、不用設 proxy),
加一個 `windivert.py` 模組:用 [pydivert](https://github.com/ffalcinelli/pydivert)
攔截 outbound TCP SYN → 同樣呼叫 `RuleEngine.decide()` 決定出口 →
改寫封包導向。需要系統管理員權限,`rules.py` / `interfaces.py` /
`process_lookup.py` 全部可以直接重用。
