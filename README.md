# Codex 历史知识库

将本机 Codex 历史组织为“知识 → 项目 → 来源会话”三层 Markdown 知识库，支持增量同步、关键词检索和待沉淀候选筛选。仅使用 Python 标准库，需要 Python 3.10 或更新版本。

## 安装

将本仓库下载到 Codex 技能目录，文件结构应为：

```text
~/.codex/skills/search-codex-history/SKILL.md
```

如果设置了 `CODEX_HOME`，则放到该目录下的 `skills/search-codex-history`。技能中的 PowerShell 示例使用默认安装路径；自定义安装时应替换脚本路径。

## 使用

在 Codex 中请求“使用 $search-codex-history 查找以前关于某个主题的结论”，或在本仓库目录运行：

```sh
python scripts/codex_history.py sync
python scripts/codex_history.py search "查询关键词" --json
python scripts/codex_history.py status --json
python scripts/codex_history.py candidates --limit 10 --json
```

原始会话默认读取 `~/.codex`，可通过 `CODEX_HOME` 或全局参数 `--codex-home` 配置。

默认来源输出目录是 `~/Documents/ChatGPT/知识库/来源/会话`。可用 `CODEX_HISTORY_KB_DIR` 或全局参数 `--output` 替换。此环境变量指向来源会话目录；建议保留末尾 `来源/会话`，项目索引和知识目录会位于知识库根目录下。

```sh
python scripts/codex_history.py --codex-home /path/to/codex --output /path/to/vault/来源/会话 sync
```

`search` 和 `candidates` 默认先同步，会写入本地知识库；`search --no-sync` 可跳过同步。原始会话保持只读。人工笔记应放在 `知识/` 或项目目录中非 `_自动索引` 的位置，自动索引会被重建。

## 隐私与发布范围

本仓库只包含技能说明、源码、界面元数据和空白知识模板，不包含真实会话、知识库、凭据、机器配置或 Python 缓存。源码中的说话人名称使用通用的“用户”和“助手”。

工具在本地运行，不主动上传数据。**生成的知识库不是匿名数据**：它包含对话正文、任务 ID、时间、项目名称及本机路径，也可能包含对话中出现的个人信息或密钥。请保存在本地；分享前另行审阅与脱敏。仓库忽略规则用于减少误提交，不能替代内容审查。

脚本会读取本机 Codex 的 JSONL 和项目元数据；这些内部格式变化可能影响兼容性。解析器选取用户与助手消息，不主动导出系统角色、开发者角色、工具调用及工具输出；旧格式回退会保留消息正文，不应视为通用敏感信息过滤器。
