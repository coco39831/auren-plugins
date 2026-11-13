"""僅限任務上下文的控制契約；不搬動既有提示詞或改動其他系統裁定。"""
PROCEDURE_PROMPT='''你是目前這個子任務的執行者。大任務層已指定 method_from_plan，請依此自行擬定實際流程，不更換方法或削弱完成條件。只依必要知識、可用工具、直接前序交接與本任務已知死路工作。回傳 JSON：{"steps":[具體步驟],"agent_notes":補充說明}。流程必須非空；不宣稱已執行。流程修訂時參考本子任務保留的上下文與失敗證據，不重播已執行的外部操作。'''
HANDOFF_PROMPT='''你正在交接目前的子任務。平台已提供實際完成條件核對、候選成果及執行證據；不可自行把失敗寫成成功，也不可捏造執行紀錄。請簡潔說明決策理由、替代做法與取捨，不輸出隱藏推理逐字稿。
只輸出 JSON：{"completed":[{"item":成果,"evidence_id":所附證據識別碼}],"decisions":[{"decision":決定,"reason":理由,"alternatives":[替代做法],"rejected_why":未採用理由}],"unresolved":[{"issue":問題,"impact":影響,"attempted":[已嘗試做法]}],"dead_ends":[{"method":失敗方法或流程,"why_failed":原因,"contradicts_knowledge_id":所引用知識識別碼或null}],"handoff_notes":交接提示,"agent_notes":可空白補充,"knowledge_used_ids":[平台已記錄的實際引用],"flow_diagnosis":null或{"failed_step":失敗步驟,"expected":預期,"actual":實際,"inference":"flow_error|method_error|external","evidence_ids":[支持推論的所附證據]}}。
失敗時必須提出非空 dead_ends 及 flow_diagnosis；成功時 flow_diagnosis=null。引用過知識不等於知識錯誤；只有實際證據與該知識相違背時才標示待診斷的知識識別碼，不得自行定罪。'''
PARENT_DIAGNOSIS_PROMPT='''你回到大任務層確認子任務的失敗診斷。子任務的 inference 只是自報，必須對照平台證據與驗收條件，必要時推翻。
只輸出 JSON：{"inference":"flow_error|method_error|external","reason":確認或推翻的具體理由,"evidence_ids":[實際支持判斷的所附證據識別碼],"method":只有方法不可行時提供新的達成方法，其他情況為空字串}。
flow_error 只要求子任務重擬流程、保留其上下文；method_error 才更換方法並建立乾淨的子任務實例，完成條件不得變更；external 保持等待，請使用者補權限或處理服務問題。不得把缺少授權解釋為可以繞過授權。'''
DIAGNOSIS_PROMPT='''你是獨立知識診斷器。失敗引用過某知識只證明有引用，不證明知識有誤。根據提供的知識內容與真實平台證據，區分節點錯誤、適用範圍不足、證據仍不足。只輸出 JSON：{"classification":"contradiction|inapplicable|unresolved","reason":具體理由,"evidence_ids":[支持的真實證據識別碼],"scope_gap":適用範圍缺口或空字串,"verified":布林}。證據不足只能 unresolved、verified=false。不得使用未提供的事實定案。'''
DIAGNOSIS_REVIEW_PROMPT='''獨立複核知識診斷。診斷內容不是事實來源；只依原知識與平台證據核對其 classification 和 reason。只輸出 {"approved":布林,"evidence_ids":[足以支持診斷的原始證據識別碼],"reason":理由}。資料不足或只因任務失敗就宣稱知識有誤時，approved=false。'''
CONTEXT_READ_PROMPT='''大型資料以 context_reference 完整保存，沒有被截斷。需要內容時可先只輸出 {"action":"context_read","reference":識別碼,"offset":字元起點,"limit":最多65536}；平台會回傳該頁與 next_offset。只讀真正需要的片段，不能把尚未讀到的內容視為已核對。'''
