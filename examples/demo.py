"""An isolated fictional demo; never accesses real history."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from codex_history import sync_history, search_history


def main():
    with tempfile.TemporaryDirectory(prefix="history-demo-") as directory:
        root = Path(directory)
        home = root / "codex"
        (home / "sessions").mkdir(parents=True)
        source = home / "sessions" / "00000000-0000-4000-8000-000000000001.jsonl"
        records = [
            {"type": "event_msg", "payload": {"type": "user_message", "message": "演示项目如何加快缓存查询？联系 demo@example.invalid"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "channel": "final", "message": "结论：建立本地缓存索引，按变更增量更新。"}},
        ]
        source.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        output = root / "vault" / "来源" / "会话"
        sync_history(home, output, quiet=True)
        results = search_history(home, output, "缓存", limit=8, auto_sync=False)
        assert results
        for result in results:
            print(result["层级"], result["标题"])
        mirrors = list(output.rglob("*.md"))
        assert mirrors and "demo@example.invalid" not in mirrors[0].read_text(encoding="utf-8")
        print("虚构演示通过：可检索缓存结论，邮箱已遮盖。临时数据将在退出时清理。")


if __name__ == "__main__":
    main()
