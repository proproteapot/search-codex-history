#!/usr/bin/env python3
"""将本机 Codex 历史同步为来源、项目、知识三层 Markdown 索引。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MANIFEST_NAME = ".codex-history-index.json"
MANIFEST_VERSION = 1
SESSION_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
SPACE_RE = re.compile(r"\s+")
DATA_URL_RE = re.compile(r"data:[^;\s]+;base64,[A-Za-z0-9+/=\r\n]{256,}")
MAX_MESSAGE_CHARS = 200_000
PRIVACY_VERSION = 2


def redact(text: str) -> str:
    """Best-effort filtering, not an anonymization guarantee."""
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)", "[已隐藏私钥]", text, flags=re.S)
    text = re.sub(r"\b(?:gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]{16,}", "[已隐藏令牌]", text)
    text = re.sub(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/-]+=*", r"\1 [已隐藏]", text)
    text = re.sub(r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)\b([\s\"']*[:=][\s\"']*)[^\s\"',;]+", r"\1\2[已隐藏]", text)
    text = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[已隐藏邮箱]", text)
    text = re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[已隐藏手机号]", text)
    text = re.sub(r"(?i)(?:[A-Z]:[\\/](?:Users|Documents and Settings)[\\/]|/(?:Users|home)/)[^\s/\\<>\"']+", "[用户目录]", text)
    return text


@dataclass
class Session:
    session_id: str
    title: str
    source_path: Path
    status: str
    cwd: str
    started_at: str
    updated_at: str
    messages: list[tuple[str, str, str]]
    project_id: str = ""
    project_name: str = ""
    project_path: str = ""


def default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def default_output_dir() -> Path:
    configured = os.environ.get("CODEX_HISTORY_KB_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Documents" / "ChatGPT" / "知识库" / "来源" / "会话"


def vault_dir_from_output(output_dir: Path) -> Path:
    """从来源目录反推知识库根目录，同时兼容自定义旧式输出目录。"""
    if output_dir.name == "会话" and output_dir.parent.name == "来源":
        return output_dir.parent.parent
    return output_dir.parent


def safe_filename(value: str, fallback: str = "未命名") -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip(" .")
    value = SPACE_RE.sub(" ", value)
    return (value[:80].rstrip() or fallback)


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_json_read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def yaml_string(value: Any) -> str:
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def clean_message(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for tag in ("environment_context", "INSTRUCTIONS", "system", "developer", "app-context", "permissions", "skills_instructions"):
        text = re.sub(rf"<{tag}\b[^>]*>.*?(?:</{tag}>|\Z)", "", text, flags=re.S | re.I)
    text = redact(text)
    text = DATA_URL_RE.sub("[已省略内嵌二进制数据]", text).strip()
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS].rstrip() + "\n\n[内容过长，已在索引镜像中截断]"
    return text


def infer_session_id(path: Path) -> str:
    match = SESSION_ID_RE.search(path.name)
    if match:
        return match.group(1).lower()
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:32]


def read_titles(codex_home: Path) -> dict[str, dict[str, str]]:
    titles: dict[str, dict[str, str]] = {}
    path = codex_home / "session_index.jsonl"
    if not path.exists():
        return titles
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                session_id = str(item.get("id", "")).lower()
                if session_id:
                    titles[session_id] = {
                        "title": str(item.get("thread_name", "")).strip(),
                        "updated_at": str(item.get("updated_at", "")).strip(),
                    }
    except OSError:
        pass
    return titles


def read_project_catalog(codex_home: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    state = safe_json_read(codex_home / ".codex-global-state.json", {})
    projects = state.get("local-projects", {}) if isinstance(state, dict) else {}
    assignments = state.get("thread-project-assignments", {}) if isinstance(state, dict) else {}
    if not isinstance(projects, dict):
        projects = {}
    if not isinstance(assignments, dict):
        assignments = {}
    return projects, assignments


def resolve_project(
    session_id: str,
    cwd: str,
    projects: dict[str, dict[str, Any]],
    assignments: dict[str, dict[str, Any]],
) -> tuple[str, str, str]:
    assignment = assignments.get(session_id, {})
    project_id = str(assignment.get("projectId", "")) if isinstance(assignment, dict) else ""
    project = projects.get(project_id, {})
    if isinstance(project, dict) and project:
        roots = project.get("rootPaths", [])
        root = str(roots[0]) if isinstance(roots, list) and roots else str(assignment.get("cwd", ""))
        return project_id, str(project.get("name", "")) or Path(root).name, root

    normalized_cwd = os.path.normcase(os.path.normpath(cwd)) if cwd else ""
    best: tuple[int, str, str, str] | None = None
    for candidate_id, candidate in projects.items():
        if not isinstance(candidate, dict):
            continue
        roots = candidate.get("rootPaths", [])
        if not isinstance(roots, list):
            continue
        for root_value in roots:
            root = str(root_value)
            normalized_root = os.path.normcase(os.path.normpath(root))
            if normalized_cwd == normalized_root or normalized_cwd.startswith(normalized_root + os.sep):
                choice = (len(normalized_root), str(candidate_id), str(candidate.get("name", "")), root)
                if best is None or choice[0] > best[0]:
                    best = choice
    if best:
        return best[1], best[2] or Path(best[3]).name, best[3]
    return "unclassified", "未归类", ""


def discover_files(codex_home: Path) -> list[tuple[Path, str]]:
    discovered: dict[str, tuple[Path, str]] = {}
    for folder_name, status in (("sessions", "活跃"), ("archived_sessions", "已归档")):
        root = codex_home / folder_name
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            session_id = infer_session_id(path)
            previous = discovered.get(session_id)
            if previous is None or path.stat().st_mtime_ns >= previous[0].stat().st_mtime_ns:
                discovered[session_id] = (path, status)
    return sorted(discovered.values(), key=lambda pair: pair[0].stat().st_mtime_ns, reverse=True)


def content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") not in {"input_text", "output_text"}:
            continue
        text = clean_message(item.get("text", ""))
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def first_line_title(messages: list[tuple[str, str, str]]) -> str:
    for role, text, _timestamp in messages:
        if role != "user":
            continue
        line = SPACE_RE.sub(" ", text).strip().lstrip("#>- ")
        if line:
            return line[:60] + ("…" if len(line) > 60 else "")
    return "未命名会话"


def parse_session(path: Path, status: str, title_info: dict[str, dict[str, str]]) -> Session:
    session_id = infer_session_id(path)
    filename_has_id = SESSION_ID_RE.search(path.name) is not None
    accepted_meta_id = False
    cwd = ""
    started_at = ""
    first_timestamp = ""
    messages: list[tuple[str, str, str]] = []
    fallback_messages: list[tuple[str, str, str]] = []

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # 正在写入的会话可能暂时留下不完整的最后一行。
                continue
            if not isinstance(record, dict):
                continue
            timestamp = str(record.get("timestamp", ""))
            if timestamp and not first_timestamp:
                first_timestamp = timestamp
            record_type = record.get("type")
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue

            if record_type == "session_meta":
                meta_id = payload.get("id") or payload.get("session_id")
                # 压缩或续接后的 JSONL 可能含有旧会话的 session_meta。
                # 文件名中的任务 ID 才是这份 transcript 的稳定主键。
                if meta_id and not filename_has_id and not accepted_meta_id:
                    session_id = str(meta_id).lower()
                    accepted_meta_id = True
                cwd = str(payload.get("cwd", ""))
                started_at = str(payload.get("timestamp", "")) or started_at
                continue

            if record_type == "event_msg":
                event_type = payload.get("type")
                if event_type == "user_message":
                    text = clean_message(payload.get("message", ""))
                    if text:
                        messages.append(("user", text, timestamp))
                elif event_type == "agent_message":
                    if payload.get("channel") not in {None, "final", "commentary"}:
                        continue
                    text = clean_message(payload.get("message") or payload.get("text", ""))
                    if text:
                        messages.append(("assistant", text, timestamp))
                continue

            # 兼容缺少 event_msg 的旧版会话；只有在最终没有事件消息时才采用。
            if record_type == "response_item" and payload.get("type") == "message":
                role = payload.get("role")
                if payload.get("recipient") not in {None, "all"}:
                    continue
                if role == "assistant" and payload.get("channel") not in {"final", "commentary"}:
                    continue
                if role in {"user", "assistant"}:
                    text = content_text(payload.get("content"))
                    if text:
                        fallback_messages.append((str(role), text, timestamp))

    if not messages:
        messages = fallback_messages

    deduplicated: list[tuple[str, str, str]] = []
    for message in messages:
        if deduplicated and message[:2] == deduplicated[-1][:2]:
            continue
        deduplicated.append(message)

    info = title_info.get(session_id, {})
    title = clean_message(info.get("title") or first_line_title(deduplicated)) or "未命名会话"
    updated_at = info.get("updated_at") or datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    ).astimezone().isoformat(timespec="seconds")
    started_at = started_at or first_timestamp or updated_at
    return Session(
        session_id=session_id,
        title=title,
        source_path=path,
        status=status,
        cwd=cwd,
        started_at=started_at,
        updated_at=updated_at,
        messages=deduplicated,
    )


def session_output_path(output_dir: Path, session: Session) -> Path:
    match = re.match(r"(\d{4})-(\d{2})", session.started_at)
    year, month = match.groups() if match else ("未知日期", "")
    folder = output_dir / year
    if month:
        folder /= month
    return folder / f"{session.session_id}.md"


def render_markdown(session: Session) -> str:
    project = session.project_name or "未归类"
    lines = [
        "---",
        "类型: \"Codex 历史会话\"",
        f"标题: {yaml_string(session.title)}",
        f"任务ID: {yaml_string(session.session_id)}",
        f"状态: {yaml_string(session.status)}",
        f"项目: {yaml_string(project)}",
        f"项目路径: {yaml_string(session.project_path)}",
        f"工作目录: {yaml_string(session.cwd)}",
        f"开始时间: {yaml_string(session.started_at)}",
        f"更新时间: {yaml_string(session.updated_at)}",
        f"消息数: {len(session.messages)}",
        "标签:",
        "  - codex",
        "  - 历史会话",
        "---",
        "",
        f"# {session.title}",
        "",
        f"> 任务 ID：`{session.session_id}`  ",
        f"> 状态：{session.status}  ",
        f"> 项目：{project}  ",
        f"> 工作目录：`{session.cwd or '未记录'}`  ",
        f"> 原始文件：`{session.source_path}`",
        "",
        "## 对话",
        "",
    ]
    for role, text, timestamp in session.messages:
        speaker = "用户" if role == "user" else "助手"
        lines.extend((f"### {speaker}", ""))
        if timestamp:
            lines.extend((f"*{timestamp}*", ""))
        lines.extend((text, ""))
    lines.extend(
        (
            "---",
            "",
            "> 此文件由 Codex 历史知识库自动生成；正文可能在同步时重写。请把人工笔记放在独立 Markdown 文件中。",
            "",
        )
    )
    return redact("\n".join(lines))


def load_manifest(output_dir: Path) -> dict[str, Any]:
    manifest = safe_json_read(output_dir / MANIFEST_NAME, {})
    if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION:
        return {"version": MANIFEST_VERSION, "items": {}}
    if not isinstance(manifest.get("items"), dict):
        manifest["items"] = {}
    return manifest


def save_manifest(output_dir: Path, manifest: dict[str, Any]) -> None:
    manifest["version"] = MANIFEST_VERSION
    manifest["generated_at"] = iso_now()
    atomic_write_text(
        output_dir / MANIFEST_NAME,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )


def read_frontmatter(path: Path) -> dict[str, str]:
    """读取本工具所需的少量单值 YAML 属性，不实现通用 YAML。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    result: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        key, raw = line.split(":", 1)
        raw = raw.strip()
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw.strip("'\"")
        result[key.strip()] = str(value)
    return result


