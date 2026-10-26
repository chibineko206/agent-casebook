#!/usr/bin/env python3
"""/done の決定的な処理をまとめたスクリプト。Skill（.claude/commands/done.md）はこれを呼び、判断と文章だけを担う。

  python3 tools/casebook.py start <slug>            案件の特定、ヘッダ、前回の未決事項（問答用）、git の状態を出す
  python3 tools/casebook.py start                   引数なし: 案件一覧と git の状態だけ出す
  python3 tools/casebook.py new <slug> --title T --category C [--private] [--artifacts A] [--related R] [--periodic P]
                                                    新規案件のディレクトリと CASE.md ヘッダを作る
  python3 tools/casebook.py check <entry.md>         エントリの書式を検査する（追記はしない）
  python3 tools/casebook.py finish <slug> --entry <entry.md> [--state S] [--artifacts A] [--also PATH ...] [--no-commit] [--message M]
                                                    書式検査 → CASE.md 末尾に追記 → ヘッダ更新 → INDEX 更新 → ダッシュボード再生成 → コミット

エントリのファイルは `## YYYY-MM-DD [モード] 要約` で始まり、`### やったこと` `### 判断` `### 未決事項` の 3 節を持つ。
未決事項の各行は `- 決める: ` `- やる: ` `- 確かめる: ` のどれかで始めるか、`- なし` とする。
リポジトリは、このファイルから上に辿って CLAUDE.md と .claude/ を持つディレクトリとする（cwd に依存しない）。
"""
import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dashboard  # noqa: E402  同じディレクトリの dashboard.py（CASE.md の解析を共有する）

REPO = dashboard.REPO
HEADING_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})(?:〜[\d-]+)? \[(Code|チャット|Cowork|Code/Cowork|記録のみ)\] (.+)$")
SECTIONS = ("やったこと", "判断", "未決事項")
ITEM_RE = re.compile(r"^- (決める|やる|確かめる)[:：] ?\S")
NONE_RE = re.compile(r"^- なし")


def sh(args, check=True):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, check=check).stdout


def find_case(slug):
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", slug):
        sys.exit(f"slug の形式が不正: {slug!r}（英小文字・数字・ハイフン）")
    for area in ("cases", "private"):
        d = REPO / area / slug
        if d.is_dir():
            return area, d
    return None, None


def git_status_lines():
    return [l for l in sh(["git", "status", "--porcelain"]).splitlines() if l.strip()]


# ---------------------------------------------------------------- start

def cmd_start(a):
    lines = git_status_lines()
    if not a.slug:
        print("案件一覧:")
        for area in ("cases", "private"):
            base = REPO / area
            if base.is_dir():
                names = sorted(p.name for p in base.iterdir() if p.is_dir())
                print(f"  {area}/: " + " ".join(names))
        print(f"git: 未コミットの変更 {len(lines)} 件")
        return
    area, d = find_case(a.slug)
    if not d:
        print(f"案件 {a.slug} は無い（新規なら `casebook.py new {a.slug} --title ... --category ...`）")
        print(f"git: 未コミットの変更 {len(lines)} 件")
        return
    c = dashboard.parse_case(d / "CASE.md", area)
    print(f"案件: {area}/{a.slug}/CASE.md")
    print(f"題名: {c.title}")
    for k in ("分類", "状態", "成果物", "関連", "定期作業"):
        if k in c.fields:
            print(f"{k}: {c.fields[k]}")
    if c.latest:
        print(f"最新エントリ: ## {c.latest.heading}（{len(c.entries)} 件目）")
        items = c.open_items
        print(f"前回の未決事項 {len(items)} 件:" if items else "前回の未決事項: なし")
        today = datetime.date.today()
        for i, it in enumerate(items, 1):
            flags = []
            if it.waiting:
                flags.append("待ち")
            if it.deadline and it.deadline < today:
                flags.append("期限切れ")
            if it.kind == "旧書式":
                flags.append("旧書式")
            print(f"  {i}. [{it.kind}] {it.text}" + (f"  ← {', '.join(flags)}" if flags else ""))
    else:
        print("エントリ: まだ無い")
    mine = [l for l in lines if f"{area}/{a.slug}/" in l]
    print(f"git: 未コミットの変更 {len(lines)} 件（この案件 {len(mine)} 件）")
    print(sh(["git", "log", "--oneline", "-3"]).rstrip())


