# 統一插件格式與執行協定 v1

本規格將套件、能力契約與工作流程分開版本化。統一的是格式與呼叫方式，不要求所有能力使用同一種語言，也不讓一種套件只能提供一種功能。

## 套件

副檔名為 `.auren-plugin`，內容為 ZIP，根目錄直接放 `plugin.json`，不可再包一層同名資料夾。原始碼目錄與封裝檔使用相同相對路徑。封裝檔另含開發工具產生的 `integrity.json`，記錄所有其他檔案的 SHA-256。

```text
plugin.json
README.md
src/main.py                 # 執行入口；宣告式 Skill 可不含程式
schemas/input.json
schemas/output.json
tests/cases.json
workflows/main.json         # 有可執行 Skill 時附上
SKILL.md                    # Skill 的人類可讀說明
prompts/                    # 有提示詞時附上，須逐檔宣告
ui/                         # 有介面時附上
migrations/                 # 有資料格式升級時附上
integrity.json              # 僅封裝成品具有，來源目錄不能預置
```

來源目錄不放虛擬環境、node_modules、快取、私鑰、API 金鑰、使用者資料庫或 `.env`。執行依賴由宿主供應的執行環境與已安裝插件解析；v1 不自動執行 pip、npm 或安裝腳本。需額外二進位資產時附入套件並受完整性清單保護，不在執行時偷偷下載程式。

雜湊用來驗證完整性，不代表作者可信，也不是開發者簽章。第三方插件不因此必須取得產品開發者簽章；來源信任與高權限授權是另外的判斷。

## plugin.json

以 `schemas/plugin-v1.schema.json` 為機器可讀定義，不接受未知欄位。主要欄位：

| 欄位 | 意義 |
| --- | --- |
| `format_version` | 固定 1，描述套件格式 |
| `id` | 小寫反向網域識別碼，例如 `local.my-plugin`；不是可變的顯示名稱 |
| `version` | 三段整數版本，例如 `1.2.0`；破壞既有資料或行為須升主要版 |
| `host_api` | 固定 1；不同宿主 API 不暗中當成相容 |
| `name/description/author/license` | 供人及 Agent 閱讀、搜尋與判斷的資訊 |
| `runtime` | 執行種類、入口、支援平台、權限模式 |
| `runtime.transport` | `stdio` 或 `factory`；既有 stdio 程式入口可省略，服務工廠必須明列 `factory`；宣告式流程與介面仍由對應的 `runtime.kind` 處理 |
| `exports` | 本插件提供的能力；每個出口宣告契約及輸入／輸出 Schema |
| `requires` | 所需能力契約與是否必要；不綁定作者或廠商 |
| `dependencies` | 必須綁定特定套件時使用，v1 鎖定確切版本，不猜測版本範圍 |
| `permissions` | 需要的宿主能力、資源範圍與理由；宣告不是授權 |
| `prompts` | 提示詞路徑及用途；沒有就明確使用空陣列 |
| `state` | 自己的資料版本與逐版搬移／回滾程式路徑 |
| `documentation/tests` | 說明及契約案例檔；不可省略 |
| `workflows/config_schema/ui` | 有流程、設定或管理介面才宣告 |

同一個 `exports` 陣列可包含 `tool`、`provider`、`workflow`、`ui`、`adapter`。`id` 是套件內穩定出口名稱，`contract` 使用 `能力名稱/主要版號`，例如 `auren.memory.query/1`。替換提供者時契約必須相容，顯示名稱相同不算相容。

`exports[].methods` 可列出服務物件承諾提供的可呼叫方法名稱。宿主建構工廠後會檢查可呼叫性。只有另外設定 `facade=true` 的出口會成為 Runtime 相容方法路由；一般服務不設此旗標，避免各自的 `status` 等同名方法互相衝突。它不是逐方法的輸入輸出 Schema，也不是權限白名單，不能宣稱列出它就隔離了物件的其他屬性。一般 JSON 能力入口仍使用出口的輸入／輸出 Schema；沒有 `invoke` 的工廠服務不能透過 JSON 能力入口任意呼叫其他方法。

`exports[].name`、`description`、`features` 是選填的使用者功能介紹：分別是白話名稱（1–120 字元）、具體用途（1–1200 字元）、特色清單（1–12 項，每項 1–300 字元）。請描述實際效果、使用情境與必要條件，不要只寫 `service` 或契約代號，也不要承諾尚未實作的功能。管理頁將技術代號收在展開區；這些介紹不會加入模型提示詞或改變授權。未填的既有插件保持相容，使用插件本身的名稱與介紹；隨產品附帶的官方能力另有介面說明，不套用到同契約的第三方插件。

