---
name: lvke-url-audit-fetch-visual-chain
description: >
  Document the correct sequence for URL audit → fetch → visual capture → citation
  in reports. Use when verifying source quality, URL liveness, screenshot evidence,
  or citation traceability from public web sources.
---

# URL 审计 → 抓取 → 截图 → 引用 完整链路

## 目的

公开来源从"搜索结果"到"报告引用"的可核验链路有三个独立步骤，各自有明确边界。

## 三步链路

### 1. URL 审计（data_audit_urls）

**作用**：预检 URL 是否为可处理的公网目标，并按需检查可达性。

**两种模式**：
- `audit_mode="safety"`（默认）：只做本地 URL/公网目标检查，不联网，不返回响应码或 MIME
- `audit_mode="live"`：**仅检查可达性**，不采集正文，**不授予证据资格**

**输出**：不可变 `UrlAudit` 对象（含 `url_audit_id`、`basis`、`content_hash`）

**关键约束**：`audit_mode="live"` 的输出**不能**直接用作报告引用的证据。它只证明"此 URL 在某时刻可访问"，但没有正文快照。

---

### 2. 正文抓取与固化（data_fetch / data_collect / data_import_external_snapshot）

**作用**：获取页面正文并固化为不可变 `SourceSnapshot`，带 `content_hash` 和 `locator`。

**工具选择**：
- `data_fetch(urls, content_mode="readable")` — 直接抓取并固化，返回 `source_snapshot_id`
- `data_collect(discovery_set_id, selected_candidate_ids)` — 从 discovery set 中选定候选并批量抓取
- `data_import_external_snapshot(url, title, content, provider, provider_tool, ...)` — 将 Tavily 已提取的正文固化为 Lvke 快照

**输出**：`SourceSnapshot` 对象，包含：
- `source_snapshot_id`
- `url`
- `content`（Markdown 或 raw）
- `content_hash`（SHA256）
- `retrieved_at`
- `provider` / `provider_tool`

**关键约束**：只有 `SourceSnapshot` 才能进入 `EvidencePack` 并作为报告引用的正式来源。

---

### 3. 可视化捕获（data_capture_source_view）

**作用**：将已导入的 PNG/JPEG 截图绑定到 `SourceSnapshot`，记录 viewport、timestamp、page_title。

**前提**：
- 截图文件已通过 `source_import_content` 或 `source_upload_*` 导入为 `file_id`
- 对应的 `source_snapshot_id` 已存在（即步骤 2 已完成）

**输出**：`VisualSourceCapture` 对象，包含：
- `visual_capture_id`
- `source_snapshot_id`（关联正文快照）
- `image_file_id`（关联截图文件）
- `url`
- `viewport` (width, height, device_scale_factor)
- `captured_at`
- `image_content_hash`

**用途**：
- 报告附件：截图作为来源页面的可视化佐证
- 人工复核：当正文解析有歧义时，reviewer 可查看原始页面外观

---

## 正确使用顺序

### 场景 A：从搜索结果到正式引用（标准路径）

```
1. data_discover / tavily_search
   → 候选 URL 列表

2. data_audit_urls(urls, audit_mode="safety")
   → 过滤掉不可达、非公网、高风险 URL

3. data_fetch(通过审计的 URLs) 或 data_collect(...)
   → source_snapshot_id 列表

4. analysis_ingest(source_snapshot_ids)
   → 可查询内容

5. analysis_extract_candidates([{field, expected_unit, ...}])
   → 带 locator 的事实候选

6. analysis_build_evidence_pack(selected_candidate_ids, selected_source_ids)
   → evidence_pack_id

7. 报告引用 EvidencePack 中的 source_snapshot_id
   → 可追溯到 URL、content_hash、locator
```

### 场景 B：补充截图证据（可选）

```
（在标准路径步骤 3 之后）

4a. 用 Playwright / browser 插件截图，保存为 PNG

4b. source_import_content(
      original_filename="source-view.png",
      declared_mime="image/png",
      content_base64=截图的Base64内容,
      idempotency_key=唯一键
    )
    → file_id

4c. data_capture_source_view(
      source_snapshot_id=步骤3的ID,
      image_file_id=步骤4b的ID,
      url=原URL,
      viewport={width, height, ...},
      captured_at=截图时间,
      image_content_hash=SHA256
    )
    → visual_capture_id

5. 报告附录引用 visual_capture_id 作为"来源X的截图"
```

---

## 常见错误

### ❌ 错误 1：将 audit 结果当作证据

```python
# 错误写法
audit_result = data_audit_urls(["https://example.com"], audit_mode="live")
# audit_result 只有 url_audit_id，没有正文内容
evidence_pack = analysis_build_evidence_pack(..., selected_source_ids=[audit_result["url_audit_id"]])
# → 失败：url_audit_id 不是 source_snapshot_id
```

**正确做法**：audit 后必须 fetch。

---

### ❌ 错误 2：跳过 SourceSnapshot 直接截图

```python
# 错误写法
visual_id = data_capture_source_view(source_snapshot_id="missing", image_file_id="...")
# → 失败：source_snapshot_id 不存在
```

**正确做法**：先 fetch 固化正文快照，再绑定截图。

---

### ❌ 错误 3：认为 `audit_mode="live"` 已采集正文

```python
# 错误理解
audit = data_audit_urls(urls, audit_mode="live")
# 以为此时已有正文 → 错误
# audit_mode="live" 只验证可达性，不读取 body
```

**正确理解**：`live` 模式是轻量预检，正文采集必须调用 `data_fetch`。

---

## 集成验收检查点

在 Codex 真实对话中验证：

1. ✅ `data_audit_urls(audit_mode="safety")` → 通过的 URL 进入下一步
2. ✅ `data_fetch` 返回 `source_snapshot_id` 且 `content` 非空
3. ✅ `analysis_ingest` 能读取 snapshot 内容
4. ✅ `analysis_extract_candidates` 返回带 `locator` 的事实
5. ✅ `analysis_build_evidence_pack` 接受 `source_snapshot_id`（不接受 `url_audit_id`）
6. ✅ 报告引用中能回溯到 URL、content_hash、具体段落 locator
7. ✅（可选）`data_capture_source_view` 将截图绑定到已有 snapshot

---

## 与其他 Skill 的关系

- **lvke-source-acquisition** — 定义 Tavily 多查询、多发布主体来源门禁和 fetch 标准路径
- **lvke-evidence-analysis** — 定义 EvidencePack 的构建规则
- **lvke-report** — 定义报告引用格式和 citation 审计
- 本 Skill — 专门澄清 audit/fetch/visual 三步的边界与正确顺序

---

## 反模式

- 用 `audit_mode="live"` 的结果当正文快照
- 跳过 `data_fetch` 直接用搜索摘要写报告
- 截图没有关联 `source_snapshot_id`，成为孤立附件
- 报告引用指向 `url_audit_id` 而非 `source_snapshot_id`
