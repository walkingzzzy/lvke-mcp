---
name: lvke-frontend-workbench
description: >
  Frontend skill for the Lvke web workbench (web/ React 19 + Vite + TypeScript).
  Use when changing workbench pages, Finance 页, formal_delivery_ready UI, finance-v3,
  sources UI, SourceEvidenceWorkbench, keyui-api, publish/evidence/research tabs,
  high-trust actions, typecheck, npm workspace, Playwright e2e, 前端, 工作台,
  Workbench, or desktop shell integration. Trigger on web/**, apps/lvke-desktop/**.
---


# Lvke 前端工作台

## 产品 UI 原则

- **一个项目上下文走完主链**：资料 → 研究 → 财务 → 正文 → 发布。  
- 状态诚实：区分草稿 / 测算预览 / 已批准 / 正式工件；**匡算不得展示为已签章报批**。  
- 高信任动作：人工确认或业务门禁未满足时隐藏、禁用或说明。
- 长任务走统一 Job/SSE，避免静默假成功。

## 技术栈与入口

| 项 | 路径 |
|---|---|
| 路由 | `web/src/App.tsx` |
| 工作台页 | `web/src/pages/workbench/*` |
| 收购财务 UI | `web/src/features/finance-v3/`（已挂 FinancePage） |
| 资料证据 UI | `web/src/features/sources/`（已挂 EvidencePage） |
| 旧财务 API 客户端 | `web/src/lib/keyui-api.ts`（与 v3 并存，新功能优先 v3） |
| E2E | `web/e2e/`（目前薄，主链需扩展） |

包管理：只用**根** npm workspace + `package-lock.json`，不要在 `web/` 另起 lockfile。

## 实现工作流

1. **读现有模式** — 对照邻近 page 的 RemoteState / PageHeader / 权限处理。  
2. **API** — 认证 cookie/token 与后端错误码；幂等写带 Idempotency-Key（若后端要求）。  
3. **双轨展示** — 参考轨 / 修正轨 / 差异裁决分开展示；禁止单一「对齐甲方」按钮掩盖 bridge。  
4. **类型** — 避免随意 `any`；改契约同步类型。  
5. **验证**

```bash
npm --workspace web run typecheck
npm --workspace web run test
npm --workspace web run lint
```

## 视觉与文案

- 专业工具感：信息密度服务审核，不追求营销落地页。  
- 数字格式与单位（万元、%、年）与后端一致。  
- 空态/错误态给出下一步（补资料、去复核、完成确认）。
- 通用视觉方法论可叠加插件 `frontend-design`，但**业务状态语义以本 skill 为准**。

## 桌面壳

`apps/lvke-desktop`：薄 Electron，拉起 `hermes dashboard` 后 loadURL。改 UI 优先 web；壳侧重进程、安装与签名（生产方案 P4）。

## 反模式

- 前端信任 body 里的 actor/role  
- 把 formal 门失败当成功 toast  
- 新功能继续堆在未挂路由的 System/Env/Config 遗留页  
- 为绿而跳过 typecheck  
