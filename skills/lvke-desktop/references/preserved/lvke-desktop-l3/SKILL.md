---
name: lvke-desktop-l3
description: >
  Lvke single-machine desktop shell (apps/lvke-desktop) and L3 whole-product
  readiness. Use when changing electron main/backend/preload, lvke_entry,
  packaging, NSIS installer, code signing, fuses, or discussing 桌面, 安装包,
  整机生产级, L3, 签名. Backend green alone is not L3.
---

# 桌面壳与整机 L3

## 定位

`apps/lvke-desktop`：方案 B **薄 Electron 壳**。  
流程：起本地 `hermes dashboard` / `lvke_entry` → 等 `HERMES_DASHBOARD_READY port=` → 加载工作台 UI。

**不是**重写业务前端；业务在 `web/`。上游 `apps/desktop` 为 Hermes 通用壳，**勿与 lvke-desktop 混改**。

## 关键路径

| 路径 | 职责 |
|---|---|
| `apps/lvke-desktop/electron/main.cjs` | 生命周期、起后端、建窗 |
| `apps/lvke-desktop/electron/backend.cjs` | 解析就绪握手 |
| `apps/lvke-desktop/electron/preload.cjs` | 最小 `window.lvke` |
| `apps/lvke-desktop/packaging/lvke_entry.py` | 后端入口（规划 Nuitka） |
| `apps/lvke-desktop/README.md` | 进度与待办 |

数据目录（Windows 文档）：`%LOCALAPPDATA%\Lvke`；日志：`logs/desktop.log`。

## 进度语义（README）

- **P0**：dev 用仓库 `.venv` 起后端 + 加载 UI — 已有基础。  
- **P1+ 待办**：进程树级联杀、Nuitka 自包含、Electron Fuses、品牌化、NSIS、**代码签名**。

已有 `dist-installer` 产物 **≠** 已签名正式安装包。

## L2 vs L3（生产方案）

| 级别 | 含义 |
|---|---|
| **L2 后端生产级** | G1–G5/G7 等：API + 金标 + fail-closed |
| **L3 整机生产级** | L2 + 前端主链可用 + **桌面签名安装包/可重复安装验收** |

**后端金标过了但安装包未签名 / 无进程树清理 → 不得称整机生产级。**

## 开发与验收清单

```text
- [ ] web 已 build 到 hermes_cli/web_dist（或当前壳约定路径）
- [ ] npm run dev 能看到 READY 握手并加载 UI
- [ ] 杀壳后后端子进程不残留（P1 目标）
- [ ] 安装包版本号与变更说明可追溯
- [ ] 代码签名（组织证书）完成后再给业务方
- [ ] 冒烟：打开壳 → 登录/工作区 → 打开 Finance/Evidence 各一页
```

```bash
# 前端
npm --workspace web run build
# 桌面（在 apps/lvke-desktop）
npm run dev
```

## 反模式

- 在 electron 主进程复制业务财务逻辑  
- 用未签名安装包当正式交付物  
- L2 通过就对外说「整机可装」  
- 改 `apps/desktop` 当绿科产品壳  

联用：`lvke-frontend-workbench`、`lvke-local-verify`、`lvke-delivery-guardrails`。
