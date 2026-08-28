# Lvke MCP 本地验证入口。
#
# 仓库没有远端 CI，所以"该跑哪些门禁"此前只存在于人的记忆里：
# scripts/validate_skill_tool_mapping.py 长期是孤儿脚本，没有测试也没有入口。
# 这份 Makefile 把门禁固化成可复述的命令，`make verify` 就是提交前的全套。
#
# 所有目标都在 conda 环境 lvke-mcp 内执行：base 环境的 pytest import 不到 src。

CONDA_RUN := conda run -n lvke-mcp --no-capture-output
PY := $(CONDA_RUN) python

.PHONY: help verify test skills plugin wheel clean-probe

help:
	@echo "make test    - 全量测试（数据根自动隔离到临时目录）"
	@echo "make skills  - 校验 Skill 指引与真实工具面一致，并检查双树同步"
	@echo "make plugin  - 重建 Codex 插件 skills 树（含严格门禁）"
	@echo "make wheel   - 构建 wheel 并列出随包分发的 runtime 契约文件"
	@echo "make verify  - 提交前全套：test + skills + plugin"

# tests/conftest.py 会在 LVKE_MCP_DATA_DIR 未设置时指向临时目录，
# 因此这里刻意不传该变量：真实数据根 ~/.lvke 不会被污染。
test:
	$(PY) -m pytest -q

skills:
	$(PY) scripts/validate_skill_tool_mapping.py --strict --check-plugin-sync

plugin:
	$(PY) scripts/build_codex_plugin.py

wheel:
	$(PY) -m pip wheel --no-deps -w dist/ .
	@$(PY) -c "import glob, zipfile; \
w = sorted(glob.glob('dist/lvke_mcp-*.whl'))[-1]; \
names = zipfile.ZipFile(w).namelist(); \
print('wheel:', w); \
[print('  ', n) for n in sorted(x for x in names if '/runtime/' in x and x.endswith('.json'))]"

verify: test skills plugin
	@echo "verify OK"

# 清掉探测/验收在真实数据根下留下的临时工作区（只删 audit- 前缀，不动其它）。
clean-probe:
	@rm -rf $(HOME)/.lvke/workspaces/audit-* 2>/dev/null || true
	@echo "cleaned audit-* probe workspaces"