```json
{"name":"跨對話的長期記憶","description":"把對話與經歷保存在本機，重新開啟後仍可查找。","features":["可搜尋過往記憶","封存資料仍能找回"]}
```

`runtime.kind` 接受 `python/node/native/wasm/declarative/mcp/web`，這只是格式能表達的種類，不表示目前已有全部執行器。現有開發試跑工具只執行 Python；其他種類能做靜態驗證。

`runtime.access=brokered` 表示需要宿主代管權限的執行環境，不表示讀到清單後就自然具有隔離。宿主缺少合適執行器必須報未就緒，不能改成直接執行。`native` 表示高信任原生執行，必須另宣告 `host.native` 權限並取得使用者明確授權。

## 資料契約 APS1

JSON Schema 使用可由標準 2020-12 工具理解的有限子集，稱為 APS1。目前開發工具支持：

`type`、`properties`、`required`、`additionalProperties`、`items`、`const`、`enum`、`oneOf`、`minItems`、`maxItems`、`uniqueItems`、`minLength`、`maxLength`、`minimum`、`maximum`、`pattern`、`$defs`、同檔案 `#/$defs/...` 參照，以及說明用的 `$schema/$id/title/description`。

不支援的關鍵字必須拒絕，不忽略。只允許單一字串型別宣告，聯集用 `oneOf`。資料必須是有限數值的 JSON；不接受 NaN、Infinity 或重複欄位。正規表示式使用 Python 與常見 Schema 工具皆支援的簡單交集，不依賴特定引擎功能。

自訂能力應發布輸入輸出 Schema、行為說明、錯誤種類、相容規則與契約案例。取代別人的插件時直接沿用已發布契約，不從插件名稱猜輸入輸出。單靠兩份 Schema 都能解析不能證明語意相容，替代品還必須跑過該能力的契約案例。

有 JSON `invoke` 的出口，其案例是非空的 `[{"export":...,"input":...,"expected":...}]`。只提供原生 Service Protocol 的工廠改用下列服務宣告，方法必須與各出口的 `methods` 完全相符：

```json
{"format_version":1,"kind":"native-service","exports":{"service":["health","status"]},"integration_required":true}
```

這個宣告不算已執行測試，不轉成 `{}` 回傳 `{}` 的假案例。宿主建構時核對方法，功能本身由服務及整合測試驗證。`probe` 會拒絕把這類工廠當 stdio 執行。

## stdio 插件 RPC v1

以 UTF-8 的 JSON-RPC 2.0 訊息，一行一個 JSON，透過標準輸入／輸出傳遞。v1 不支持批次請求，也不假設一個程序可同時處理多個呼叫。標準輸出不印一般紀錄；標準錯誤不得輸出憑證或原始使用者內容。

固定生命週期為 `plugin.initialize → plugin.health／plugin.invoke → plugin.shutdown`。初始化協議版本不符、初始化前呼叫能力、無法解析輸入或不存在的出口，必須回傳結構化錯誤。每次請求識別碼由宿主建立，回應原樣保留。

```json
{"jsonrpc":"2.0","id":1,"method":"plugin.initialize","params":{"protocol_version":1}}
{"jsonrpc":"2.0","id":1,"result":{"protocol_version":1}}
{"jsonrpc":"2.0","id":2,"method":"plugin.invoke","params":{"export":"echo","input":{"text":"你好"},"context":{"agent_id":"a1","task_id":"t1","run_id":"r1","config":{}}}}
{"jsonrpc":"2.0","id":2,"result":{"text":"你好"}}
```

stdio 的 JSON `context` 只傳宿主已核定的識別碼、設定與資料參照，不交出 Runtime、資料庫連線、API 金鑰或服務內部物件。它與下節的同程序 `NativeContext` 是不同介面，不可混用。宿主不得相信插件在回應中自行宣告的身分、授權或完成收據。

宿主與平台服務之間的能力呼叫也以資料契約定義。原生插件若選擇直接使用作業系統，必須承認該行為不受一般能力仲介完整控制。MCP 插件由轉接器映射列舉與呼叫，不能把「已連上 MCP」當成完成任務或已取得權限。

每次公開 JSON 入口的輸入及輸出都由宿主驗證 Schema。錯誤使用 JSON-RPC `error={code,message,data?}`，不得偽裝為成功結果。stdio 程序的逾時、取消及資源清理由對應執行器處理；不能將此描述套用成同程序工廠具有獨立程序隔離或可被單獨強制終止的保證。

