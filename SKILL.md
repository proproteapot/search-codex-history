---
name: search-codex-history
description: 同步、检索并沉淀本机 Codex 历史知识。用户提到“查历史”“以前聊过什么”“从知识库找”“回顾项目”“同步历史会话”“沉淀当前会话”或需要复用过去任务中的决策、方法、产物、经验时使用。
---

# Codex 历史知识库

采用三层结构：知识是主要检索对象，项目是组织入口，会话仅作为来源证据。始终只读 Codex 原始 JSONL。

## 检索

运行：

```powershell
python "$env:USERPROFILE\.codex\skills\search-codex-history\scripts\codex_history.py" search "用户的查询" --json
```

先使用“知识”结果，再使用“项目”结果；仅在前两层不足时打开“来源会话”。回答时引用实际读取的 Markdown 路径和来源任务 ID。若无结果，用项目名、文件名或更短关键词重试一次。

## 同步

运行：

```powershell
python "$env:USERPROFILE\.codex\skills\search-codex-history\scripts\codex_history.py" sync
```

同步会增量更新 `来源/会话`，并重建 `项目/_自动索引`。它不会自动创建知识条目。

查看状态：

```powershell
python "$env:USERPROFILE\.codex\skills\search-codex-history\scripts\codex_history.py" status --json
```

## 沉淀知识

用户要求沉淀当前会话或整理历史时：

1. 先同步。批量整理时运行 `candidates --limit 10 --json` 获取候选。
2. 打开候选会话，判断是否形成可复用的“决策、方法、产物、经验”之一。没有明确价值时不要创建知识条目。
3. 搜索已有知识，优先更新同一主题；不要按会话机械创建新文件。
4. 按 `assets/knowledge-card-template.md` 写入 `知识/<知识类型>/`，提炼结论而非复制聊天全文。
5. 添加所有来源任务 ID 和会话链接；区分已验证事实、选择理由与尚未解决事项。
6. 再运行一次同步，使项目索引关联新知识。

一个会话允许产生 0 到多条知识，多场会话也允许合并到同一条知识。

## 边界

- 不修改、移动或删除 `~/.codex/sessions`、`archived_sessions` 或内部数据库。
- 不导出系统提示、开发者指令、推理、工具调用或工具输出。
- 不把候选分当作知识质量判断；它只决定人工审阅顺序。
- 自动生成的来源会话和项目索引会被重写；人工内容只放在 `知识/` 或非 `_自动索引` 的项目笔记中。