# ---------------------------------------------------------------- new

def cmd_new(a):
    area = "private" if a.private else "cases"
    d = REPO / area / a.slug
    if d.exists():
        sys.exit(f"既にある: {d}")
    d.mkdir(parents=True)
    lines = [f"# {a.title}", "", f"分類: {a.category}", "状態: 進行中",
             f"成果物: {a.artifacts or 'なし'}", f"関連: {a.related or 'なし'}"]
    if a.periodic:
        lines.append(f"定期作業: {a.periodic}")
    (d / "CASE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"作成: {area}/{a.slug}/CASE.md")


# ---------------------------------------------------------------- check

def check_entry(text):
    """エントリの書式を検査し、(エラー一覧, 警告一覧, 見出し情報) を返す。"""
    errors, warns = [], []
    lines = text.strip("\n").splitlines()
    if not lines:
        return ["エントリが空"], [], None
    m = HEADING_RE.match(lines[0])
    if not m:
        errors.append(f"1 行目が `## YYYY-MM-DD [Code] 要約` の形でない: {lines[0][:60]!r}")
        info = None
    else:
        info = {"date": m.group(1), "mode": m.group(2), "summary": m.group(3).strip()}
    present = [l[4:].strip() for l in lines if l.startswith("### ")]
    for s in SECTIONS:
        if s not in present:
            errors.append(f"`### {s}` の節が無い")
    if any(l.startswith("## ") for l in lines[1:]):
        errors.append("エントリの中に `## ` 見出しが複数ある（1 エントリ 1 見出し）")
    # 未決事項の節の中身
    try:
        i = next(k for k, l in enumerate(lines) if l.startswith("### 未決事項"))
        body = [l for l in lines[i + 1:] if l.strip() and not l.startswith("### ")]
        bullets = [l for l in body if l.startswith("- ")]
        if not bullets:
            errors.append("未決事項が空（無ければ `- なし` と書く）")
        for l in bullets:
            if not (ITEM_RE.match(l) or NONE_RE.match(l)):
                errors.append(f"未決事項の行が `- 決める: / やる: / 確かめる: / なし` で始まっていない: {l[:60]!r}")
            if re.search(r"継続|引き続き残る|前エントリ|前回の未決", l):
                warns.append(f"未決事項に前のエントリへの参照がある（全量を書き直す）: {l[:60]!r}")
        if len(bullets) > 10:
            warns.append(f"未決事項が {len(bullets)} 件ある。10 件を超えたら案件の切り方を疑う")
    except StopIteration:
        pass
    return errors, warns, info


def cmd_check(a):
    text = Path(a.entry).read_text(encoding="utf-8")
    errors, warns, info = check_entry(text)
    for e in errors:
        print("エラー:", e)
    for w in warns:
        print("注意:", w)
    if not errors:
        print(f"書式 OK: {info['date']} [{info['mode']}] {info['summary']}")
    return 1 if errors else 0


# ---------------------------------------------------------------- finish

def update_header(case_md, state=None, artifacts=None):
    s = case_md.read_text(encoding="utf-8")
    head, sep, rest = s.partition("\n---\n")
    if not sep:
        head, rest = s, ""
    def set_field(h, key, value):
        if re.search(rf"^{key}:.*$", h, re.M):
            return re.sub(rf"^{key}:.*$", f"{key}: {value}", h, count=1, flags=re.M)
        return h.rstrip("\n") + f"\n{key}: {value}\n"
    if state:
        head = set_field(head, "状態", state)
    if artifacts:
        head = set_field(head, "成果物", artifacts)
    case_md.write_text(head + (sep + rest if sep else ""), encoding="utf-8")


def update_index(area, slug, title_hint):
    index = REPO / ("INDEX.md" if area == "cases" else "private/INDEX.md")
    c = dashboard.parse_case(REPO / area / slug / "CASE.md", area)
    state = c.state_raw or "進行中"
    updated = c.last_date.isoformat() if c.last_date else datetime.date.today().isoformat()
    link = f"cases/{slug}/CASE.md" if area == "cases" else f"{slug}/CASE.md"
    row = f"| [{slug}]({link}) | {c.category} | {state} | {updated} |"
    if not index.is_file():
        index.write_text(f"# INDEX\n\n| 案件 | 分類 | 状態 | 更新 |\n|---|---|---|---|\n{row}\n", encoding="utf-8")
        return "作成"
    s = index.read_text(encoding="utf-8")
    pat = re.compile(rf"^\| \[{re.escape(slug)}\]\([^)]*\)[^\n]*$", re.M)
    if pat.search(s):
        s = pat.sub(lambda m: row, s, count=1)
        action = "更新"
    else:
        # 表の中で slug 順の位置に挿入する
        rows = [m for m in re.finditer(r"^\| \[([^\]]+)\]\([^)]*\)[^\n]*$", s, re.M)]
        after = None
        for m in rows:
            if m.group(1) < slug:
                after = m
        if after:
            s = s[:after.end()] + "\n" + row + s[after.end():]
        elif rows:
            s = s[:rows[0].start()] + row + "\n" + s[rows[0].start():]
        else:
            # 行がまだ無い表は、区切り行（|---|）の直後に入れる
            sep = re.search(r"^\|[-| ]+\|[ \t]*$", s, re.M)
            if sep:
                s = s[:sep.end()] + "\n" + row + s[sep.end():]
            else:
                s = s.rstrip("\n") + "\n" + row + "\n"
        action = "追加"
    index.write_text(s, encoding="utf-8")
    return action


def cmd_finish(a):
    area, d = find_case(a.slug)
    if not d:
        sys.exit(f"案件が無い: {a.slug}（先に `new` で作る）")
    text = Path(a.entry).read_text(encoding="utf-8")
    errors, warns, info = check_entry(text)
    for w in warns:
        print("注意:", w)
    if errors:
        for e in errors:
            print("エラー:", e)
        sys.exit("書式エラーがあるので追記しない")
    case_md = d / "CASE.md"
    s = case_md.read_text(encoding="utf-8")
    if not s.endswith("\n"):
        s += "\n"
    case_md.write_text(s + "\n---\n\n" + text.strip("\n") + "\n", encoding="utf-8")
    print(f"追記: {area}/{a.slug}/CASE.md ← ## {info['date']} [{info['mode']}] {info['summary']}")
    if a.state or a.artifacts:
        update_header(case_md, a.state, a.artifacts)
        print("ヘッダ更新:", " ".join(x for x in [f"状態={a.state}" if a.state else "", f"成果物={a.artifacts}" if a.artifacts else ""] if x))
    print(f"INDEX {update_index(area, a.slug, info['summary'])}: " + ("INDEX.md" if area == "cases" else "private/INDEX.md"))
    try:
        subprocess.run([sys.executable, str(HERE / "dashboard.py"), "--html"], cwd=REPO, check=True, capture_output=True)
        print("ダッシュボード再生成: .tmp/dashboard.html")
    except Exception as e:  # 失敗しても手順は止めない
        print("ダッシュボード再生成に失敗（続行）:", e)
    if a.no_commit:
        print("コミット: しない（--no-commit）")
        return
    if area == "private":
        print("コミット: 対象なし（private/ は gitignore 済み）")
        return
    paths = [f"cases/{a.slug}", "INDEX.md"] + list(a.also or [])
    sh(["git", "add", "--"] + paths)
    staged = sh(["git", "diff", "--cached", "--name-only"]).split()
    if not staged:
        print("コミット: 変更なし")
        return
    msg = a.message or f"{a.slug}: {info['summary']}"
    sh(["git", "commit", "-q", "-m", msg])
    print("コミット:", sh(["git", "log", "--oneline", "-1"]).strip())
    print("  対象:", " ".join(staged))


# ---------------------------------------------------------------- main

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start"); p.add_argument("slug", nargs="?")
    p = sub.add_parser("new"); p.add_argument("slug"); p.add_argument("--title", required=True); p.add_argument("--category", required=True)
    p.add_argument("--private", action="store_true"); p.add_argument("--artifacts"); p.add_argument("--related"); p.add_argument("--periodic")
    p = sub.add_parser("check"); p.add_argument("entry")
    p = sub.add_parser("finish"); p.add_argument("slug"); p.add_argument("--entry", required=True); p.add_argument("--state")
    p.add_argument("--artifacts"); p.add_argument("--also", nargs="*"); p.add_argument("--no-commit", action="store_true"); p.add_argument("--message")
    a = ap.parse_args(argv)
    return {"start": cmd_start, "new": cmd_new, "check": cmd_check, "finish": cmd_finish}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