## factory 原生服務工廠 v1

工廠沿用同一個 `plugin.json`、能力出口、完整性清單及版本規則，只改變服務的建構方式。目前僅接受 `runtime.kind=python`、`runtime.access=native` 及 `runtime.transport=factory` 同時成立；不是所有 `native` 執行檔或其他語言都已支援工廠。

下列是清單欄位的摘錄，其餘必要欄位仍須依完整 Schema 提供：

```json
{
  "runtime": {
    "kind": "python",
    "entrypoint": "src/main.py",
    "platforms": ["windows-x64"],
    "access": "native",
    "transport": "factory"
  },
  "exports": [{
    "id": "service",
    "kind": "provider",
    "contract": "example.echo/1",
    "input_schema": "schemas/input.json",
    "output_schema": "schemas/output.json",
    "methods": ["invoke"]
  }],
  "permissions": [{
    "capability": "host.native",
    "resources": ["agent-data"],
    "reason": "明確授權的同程序服務建構，不是隔離程序。"
  }]
}
```

入口提供 `create(context: NativeContext, export_id: str)` 並回傳服務物件。`export_id` 是清單出口的 `id`，不是契約字串；多出口插件必須依它回傳正確實作。宿主以插件識別碼、版本、契約、套件完整性識別及設定內容區別服務實例，避免把不同版本或設定誤當同一物件。

```python
class EchoService:
    def invoke(self, arguments, context):
        return {"text": arguments["text"]}

    def close(self):
        pass  # 實際服務應釋放自己建立的資源。


def create(context, export_id):
    if export_id != "service":
        raise ValueError("未知能力出口")
    context.checkpoint()
    return EchoService()
```

公開 JSON 呼叫的工廠出口需提供 `invoke(arguments, context)`；一般 Service Protocol 的本地相容方法則依原契約提供，不強制假裝成 JSON-RPC。這兩種呼叫不可混淆：`methods` 列表不會自動將本地物件轉成可傳出程序的 JSON API。

### NativeContext

| 成員 | 目前行為與使用界線 |
| --- | --- |
| `data_root` | 目前 Agent 的資料根目錄，不是插件專屬隔離目錄；新插件應使用自己的資料子目錄，官方遷移服務可保留原有資料位置 |
| `options` | 宿主建構選項的字典副本；屬高信任本地資訊，不得整份轉交給 stdio、介面或紀錄 |
| `config` | 依本插件及固定的狀態快照讀取設定；不可自行假設它包含其他插件的資料 |
| `require(contract)` | 取得宿主明列的啟動服務，或依契約綁定的動態服務埠；所需能力應在清單宣告，不能猜測或直接匯入其他插件的內部實作 |
| `callback(name)` | 取得當前呼叫範圍或宿主提供的建構回呼；不存在就拒絕，回呼不是 JSON 欄位，不寫入設定或磁碟 |
| `checkpoint()` | 檢查目前宿主的取消、關閉及時間預算；長工作應定期呼叫，不代表宿主能中斷任意原生程式 |

`callback` 優先採用目前呼叫範圍的延續，再查宿主的固定回呼。只使用實際提供的名稱；不能臆測一個名稱就當成已存在能力，也不應把暫時的延續快取到下一個任務。

工廠在 **Auren 宿主同一程序內執行**，可以接觸 Python 執行環境與服務物件。`NativeContext` 是建構及相容契約，不是安全沙箱；命名空間、方法中繼資料與服務埠都不是作業系統隔離。惡意或失控工廠可能讀取宿主記憶體、阻塞或終止整個程序，不能宣稱能像獨立 worker 一樣單獨強制終止。新第三方版本必須取得與該完整性識別及權限一致的原生授權，未授權不得載入。

### 官方封裝與更新

官方附帶套件也具有 `plugin.json` 與 `integrity.json`，載入及公開 JSON 呼叫會核對封裝內容；不得直接編輯已發布檔案或在其中建立 bytecode 快取。工廠透過相對匯入組織自己的程式，不靠修改宿主的模組搜尋路徑讀取另一個插件。

目前一般安裝入口不接受以官方識別碼覆蓋附帶套件。改寫官方能力時，複製成另一個插件識別碼、保留要取代的能力契約、測試並封裝新版本，再切換綁定。第三方套件更新也建立新版本及新的完整性快照，不就地改正在執行的版本。資料版本不相容時須先處理搬移；不能僅切換程式版本就宣稱回滾成功。

stdio JSON-RPC 的格式與生命週期保持不變。工廠的 `create` 不會由 stdio 的 `plugin.initialize` 代替，開發工具的 stdio 試跑亦不會替你建立 `NativeContext`。

