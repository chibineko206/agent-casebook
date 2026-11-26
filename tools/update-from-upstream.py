#!/usr/bin/env python3
"""配布元（テンプレートの元リポジトリ）から、仕組みのファイルだけを取り込む。

テンプレートから作ったリポジトリは配布元と履歴がつながらないので、`git pull` は使えない。
代わりに配布元を一時ディレクトリへ浅く clone し、下の MANAGED に挙げたファイルだけを置き換える。

  python3 tools/update-from-upstream.py --dry-run     何が変わるかを表示するだけ
  python3 tools/update-from-upstream.py               取り込む（コミットはしない。差分を見てから自分でコミットする）
  python3 tools/update-from-upstream.py --ref v0.2.0  タグやブランチを指定する（既定は配布元の既定ブランチ）
  python3 tools/update-from-upstream.py --repo URL    配布元を指定する（初回に .claude/upstream.json へ記録される）

触らないもの: cases/、private/、INDEX.md、.claude/rules/、.claude/settings.local.json、
.claude/hooks/*.local.sh、およびその他の利用側のファイル。
ディレクトリ単位の対象（tools/ など）は配布元と同じ内容に揃えるので、利用側が足したファイルは
消える。利用側の追加は .claude/rules/ と session-start.local.sh に書くこと。
"""
from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_REPO = "https://github.com/chibineko206/agent-casebook.git"

# 配布元から取り込むもの。末尾が / のものはディレクトリ（配布元と同じ内容に揃える）
MANAGED = [
    "CLAUDE.md",
    ".claude/settings.json",
    ".claude/hooks/",
    ".claude/commands/",
    "tools/",
    "templates/",
    "docs/manual.html",
    "docs/chat-writeback-prompt.md",
]
# ディレクトリ単位の対象の中でも、利用側のものとして残す（上書きも削除もしない）
KEEP_LOCAL = ("*.local.sh", "__pycache__")
STATE_FILE = ".claude/upstream.json"


def find_repo() -> Path:
    p = Path(__file__).resolve().parent
    for d in [p, *p.parents]:
        if (d / "CLAUDE.md").is_file() and (d / ".claude").is_dir():
            return d
    sys.exit("リポジトリ（CLAUDE.md と .claude/ を持つディレクトリ）が見つからない")


def run(*cmd: str, cwd: Path | None = None) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"失敗: {' '.join(cmd)}\n{r.stderr.strip()}")
    return r.stdout.strip()


def is_local(path: Path) -> bool:
    return any(path.match(pat) or pat in path.parts for pat in KEEP_LOCAL)


def files_under(root: Path) -> set[str]:
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not is_local(p.relative_to(root))
    }


def plan(src: Path, dst: Path) -> list[tuple[str, str]]:
    """(動作, 相対パス) の一覧。動作は add / update / delete"""
    out: list[tuple[str, str]] = []
    for item in MANAGED:
        if item.endswith("/"):
            s, d = src / item, dst / item
            theirs = files_under(s) if s.is_dir() else set()
            mine = files_under(d) if d.is_dir() else set()
            for f in sorted(theirs):
                rel = item + f
                if f not in mine:
                    out.append(("add", rel))
                elif not filecmp.cmp(s / f, d / f, shallow=False):
                    out.append(("update", rel))
            for f in sorted(mine - theirs):
                out.append(("delete", item + f))
        else:
            s, d = src / item, dst / item
            if not s.is_file():
                continue
            if not d.is_file():
                out.append(("add", item))
            elif not filecmp.cmp(s, d, shallow=False):
                out.append(("update", item))
    return out


def apply(src: Path, dst: Path, changes: list[tuple[str, str]]) -> None:
    for action, rel in changes:
        if action == "delete":
            (dst / rel).unlink()
        else:
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, dst / rel)
    for item in MANAGED:  # 空になったディレクトリを片付ける
        if item.endswith("/") and (dst / item).is_dir():
            for d in sorted((dst / item).rglob("*"), reverse=True):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()


def main() -> None:
    ap = argparse.ArgumentParser(description="配布元から仕組みのファイルだけを取り込む")
    ap.add_argument("--repo", help="配布元の URL かパス（既定: 前回の記録、なければ公開リポジトリ）")
    ap.add_argument("--ref", help="取り込むタグかブランチ（既定: 配布元の既定ブランチ）")
    ap.add_argument("--dry-run", action="store_true", help="変更を表示するだけで何も書かない")
    ap.add_argument("--force", action="store_true", help="取り込み対象に未コミットの変更があっても進める")
    a = ap.parse_args()

    repo = find_repo()
    state_path = repo / STATE_FILE
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    url = a.repo or state.get("repo") or DEFAULT_REPO

    if not a.dry_run and not a.force and (repo / ".git").exists():
        dirty = run("git", "status", "--porcelain", "--", *[m.rstrip("/") for m in MANAGED], cwd=repo)
        if dirty:
            sys.exit("取り込み対象に未コミットの変更がある。先にコミットするか退避する（意図して捨てるなら --force）:\n" + dirty)

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "upstream"
        cmd = ["git", "clone", "--quiet", "--depth", "1"]
        if a.ref:
            cmd += ["--branch", a.ref]
        run(*cmd, url, str(work))
        sha = run("git", "rev-parse", "HEAD", cwd=work)
        changes = plan(work, repo)

        print(f"配布元: {url}（{a.ref or '既定ブランチ'} @ {sha[:7]}）")
        if state.get("commit"):
            print(f"前回の取り込み: {state['commit'][:7]}（{state.get('date', '日付なし')}）")
        if not changes:
            print("変更なし。仕組みのファイルは配布元と同じ。")
        for action, rel in changes:
            print(f"  {action:6} {rel}")

        if a.dry_run:
            print("（--dry-run: 何も書いていない）")
            return
        apply(work, repo, changes)

    import datetime
    state = {"repo": url, "commit": sha, "ref": a.ref or "", "date": datetime.date.today().isoformat()}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"取り込み完了（{len(changes)} 件）。git diff で確認してコミットする。例: git add -A && git commit -m 'casebook: 仕組みを {sha[:7]} に更新'")


if __name__ == "__main__":
    main()
