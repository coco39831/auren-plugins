# Auren 插件目錄

本倉庫保存 Auren 現有 30 個官方插件、統一格式套件及開發文件。本次快照：`2026-11-13.1`。暫不實作插件收費、訂單或付費資格判定。

## 下載及使用

下表可下載每個 `.auren-plugin` 套件；[catalog.json](catalog.json) 提供版本、依賴、權限、SHA-256 與下載網址，亦可下載整個儲存庫保存原始碼及套件。每個套件使用同一份來源快照封裝，校驗碼見 [SHA256SUMS](SHA256SUMS)。

這是現有官方插件的公開發布快照，不是完整 Auren 安裝檔，也不是已上線的插件商店。官方插件使用 Auren 提供的執行環境、服務契約及宿主能力，不能當成獨立 Python 程式執行。

現行客戶端的官方插件由安裝檔附帶；一般匯入入口不允許覆寫相同官方識別碼。本次沒有變更這項行為，也沒有讓客戶端自動從 GitHub 下載。使用者請在相符版本的 Auren「功能與插件」安裝既有的可選功能。

`catalog.json` 的 `builtin_pages` 表示介面仍由相符版本的客戶端提供，例如本地聊天室的介面；只有下載套件不能讓舊客戶端突然具備新介面。改寫插件時請使用自己的識別碼及相容契約，經測試後再選用。

## 現有插件

| 插件 | 用途類型 | 版本 | 下載 | 原始碼 |
| --- | --- | --- | --- | --- |
| 任務協作插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.application-1.0.0.auren-plugin) | [查看](plugins/official/auren.application) |
| 隔離執行插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.brokered-executor-1.0.0.auren-plugin) | [查看](plugins/official/auren.brokered-executor) |
| 功能開關與授權插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.components-1.0.0.auren-plugin) | [查看](plugins/official/auren.components) |
| Agent 設定插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.configuration-1.0.0.auren-plugin) | [查看](plugins/official/auren.configuration) |
| 日記插件 | 可選安裝 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.diary-1.0.0.auren-plugin) | [查看](plugins/official/auren.diary) |
| 情感表演插件 | 可選安裝 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.emotion-1.0.0.auren-plugin) | [查看](plugins/official/auren.emotion) |
| 任務規劃與驗收插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.flow-1.0.0.auren-plugin) | [查看](plugins/official/auren.flow) |
| 工具執行與資料搬移插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.infra-1.0.0.auren-plugin) | [查看](plugins/official/auren.infra) |
| 知識與工具庫插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.knowledge-1.0.0.auren-plugin) | [查看](plugins/official/auren.knowledge) |
| 知識學習插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.learning-1.0.0.auren-plugin) | [查看](plugins/official/auren.learning) |
| 產品授權插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.license-1.0.0.auren-plugin) | [查看](plugins/official/auren.license) |
| 長期記憶插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.memory-1.0.0.auren-plugin) | [查看](plugins/official/auren.memory) |
| 記憶回想插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.memory-context-1.0.0.auren-plugin) | [查看](plugins/official/auren.memory-context) |
| 回覆準備流程插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.model-pipeline-1.0.0.auren-plugin) | [查看](plugins/official/auren.model-pipeline) |
| 模型請求整理插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.model-request-1.0.0.auren-plugin) | [查看](plugins/official/auren.model-request) |
| 主要模型選用插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.model-route-1.0.0.auren-plugin) | [查看](plugins/official/auren.model-route) |
| 模型連線插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.models-1.0.0.auren-plugin) | [查看](plugins/official/auren.models) |
| 操作授權與約定插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.permission-1.0.0.auren-plugin) | [查看](plugins/official/auren.permission) |
| 人格塑造與成長插件 | 可選安裝 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.personality-1.0.0.auren-plugin) | [查看](plugins/official/auren.personality) |
| 工具製作與管理插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.plugins-1.0.0.auren-plugin) | [查看](plugins/official/auren.plugins) |
| 思考準備插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.reasoning-1.0.0.auren-plugin) | [查看](plugins/official/auren.reasoning) |
| 網路搜尋與漫遊插件 | 可選安裝 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.roaming-1.0.0.auren-plugin) | [查看](plugins/official/auren.roaming) |
| 人工事件追蹤插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.security-1.0.0.auren-plugin) | [查看](plugins/official/auren.security) |
| 自我改進插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.selfmod-1.0.0.auren-plugin) | [查看](plugins/official/auren.selfmod) |
| Agent 資料共享插件 | 可選安裝 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.sharing-1.0.0.auren-plugin) | [查看](plugins/official/auren.sharing) |
| 本地聊天室與社群插件 | 可選安裝 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.social-1.0.0.auren-plugin) | [查看](plugins/official/auren.social) |
| 語音轉文字插件 | 可選安裝 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.speech-1.0.0.auren-plugin) | [查看](plugins/official/auren.speech) |
| 子任務記憶與交接插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.task-context-1.0.0.auren-plugin) | [查看](plugins/official/auren.task-context) |
| 任務執行與驗收插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.task-workflow-1.0.0.auren-plugin) | [查看](plugins/official/auren.task-workflow) |
| 狀態面板插件 | 官方基礎功能 | 1.0.0 | [下載套件](https://raw.githubusercontent.com/coco39831/auren-plugins/main/packages/2026-11-13.1/auren.official.views-1.0.0.auren-plugin) | [查看](plugins/official/auren.views) |

## 開發及授權

- [統一插件格式](plugins/FORMAT.md)
- [插件開發守則](plugins/DEVELOPMENT.md)
- [開發工具](plugins/devkit.py) 與 [範本](plugins/templates)
- 原始碼公開不代表改成開源授權；各插件仍保留 plugin.json 的既有 license。本快照皆為 Proprietary，未擅自改授 MIT、Apache 或其他授權。
- 套件完整性校驗不能代替作者身分驗證或電腦操作授權。安裝、啟停、權限與每個 Agent 的選用仍由本機管理。

檢查自己的新插件可在本倉庫執行 `python plugins/devkit.py validate 你的插件目錄`；需自行準備支援的 Python。這是開發操作，不是一般用戶的安裝前置作業。

## 發布範圍

只收集通過檢查的官方套件與明列開發文件，不包含 Agent 記憶、對話、資料庫、產品金鑰、私鑰、API 金鑰、設定檔、虛擬環境、模型或執行紀錄。插件中明列的提示詞屬程式資產，隨插件公開。

之後更新需建立新的快照識別碼與下載路徑，不覆寫既有版本套件。本倉庫的快照不宣稱已在乾淨 Windows 或真實模型供應商環境完成所有功能驗收。