## Skill 流程 v1

`SKILL.md` 供人與 Agent 理解；`workflows/*.json` 才是核心執行的明確流程。Schema 為 `schemas/workflow-v1.schema.json`。

節點以 `call` 指定能力契約，不直接寫死插件或模型。宿主在任務開始時解析綁定，固定版本、設定及授權快照。

- `needs` 指定先行節點，禁止循環及未知節點。
- `required=true` 表示啟用該節點時不得跳過；有 `when` 時只在條件成立後啟用。
- `arguments` 每個欄位使用 `{"literal":值}` 或 `{"source":"input／nodes.節點.output","pointer":"JSON Pointer"}`。
- `when` 使用一個資料參照與 `equals` 值比較，不執行字串 eval。
- `result` 指定流程回傳資料參照。

資料只能參照已宣告的先行依賴。節點查無資料、失敗、被取消及條件不成立是不同狀態；不能一概塞空字串。消費不存在的必要資料時必須阻塞。v1 一份流程為有向無環圖；要重規劃或迭代，由工作流程提供者提出下一輪圖，宿主沿用同一任務預算、檢查點與取消狀態，不能無限遞迴。

必要步驟的執行收據由宿主產生並綁定任務、節點、插件版本、輸入／輸出參照及狀態。後續模型請求必須能追到被消費的資料。這能驗證插件執行與上下文注入，不保證外部模型的不可觀測內部推理方式。

## 安裝與版本生命週期

目前宿主提供：發現套件 → 靜態檢查 → 解析依賴及能力 → 顯示權限差異 → 取得授權 → 交易式安裝 → 綁定 Agent。草稿另有靜態檢查與明確同意後的試跑；一般人工匯入不會在未同意前執行任意安裝腳本。原生服務工廠與 Skill 需要服務整合驗收，不能把靜態宣告案例當成已執行。Agent 自造的舊能力沿用平台案例、品質閘門及審批後才轉換。

更新固定新版本；同一次執行快照固定能力綁定、版本與設定，下一次執行再建立快照。收據保留實際使用版本，不宣稱能跨程序重啟或外部實作函式庫升級重播任意原生副作用。必要插件失敗不暗中回到內建方法。移除先檢查依賴，既有快照保留原套件目錄與資料；資料清除另依產品規則處理。

核心的安裝器與恢復入口是啟動根，不讓插件取代自己正在執行的宿主程式。可替換的是宿主支援契約下的功能、策略、流程、介面及提供者；直接改宿主執行檔屬核心升級，不混同一般插件安裝。

## 草稿與管理介面

`GET /api/v1/plugin-packages` 取得目錄，`POST /plugin-packages/upload` 接受原始 `.auren-plugin`，`/install` 套用經確認的版本，`/{id}/control` 處理啟停、綁定、設定及回復。API 都保留本地登入、CSRF、產品啟用及 Agent 範圍。

`/{id}/file?path=...` 查閱套件文字；`/{id}/draft` 複製成指定識別碼及新版本。`/plugin-drafts/{draft}/write` 只寫副本，`validate` 不執行程式，`probe` 須另外明確同意原生執行，`stage` 封裝成待安裝版本。儲存未完成 JSON 不影響原版，但無法發布。寫入與試跑快照共用鎖，實際試跑再次檢查快照。

Agent 的 `plugin_manage` 動作使用同一宿主，修改要有任務授權。它傳來的 `confirmed` 或 `native_consent` 不算人類同意；原生安裝及需要人工確認的安裝會列入插件管理待確認清單。

基本安裝與 `brokered` 執行器皆不依賴虛擬機。Windows 使用隨包嵌入式 Python、AppContainer 與 Job Object，執行前以真實程序驗證隔離。原生插件沒有此隔離保證。必要提供者失敗時暫停該流程；啟動必要插件失敗時顯示本機修復頁，登入後可恢復官方綁定，保留設定備份及資料。


## 圖形化設定的共同入口

Agent 設定依已安裝套件產生入口，不需要額外登記導航。`config_schema` 是插件公開的設定契約，宿主以其 `title`、`description`、型別與選擇值產生表單；`ui` 可提供專用 HTML 介面。原始設定與技術識別碼不作為一般使用者的主要畫面。未宣告設定的舊套件仍有用途與啟停入口，不因此造出可調參數。