def relative_markdown_link(label: str, target: Path, from_dir: Path) -> str:
    relative = Path(os.path.relpath(target, from_dir)).as_posix()
    label = label.replace("[", "［").replace("]", "］")
    return f"[{label}]({relative})"


def generate_project_indexes(output_dir: Path, manifest: dict[str, Any]) -> int:
    """按项目聚合来源会话，并关联人工沉淀的知识条目。"""
    vault_dir = vault_dir_from_output(output_dir)
    project_dir = vault_dir / "项目" / "_自动索引"
    knowledge_dir = vault_dir / "知识"
    project_dir.mkdir(parents=True, exist_ok=True)

    knowledge_items: list[tuple[Path, dict[str, str]]] = []
    if knowledge_dir.exists():
        for path in knowledge_dir.rglob("*.md"):
            knowledge_items.append((path, read_frontmatter(path)))

    groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for session_id, meta in manifest.get("items", {}).items():
        project_key = str(meta.get("project_id", "")).strip() or "unclassified"
        groups.setdefault(project_key, []).append((session_id, meta))

    overview_rows: list[tuple[str, int, Path]] = []
    expected_files: set[Path] = set()
    for project_key, sessions in groups.items():
        first_meta = sessions[0][1]
        project_name = str(first_meta.get("project_name", "")).strip() or "未归类"
        project_path = str(first_meta.get("project_path", "")).strip()
        digest = hashlib.sha256(project_key.encode("utf-8")).hexdigest()[:8]
        destination = project_dir / f"{safe_filename(project_name)}-{digest}.md"
        expected_files.add(destination.resolve())
        sessions.sort(key=lambda pair: str(pair[1].get("updated_at", "")), reverse=True)
        related_knowledge = [
            (path, meta)
            for path, meta in knowledge_items
            if meta.get("项目路径") == project_path
            or (not meta.get("项目路径") and meta.get("项目") == project_name)
        ]
        lines = [
            "---",
            "类型: \"项目索引\"",
            f"项目: {yaml_string(project_name)}",
            f"项目ID: {yaml_string(project_key)}",
            f"项目路径: {yaml_string(project_path)}",
            f"会话数: {len(sessions)}",
            f"知识条目数: {len(related_knowledge)}",
            "自动生成: true",
            "---",
            "",
            f"# {project_name}",
            "",
            "> 此页自动聚合项目知识和来源会话。会话只是证据，知识条目才是优先检索对象。",
            "",
            "## 已沉淀知识",
            "",
        ]
        if related_knowledge:
            for path, meta in sorted(related_knowledge, key=lambda pair: pair[1].get("更新时间", ""), reverse=True):
                title = meta.get("标题") or path.stem
                kind = meta.get("知识类型", "知识")
                lines.append(f"- {relative_markdown_link(title, path, destination.parent)} · {kind}")
        else:
            lines.append("- 暂无。需要从有明确结论或产物的会话中沉淀。")
        lines.extend(("", "## 来源会话", ""))
        for session_id, meta in sessions:
            source_path = Path(str(meta.get("output_path", "")))
            title = str(meta.get("title", "")) or session_id
            link = relative_markdown_link(title, source_path, destination.parent)
            lines.append(
                f"- {meta.get('updated_at', '')} · {link} · {meta.get('status', '')} · "
                f"{meta.get('message_count', 0)} 条消息"
            )
        lines.extend(("", "---", "", f"> 自动更新时间：{iso_now()}", ""))
        atomic_write_text(destination, redact("\n".join(lines)))
        overview_rows.append((project_name, len(sessions), destination))

    # 只清理本工具此前生成、但已不再对应任何项目的自动索引。
    for path in project_dir.glob("*.md"):
        if path.name == "项目总览.md":
            continue
        if path.resolve() not in expected_files and not path.is_symlink() and read_frontmatter(path).get("类型") == "项目索引" and read_frontmatter(path).get("自动生成") == "True":
            path.unlink()

    overview_path = project_dir / "项目总览.md"
    overview_lines = [
        "---",
        "类型: \"项目总览\"",
        f"项目数: {len(overview_rows)}",
        "自动生成: true",
        "---",
        "",
        "# 项目总览",
        "",
        "> 项目是知识库的主要入口；完整会话位于“来源/会话”。",
        "",
    ]
    for name, count, path in sorted(overview_rows, key=lambda row: (-row[1], row[0].casefold())):
        overview_lines.append(f"- {relative_markdown_link(name, path, overview_path.parent)} · {count} 个会话")
    overview_lines.append("")
    atomic_write_text(overview_path, redact("\n".join(overview_lines)))
    return len(overview_rows)


