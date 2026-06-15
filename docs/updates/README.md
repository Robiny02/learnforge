# docs/updates/ — 更新日志（HTML）

LearnForge 的系统演进记录。**每次更新生成一份独立 HTML**，由 `index.html` 汇总（最新在上）。
离线、零依赖：直接用浏览器打开 `index.html`（`file://` 即可），样式走相对路径 `style.css`。

## 目录结构

```
docs/updates/
├── README.md        ← 本文件（约定）
├── style.css        ← 共享样式（所有页面相对引用）
├── index.html       ← 落地页 / 索引（手工维护卡片列表）
├── _template.html   ← 新条目模板（复制它）
└── YYYY-MM-DD-NNNN-slug.html   ← 每次更新一份
```

## 命名约定

`YYYY-MM-DD-NNNN-slug.html`

- `YYYY-MM-DD`：更新日期。
- `NNNN`：四位自增序号（`0001`、`0002`…），全局唯一、永不复用，便于排序与互链。
- `slug`：短横线 kebab，概括主题（如 `phase1-core-contracts`）。

## 新增一条更新（步骤）

1. `cp _template.html 2026-MM-DD-NNNN-slug.html`，填写各 section。
2. 在 `index.html` 的 `<main class="entries">` **最上方**插入一张 `<a class="card">`（复制上一张改字段）。
3. 在文末更新 badge：`phase` / `type` / `risk` / `tests`（取值见下）。
4. 如有承接关系，互相加 `<a href>` 链接（上一条的“下一步” ↔ 新条目）。

## Badge 取值

| 维度 | 可选值 | CSS class |
|---|---|---|
| phase | `Phase 0`…`Phase 7`（对应重构计划） | `phase` |
| type | `feature` / `contracts` / `refactor` / `docs` / `fix` | `type` / `docs` |
| risk | `none` / `low` / `med` / `high` | `risk-low` / `risk-med` / `risk-high` |
| tests | `green` / `n/a` | `ok` |

## 一条好更新应包含

背景/动机 · 变更内容 · 涉及文件（新增/修改标注）· 测试（用例 + 结果）· 影响/风险/回滚 · 下一步。
保持与代码事实一致：测试数字、commit hash、文件路径都要真实可核对。

## 与其它文档的关系

- 总规划：[`../architecture/refactor-plan.md`](../architecture/refactor-plan.md)（8 阶段重构方案）。
- 本目录只记“每次改了什么”；设计取舍与边界分析留在架构文档，避免重复。