自訂 `ui` 從設定入口開啟時，可使用 RPC channel `auren-plugin-v1` 的 `config.read`（params 為空物件）及 `config.save`（params.values 為完整設定物件）；僅能處理目前插件自身的 `config_schema`。儲存經宿主使用者確認，沿用既有 control/configure 的登入、Agent、確認、資料驗證與稽核，不由 iframe 宣稱已授權。無設定 Schema、已停用或在一般功能頁開啟時，這些設定訊息不可使用。


## 插件內容頁與功能歸屬

功能入口統一位於 Agent 的「功能與插件」。每個已安裝插件都有「內容／設定／說明與管理」，不再把日記、任務等內容另外放在主側欄。安裝、啟停、能力綁定與資料範圍仍各自對應目前的 Agent。

提供可檢視資料或互動功能的插件，應在 `plugin.json` 宣告 `pages`：

```json
{
  "pages": [
    {"id": "notes", "name": "旅行筆記", "contract": "example.notes/1", "ui": "ui/notes.html"}
  ],
  "ui": "ui/settings.html"
}
```

- 每頁必須有唯一 `id`、白話 `name`，以及本插件在 `exports` 宣告的 `contract`。`ui` 是套件內 HTML 相對路徑；未提供時使用頂層 `ui`。不接受路徑逸出、遺失檔案或超過 1 MiB 的 HTML。
- 頂層 `ui` 仍作為設定介面。內容頁只開放已授權的能力呼叫；設定讀寫只在設定頁由宿主仲介，儲存需使用者確認。
- 只有目前 Agent 已啟用、且選為該契約提供者的頁面可使用。更換提供者後，不暗中退回舊插件。停用保留資料；移除後入口消失。
- `GET /api/v1/plugin-packages/{id}/ui?page={page_id}` 核對頁面存在、啟用及當前提供者；回傳 HTML 仍在不具同源身分的框架內顯示。
- 舊套件未宣告 `pages` 時，保留 `auren.ui.{page}/1` 介面出口及單一頂層 `ui` 的相容入口。沒有圖形介面的背景插件只展示用途與設定入口，不虛構資料面板。
- `renderer: "builtin"` 為隨產品附帶的官方內容畫面保留；前端同時核對 `origin: "bundled"`。第三方及官方插件副本應提供自己的 HTML，不因使用相同契約就取得官方畫面。

官方日記插件 `auren.official.diary` 提供 `auren.diary/1`，透過既有記憶 Service 讀寫 diary 層，保留原資料及壓縮。支援 list、search、append、correct、archive、restore、export；搜尋及匯出不混入其他記憶層。停用日記插件會關閉它的內容入口，不等於停止所有背景記憶寫入。


## 首次設定與準備狀態

需要事前設定的插件必須公開準備條件。宿主會在進入 Agent 時顯示準備清單，指向插件自己的設定畫面；完成後依實際狀態更新，不儲存「看過導覽即完成」的旗標。停用或未選用的提供者不列為目前 Agent 的必做設定。

- 有 `config_schema` 時，未符合必要欄位的設定自動列入引導；原本有效的設定不需重填。純可選設定且空物件符合契約時，不會被強制要求填寫。
- 可加上 `setup: {"instructions": "請先選擇資料位置。"}` 指定使用者說明；這時須有 `config_schema`，且至少儲存過一次有效設定才算完成。
- 高信任原生服務若需要依自身狀態判斷，可宣告 `setup: {"instructions": "請完成初始化。", "status_contract": "example.service/1"}`。該契約必須是本插件的 factory 出口，且 `methods` 列出 `setup_status`。服務的 `setup_status()` 必須回傳 `{"ready": true}` 或 `{"ready": false}`。非布林值、例外及無法確認的狀態都不算完成。
- `setup_status` 是各插件的狀態方法，不透過 Runtime 的全域 facade 曝光；避免多個插件使用同名方法時衝突。
- `setup.blocks_tasks` 可設為 `false`，用於語音轉寫等獨立手動功能。仍顯示初始化引導、仍須依真實狀態驗證，但只阻擋該功能，不阻塞無關任務；未填時維持 `true`。
- 狀態檢查只應讀取本地已保存的設定／初始化結果，不得因檢查而產生人格、付費、下載模型或未授權連線。模型連線「已設定」不代表已驗證遠端可用性，連線測試仍由使用者操作。

`GET /api/v1/plugin-preparation` 沿用產品啟用與目前 Agent 範圍，回傳 items、ready、completed、total。items 只有識別碼、套件名稱、公開設定說明、ready、failed 與 blocks_tasks，不傳設定值、API 金鑰或人格參數。整體 ready 只判斷會阻塞任務的項目；介面仍應呈現尚未完成的選填功能。