def sync_history(
    codex_home: Path,
    output_dir: Path,
    *,
    force: bool = False,
    limit: int | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    codex_home = codex_home.resolve()
    output_dir = output_dir.resolve()
    if not codex_home.is_dir() or not any((codex_home / name).is_dir() for name in ("sessions", "archived_sessions")):
        raise ValueError("原始会话目录不存在；拒绝同步或清理，请检查 --codex-home。")
    vault = vault_dir_from_output(output_dir).resolve()
    if vault == codex_home or codex_home in vault.parents or vault in codex_home.parents:
        raise ValueError("知识库与 Codex 原始目录不能重叠。")
    project_output = vault / "项目" / "_自动索引"
    if vault not in project_output.resolve().parents:
        raise ValueError("项目索引目录越界。")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(output_dir)
    previous_home = manifest.get("codex_home")
    if previous_home and previous_home != str(codex_home):
        raise ValueError("此知识库属于另一个 Codex 目录，请使用独立输出目录。")
    if manifest.get("privacy_version") != PRIVACY_VERSION:
        force = True
    items: dict[str, Any] = manifest["items"]
    titles = read_titles(codex_home)
    projects, assignments = read_project_catalog(codex_home)
    project_catalog_hash = hashlib.sha256(
        json.dumps([projects, assignments], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if manifest.get("project_catalog_hash") != project_catalog_hash:
        force = True
    files = discover_files(codex_home)
    if limit is not None:
        files = files[:limit]

    imported = 0
    skipped = 0
    failed: list[dict[str, str]] = []
    for path, status in files:
        inferred_id = infer_session_id(path)
        stat = path.stat()
        known = items.get(inferred_id, {})
        title = clean_message(titles.get(inferred_id, {}).get("title", ""))
        unchanged = (
            not force
            and known.get("privacy_version") == PRIVACY_VERSION
            and Path(str(known.get("output_path", ""))).is_file()
            and known.get("source_path") == str(path)
            and known.get("size") == stat.st_size
            and known.get("mtime_ns") == stat.st_mtime_ns
            and known.get("title", "") == title
            and known.get("status") == status
        )
        if unchanged:
            skipped += 1
            continue
        try:
            session = parse_session(path, status, titles)
            session.project_id, session.project_name, session.project_path = resolve_project(
                session.session_id, session.cwd, projects, assignments
            )
            destination = session_output_path(output_dir, session)
            if output_dir not in destination.resolve().parents:
                raise ValueError("输出路径越界")
            rendered = render_markdown(session)
            if not destination.exists() or destination.read_text(encoding="utf-8") != rendered:
                atomic_write_text(destination, rendered)
            previous_mirror = Path(str(known.get("output_path", "")))
            if previous_mirror != destination and previous_mirror.is_file() and not previous_mirror.is_symlink() and output_dir in previous_mirror.resolve().parents:
                previous_meta = read_frontmatter(previous_mirror)
                if previous_meta.get("任务ID") == session.session_id and previous_meta.get("类型") == "Codex 历史会话":
                    previous_mirror.unlink()
            if session.session_id != inferred_id:
                items.pop(inferred_id, None)
            items[session.session_id] = {
                "privacy_version": PRIVACY_VERSION,
                "title": session.title,
                "status": session.status,
                "source_path": str(path),
                "output_path": str(destination),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "started_at": session.started_at,
                "updated_at": session.updated_at,
                "message_count": len(session.messages),
                "project_id": session.project_id,
                "project_name": session.project_name,
                "project_path": session.project_path,
                "workspace_cwd": session.cwd,
            }
            imported += 1
        except (OSError, UnicodeError, ValueError) as exc:
            failed.append({"path": str(path), "error": str(exc)})

    removed = 0
    # Only a full successful scan may remove mirrors owned by this manifest.
    if limit is None and not failed:
        current_sources = {str(path.resolve()) for path, _ in files}
        for session_id, meta in list(items.items()):
            source = Path(str(meta.get("source_path", ""))).resolve()
            if str(source) in current_sources or source.exists():
                continue
            if not any((codex_home / folder) in source.parents for folder in ("sessions", "archived_sessions")):
                continue
            mirror = Path(str(meta.get("output_path", "")))
            resolved = mirror.resolve()
            if output_dir not in resolved.parents or mirror.stem != session_id or mirror.is_symlink():
                continue
            if mirror.exists():
                meta_on_disk = read_frontmatter(mirror)
                if meta_on_disk.get("任务ID") != session_id or meta_on_disk.get("类型") != "Codex 历史会话":
                    continue
                mirror.unlink()
            items.pop(session_id)
            removed += 1
    manifest["codex_home"] = str(codex_home)
    manifest["privacy_version"] = PRIVACY_VERSION
    manifest["project_catalog_hash"] = project_catalog_hash
    save_manifest(output_dir, manifest)
    project_count = generate_project_indexes(output_dir, manifest)
    result = {
        "发现会话": len(files),
        "新增或更新": imported,
        "未变化": skipped,
        "失败": len(failed),
        "已清理来源": removed,
        "项目索引": project_count,
        "输出目录": str(output_dir),
        "错误": failed,
    }
    if not quiet:
        print(
            f"同步完成：发现 {len(files)} 个会话，新增或更新 {imported} 个，"
            f"跳过 {skipped} 个，失败 {len(failed)} 个。"
        )
        print(f"知识库目录：{output_dir}")
        for item in failed[:10]:
            print(f"警告：{item['path']}：{item['error']}", file=sys.stderr)
    return result


def normalized_excerpt(text: str, terms: list[str], width: int = 220) -> str:
    plain = SPACE_RE.sub(" ", text).strip()
    lower = plain.casefold()
    positions = [lower.find(term) for term in terms if lower.find(term) >= 0]
    start = max(0, (min(positions) if positions else 0) - 70)
    excerpt = plain[start : start + width]
    if start:
        excerpt = "…" + excerpt
    if start + width < len(plain):
        excerpt += "…"
    return excerpt


def without_frontmatter(text: str) -> str:
    return re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL)


def markdown_title(path: Path, body: str, meta: dict[str, str]) -> str:
    if meta.get("标题"):
        return meta["标题"]
    match = re.search(r"(?m)^#\s+(.+?)\s*$", without_frontmatter(body))
    return match.group(1).strip() if match else path.stem


def search_history(
    codex_home: Path,
    output_dir: Path,
    query: str,
    *,
    limit: int,
    auto_sync: bool,
) -> list[dict[str, Any]]:
    if auto_sync:
        sync_history(codex_home, output_dir, quiet=True)
    manifest = load_manifest(output_dir)
    vault_dir = vault_dir_from_output(output_dir)
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return []
    results: list[dict[str, Any]] = []

    # 项目与知识是主要检索层，权重显著高于原始会话。
    for layer, root, layer_weight in (
        ("知识", vault_dir / "知识", 120),
        ("项目", vault_dir / "项目", 70),
    ):
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            try:
                body = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            meta = read_frontmatter(path)
            title = markdown_title(path, body, meta)
            haystack = body.casefold()
            title_lower = title.casefold()
            if not all(term in haystack or term in title_lower for term in terms):
                continue
            score = layer_weight + sum(haystack.count(term) for term in terms)
            score += sum(30 for term in terms if term in title_lower)
            results.append(
                {
                    "层级": layer,
                    "标题": title,
                    "知识类型": meta.get("知识类型", meta.get("类型", layer)),
                    "项目路径": meta.get("项目路径", ""),
                    "更新时间": meta.get("更新时间", ""),
                    "Markdown路径": str(path),
                    "相关度": score,
                    "摘录": normalized_excerpt(without_frontmatter(body), terms),
                }
            )

    # 来源会话只作为回溯与兜底。
    for session_id, meta in manifest.get("items", {}).items():
        path = Path(str(meta.get("output_path", "")))
        if not path.exists():
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        haystack = body.casefold()
        title = str(meta.get("title", ""))
        title_lower = title.casefold()
        if not all(term in haystack or term in title_lower for term in terms):
            continue
        score = sum(haystack.count(term) for term in terms)
        score += sum(25 for term in terms if term in title_lower)
        project_path = str(meta.get("project_path", ""))
        score += sum(8 for term in terms if term in project_path.casefold())
        excerpt_source = without_frontmatter(body)
        results.append(
            {
                "层级": "来源会话",
                "标题": title,
                "任务ID": session_id,
                "状态": meta.get("status", ""),
                "项目路径": project_path,
                "更新时间": meta.get("updated_at", ""),
                "Markdown路径": str(path),
                "相关度": score,
                "摘录": normalized_excerpt(excerpt_source, terms),
            }
        )
    layer_priority = {"知识": 3, "项目": 2, "来源会话": 1}
    results.sort(
        key=lambda item: (
            layer_priority.get(str(item.get("层级", "")), 0),
            item["相关度"],
            item.get("更新时间", ""),
        ),
        reverse=True,
    )
    return results[:limit]


def referenced_session_ids(vault_dir: Path) -> set[str]:
    referenced: set[str] = set()
    knowledge_dir = vault_dir / "知识"
    if not knowledge_dir.exists():
        return referenced
    for path in knowledge_dir.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        referenced.update(match.group(1).lower() for match in SESSION_ID_RE.finditer(text))
    return referenced


def candidate_sessions(output_dir: Path, *, limit: int) -> list[dict[str, Any]]:
    """给 Codex 返回待人工判断的候选，不自动把会话冒充为知识。"""
    manifest = load_manifest(output_dir)
    referenced = referenced_session_ids(vault_dir_from_output(output_dir))
    candidates: list[dict[str, Any]] = []
    useful_words = ("结论", "建议", "完成", "已生成", "已创建", "方案", "步骤", "原因", "修复", "验证")
    artifact_re = re.compile(r"\[[^\]]+\]\([^)]+\.(?:md|txt|py|js|ts|tsx|html|docx|pdf|pptx|xlsx)(?::\d+)?\)", re.I)
    generic_title_re = re.compile(r"^(?:未命名会话|\d+|The following is the Codex agent history)", re.I)

    for session_id, meta in manifest.get("items", {}).items():
        if session_id in referenced:
            continue
        message_count = int(meta.get("message_count", 0) or 0)
        if message_count < 4:
            continue
        path = Path(str(meta.get("output_path", "")))
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        title = str(meta.get("title", ""))
        reasons: list[str] = []
        score = min(message_count, 40)
        if artifact_re.search(body):
            score += 25
            reasons.append("可能包含产物")
        matched_words = [word for word in useful_words if word in body]
        if matched_words:
            score += min(len(matched_words) * 4, 24)
            reasons.append("包含结论或完成信号")
        if generic_title_re.search(title):
            score -= 20
            reasons.append("标题辨识度较低")
        if not reasons:
            reasons.append("会话较长，值得人工判断")
        candidates.append(
            {
                "标题": title,
                "任务ID": session_id,
                "项目路径": meta.get("project_path", ""),
                "更新时间": meta.get("updated_at", ""),
                "消息数": message_count,
                "候选分": score,
                "理由": reasons,
                "Markdown路径": str(path),
            }
        )
    candidates.sort(key=lambda item: (item["候选分"], item["更新时间"]), reverse=True)
    return candidates[:limit]


def status_report(codex_home: Path, output_dir: Path) -> dict[str, Any]:
    discovered = discover_files(codex_home)
    manifest = load_manifest(output_dir)
    items = manifest.get("items", {})
    active = sum(1 for _path, state in discovered if state == "活跃")
    archived = sum(1 for _path, state in discovered if state == "已归档")
    exported = sum(1 for item in items.values() if Path(str(item.get("output_path", ""))).exists())
    vault_dir = vault_dir_from_output(output_dir)
    knowledge_count = len(list((vault_dir / "知识").rglob("*.md"))) if (vault_dir / "知识").exists() else 0
    project_count = (
        len([path for path in (vault_dir / "项目" / "_自动索引").glob("*.md") if path.name != "项目总览.md"])
        if (vault_dir / "项目" / "_自动索引").exists()
        else 0
    )
    return {
        "原始活跃会话": active,
        "原始归档会话": archived,
        "来源会话Markdown": exported,
        "知识条目": knowledge_count,
        "项目索引页": project_count,
        "上次同步": manifest.get("generated_at", "尚未同步"),
        "知识库目录": str(vault_dir),
        "来源目录": str(output_dir),
        "原始目录": str(codex_home),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex 历史会话中文知识索引")
    parser.add_argument("--codex-home", type=Path, default=default_codex_home())
    parser.add_argument("--output", type=Path, default=default_output_dir())
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", aliases=["同步"], help="增量同步历史会话")
    sync_parser.add_argument("--force", action="store_true", help="强制重新生成")
    sync_parser.add_argument("--limit", type=int, help="仅处理最近的 N 个会话（测试用）")
    sync_parser.add_argument("--json", action="store_true", help="输出 JSON")

    search_parser = subparsers.add_parser("search", aliases=["搜索"], help="搜索历史会话")
    search_parser.add_argument("query", help="关键词或短语")
    search_parser.add_argument("--limit", type=int, default=8, help="最多返回多少条")
    search_parser.add_argument("--no-sync", action="store_true", help="搜索前不自动同步")
    search_parser.add_argument("--json", action="store_true", help="输出 JSON")

    status_parser = subparsers.add_parser("status", aliases=["状态"], help="查看收录状态")
    status_parser.add_argument("--json", action="store_true", help="输出 JSON")

    candidates_parser = subparsers.add_parser(
        "candidates", aliases=["候选"], help="列出值得人工沉淀的会话候选"
    )
    candidates_parser.add_argument("--limit", type=int, default=10, help="最多返回多少条")
    candidates_parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    codex_home = args.codex_home.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()

    if args.command in {"sync", "同步"}:
        result = sync_history(
            codex_home,
            output_dir,
            force=args.force,
            limit=args.limit,
            quiet=args.json,
        )
        if args.json:
            print_json(result)
        return 1 if result["失败"] else 0

    if args.command in {"search", "搜索"}:
        results = search_history(
            codex_home,
            output_dir,
            args.query,
            limit=max(1, args.limit),
            auto_sync=not args.no_sync,
        )
        if args.json:
            print_json({"查询": args.query, "结果数": len(results), "结果": results})
        elif results:
            for number, item in enumerate(results, start=1):
                print(f"{number}. [{item['层级']}] {item['标题']}")
                print(f"   项目：{item['项目路径'] or '未识别'}")
                print(f"   时间：{item.get('更新时间', '')}  状态：{item.get('状态', '')}")
                print(f"   路径：{item['Markdown路径']}")
                print(f"   摘录：{item['摘录']}")
        else:
            print("没有找到匹配的历史会话。请尝试更短的关键词、项目名或文件名。")
        return 0

    if args.command in {"candidates", "候选"}:
        sync_history(codex_home, output_dir, quiet=True)
        candidates = candidate_sessions(output_dir, limit=max(1, args.limit))
        if args.json:
            print_json({"候选数": len(candidates), "候选": candidates})
        elif candidates:
            for number, item in enumerate(candidates, start=1):
                print(f"{number}. {item['标题']} · {item['候选分']} 分")
                print(f"   理由：{'、'.join(item['理由'])}")
                print(f"   路径：{item['Markdown路径']}")
        else:
            print("暂无未沉淀的高价值候选。")
        return 0

    report = status_report(codex_home, output_dir)
    if args.json:
        print_json(report)
    else:
        for key, value in report.items():
            print(f"{key}：{value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
