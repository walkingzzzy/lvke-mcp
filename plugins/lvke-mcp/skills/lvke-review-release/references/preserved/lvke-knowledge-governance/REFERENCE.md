---
name: lvke-knowledge-governance
description: "Score report sections with deterministic rubrics and govern reviewed-first Lvke knowledge candidates. Use after an evidence-backed report revision or review improvement should become reusable knowledge, or when comparing rubric assessments before and after a revision."
---

# 知识治理

知识闭环是“评分 → 修订 → 复评 → 候选 → 质量决定 → 发布”，不是 Agent 自动改写正式 Skill 或记忆。

## 工作流

1. 用 `review_list_rubrics` 读取适用 rubric 版本。
2. 对不可变 revision/section 调用 `review_score_section`；不通过时由 Codex 走报告 propose/diff/apply 后重新评分。
3. 用 `review_compare_assessments` 比较同版 rubric 的维度变化和 blocker 变化。
4. 只对通过评分且证据完整的内容调用 `knowledge_submit_candidate`。
5. 调用 `knowledge_review_candidate` 记录 accepted、rejected 或 needs_revision 的内容质量决定与理由。
6. 只有 accepted 候选才能调用 `knowledge_publish_release`，固化 reviewed knowledge。

## 门禁

- 搜索摘要、受控假设、未通过 rubric 或缺 locator/hash 的内容不得发布。
- 内容质量决定不表示身份认证、角色授权或专业签审。
- rejected/request_changes 候选不得被运行时 Skill 当作正式知识。
- 不直接编辑 `MEMORY.md`、`USER.md` 或正式 Skill；结构化记忆是权威真源，旧 Markdown 仅为可失败镜像。
