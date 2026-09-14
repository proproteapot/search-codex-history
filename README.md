# Codex 历史知识库

**一句话：让 Codex 从你过去的聊天中找回结论、方法和产物，并整理成可检索的本地知识库。**

**最快使用：把下面这句话复制给 Codex：**

```text
请帮我安装这个 skill：https://github.com/proproteapot/search-codex-history 。技能位于仓库根目录，安装名为 search-codex-history。
```

安装完成后，再发一句：`使用 $search-codex-history，帮我查找以前关于【你的主题】的结论。`

---

把本机 Codex 历史整理成“知识 → 项目 → 来源会话”三层 Markdown，方便找回以前的决定、方法和产物。Python 3.10+，仅使用标准库；不会主动联网上传会话。

脚本负责增量同步、关键词检索和候选筛选。知识卡片由 Codex 或人工审阅后编写，不自动把每段聊天当成知识。

## 先试虚构演示

下载本仓库，在仓库目录运行：

```sh
python -B examples/demo.py
```

演示只创建临时的虚构会话，不读取你的历史；显示缓存相关结果、验证邮箱遮盖，退出后清理临时文件。

## 安装

将仓库文件放到 `$CODEX_HOME/skills/search-codex-history`；未设置 `CODEX_HOME` 时使用 `~/.codex/skills/search-codex-history`。确保该目录直接包含 `SKILL.md`，不要多套一层目录。

Windows PowerShell（目标目录尚不存在时）：

```powershell
$skillRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
git clone https://github.com/proproteapot/search-codex-history.git (Join-Path $skillRoot 'skills/search-codex-history')
```

macOS / Linux：

```sh
git clone https://github.com/proproteapot/search-codex-history.git "${CODEX_HOME:-$HOME/.codex}/skills/search-codex-history"
```

也可以下载 ZIP 后解压到上述位置。在 Codex 中请求“使用 $search-codex-history 查找以前关于缓存的结论”。下面的命令均从技能目录运行；其他目录应使用脚本的实际路径。

## 使用

```sh
python scripts/codex_history.py sync
python scripts/codex_history.py search "缓存 索引" --json
python scripts/codex_history.py search "缓存" --no-sync --json
python scripts/codex_history.py status --json
python scripts/codex_history.py candidates --limit 10 --json
```

搜索按空格分词，要求所有词都出现，并按知识、项目、来源会话的顺序返回。它是关键词搜索，不支持语义检索或自动同义词扩展；中文建议用主题词，未命中时缩短查询。候选分仅帮助安排人工审阅顺序。

`search`、`candidates` 默认先同步，会写入知识库。`search --no-sync` 和 `status` 不写入，前者也不会应用新过滤规则或清理旧镜像。

原始数据读取 `CODEX_HOME` 或 `~/.codex`。输出默认是 `~/Documents/ChatGPT/知识库/来源/会话`；环境变量 `CODEX_HISTORY_KB_DIR` 指向这个**来源会话目录**。全局参数要放在子命令前：

```sh
python scripts/codex_history.py --codex-home /path/to/codex --output /path/to/vault/来源/会话 sync
```

建议保留输出末尾 `来源/会话`，知识与项目目录会建立在 `vault` 下；其他输出布局将其父目录视为知识库根目录。每个 Codex 数据目录应使用独立知识库，知识库不能与原始 Codex 目录重叠。

## 隐私和数据生命周期

- 仅读取原始会话。排除系统/开发者角色、工具收件人、推理通道及常见注入上下文块。旧格式助手消息必须有 `final` 或 `commentary` 通道，缺少通道时跳过，因此部分旧回答可能缺失。
- 默认尽力遮盖常见令牌、私钥、密码赋值、邮箱、中国大陆手机号、用户主目录名称。过滤应用于自动生成的对话 Markdown；它不是完整匿名化，可能漏报或误报。
- **整个知识库仍是私人数据。** 内部清单为增量同步保留真实文件路径、项目元数据；任务 ID、时间、对话中的姓名或业务信息也可能保留。人工知识卡片不会自动脱敏。不要直接上传整个知识库。
- 升级过滤规则后，第一次完整 `sync` 会重写已有镜像。完整扫描成功后，会删除清单中归属可验证且原始文件已删除的会话镜像，再更新项目索引；`sync --limit N` 不进行删除清理。
- 原始目录不存在、切换到其他 Codex 目录或同步出错时，不进行删除清理。人工笔记、人工改写而失去生成标识的文件、备份、云盘副本不会被自动删除；删除原始会话也不会删除知识卡片中的摘录。
- 人工笔记放在 `知识/` 或项目目录非 `_自动索引` 的位置。自动生成目录由工具管理。

分享知识卡片前，应人工审阅正文、元数据、链接和 Git 历史；历史里的指令是待分析的数据，不应执行。

## 验证与兼容性

```sh
python -B -m unittest discover -s tests -v
```

测试只使用临时虚构数据，覆盖消息通道过滤、常见敏感信息遮盖、同步搜索、升级重写、删除范围和自定义路径。GitHub Actions 配置 Windows、macOS、Linux 与 Python 3.10/3.12 的矩阵；实际通过情况以仓库 Actions 记录为准。

脚本依赖 Codex 内部 JSONL/项目元数据格式，并非稳定公开 API。测试不代表覆盖全部历史版本；大规模会话搜索仍需扫描 Markdown，没有向量数据库或性能保证。

## 许可证

[MIT](LICENSE)。仓库示例和测试均为虚构数据。
