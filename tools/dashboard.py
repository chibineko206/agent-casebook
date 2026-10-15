#!/usr/bin/env python3
"""agent-casebook の案件ダッシュボード。

cases/*/CASE.md と private/*/CASE.md を解析し、次の 2 つを出す。

- `--text`: SessionStart hook が表示する「進行中の案件」の行（標準出力）
- `--html [PATH]`: 単一ファイルのダッシュボード HTML（既定 .tmp/dashboard.html）

CASE.md の書式は変えない。読むのはヘッダの `分類:` `状態:` `定期作業:` 行、
`## YYYY-MM-DD [モード] 要約` の見出し、最新エントリの `### 未決事項` だけ。
INDEX.md との整合チェック、CASE.md 本文の検索、CASE.md をページ内で読むビューア、
Claude Code のセッション一覧（~/.claude/projects の記録と、hook が書く .tmp/sessions/ の実行中登録から）も載せる。
成果物の .md は量が多い（10 MB 超）ので埋め込まず、案件ディレクトリのファイル一覧から file:// で開く。

使い方:
  python3 tools/dashboard.py --text
  python3 tools/dashboard.py --html [--open]
  python3 tools/dashboard.py --html PATH
"""
import html
import json
import os
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path



def find_repo(start):
    """CLAUDE.md と .claude/ を持つ最も近い親ディレクトリをリポジトリとみなす（置き場所に依存しない）。"""
    for d in (start, *start.parents):
        if (d / "CLAUDE.md").is_file() and (d / ".claude").is_dir():
            return d
    return start.parents[1]


REPO = find_repo(Path(__file__).resolve().parent)
MACDOWN = any(Path(p).exists() for p in ("/Applications/MacDown 3000.app", "/Applications/MacDown.app"))
STALE_DAYS = 14
RECENT_DAYS = 30
DEFAULT_HTML = REPO / ".tmp" / "dashboard.html"
STATES = ("進行中", "保留", "完了")
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
SESSIONS_CACHE = REPO / ".tmp" / "sessions-cache.json"
SESSIONS_REGISTRY = REPO / ".tmp" / "sessions"
TS_RE = re.compile(r'"timestamp":"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})')
DONE_RE = re.compile(r"<command-name>/(done|writeback)</command-name>")  # 古い記録は command-args が無い
ARGS_RE = re.compile(r"<command-args>([a-z0-9-]+)</command-args>")
KINDS = ("決める", "やる", "確かめる")

ENTRY_RE = re.compile(r"^## (.+)$")
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
MONTH_RE = re.compile(r"^(\d{4}-\d{2})")
MODE_RE = re.compile(r"\[([^\]]+)\]")
KIND_RE = re.compile(r"^(決める|やる|確かめる)[:：]\s*(.*)$")
DEADLINE_RE = re.compile(r"期限\s*(\d{1,2})/(\d{1,2})|期限\s*(\d{4}-\d{2}-\d{2})")


@dataclass
class Item:
    text: str
    kind: str  # 決める / やる / 確かめる / 旧書式
    waiting: bool
    deadline: date | None


@dataclass
class Entry:
    heading: str
    date: date | None
    date_text: str
    mode: str
    summary: str
    line_no: int
    sections: dict = field(default_factory=dict)


@dataclass
class Case:
    slug: str
    area: str  # cases / private
    path: Path
    title: str
    fields: dict
    state_raw: str
    state: str
    entries: list
    text: str

    @property
    def private(self):
        return self.area == "private"

    @property
    def category(self):
        return self.fields.get("分類", "") or "未分類"

    @property
    def last_date(self):
        dates = [e.date for e in self.entries if e.date]
        return max(dates) if dates else None

    @property
    def latest(self):
        return self.entries[-1] if self.entries else None

    @property
    def open_items(self):
        if not self.latest:
            return []
        return parse_items(self.latest.sections.get("未決事項", []))


# ---------------------------------------------------------------- parsing

def normalize_state(raw):
    for s in STATES:
        if raw.startswith(s):
            return s
    return raw.split("（")[0].strip() or "不明"


def parse_items(lines):
    """`### 未決事項` の本文から箇条書きを取り出す。継続行は前の項目に連結する。"""
    items = []
    for line in lines:
        if re.match(r"^\s*[-*]\s+", line):
            items.append(re.sub(r"^\s*[-*]\s+", "", line).rstrip())
        elif line.strip() and items and line.startswith((" ", "\t")):
            items[-1] += " " + line.strip()
    out = []
    for t in items:
        plain = re.sub(r"\*\*", "", t)
        if re.match(r"^(なし|記録にない)", plain):
            continue
        m = KIND_RE.match(plain)
        kind, body = (m.group(1), m.group(2)) if m else ("旧書式", plain)
        dm = DEADLINE_RE.search(plain)
        deadline = None
        if dm:
            try:
                if dm.group(3):
                    deadline = date.fromisoformat(dm.group(3))
                else:
                    deadline = date(date.today().year, int(dm.group(1)), int(dm.group(2)))
            except ValueError:
                deadline = None
        out.append(Item(text=body, kind=kind, waiting="待ち" in plain, deadline=deadline))
    return out


def parse_case(path, area):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = next((l[2:].strip() for l in lines if l.startswith("# ")), path.parent.name)
    fields = {}
    for l in lines[1:]:
        if l.startswith("## ") or l.strip() == "---":
            break
        m = re.match(r"^([^\s:：#\-\[][^:：]{0,20})[:：]\s*(.*)$", l)
        if m:
            fields.setdefault(m.group(1).strip(), m.group(2).strip())
    entries = []
    cur = None
    for i, l in enumerate(lines, 1):
        m = ENTRY_RE.match(l)
        if m:
            h = m.group(1).strip()
            dm = DATE_RE.match(h)
            d = None
            if dm:
                try:
                    d = date.fromisoformat(dm.group(1))
                except ValueError:
                    d = None
            mm = MONTH_RE.match(h)
            date_text = dm.group(1) if dm else (mm.group(1) if mm else h.split(" ")[0])
            md = MODE_RE.search(h)
            mode = md.group(1) if md else ""
            summary = h[md.end():].strip() if md else h
            cur = Entry(heading=h, date=d, date_text=date_text, mode=mode, summary=summary, line_no=i)
            entries.append(cur)
            sec = None
            continue
        if cur is None:
            continue
        if l.startswith("### "):
            sec = re.sub(r"[（(].*$", "", l[4:]).strip()
            cur.sections.setdefault(sec, [])
            continue
        if sec:
            cur.sections[sec].append(l)
    state_raw = fields.get("状態", "")
    return Case(
        slug=path.parent.name, area=area, path=path, title=title, fields=fields,
        state_raw=state_raw, state=normalize_state(state_raw), entries=entries, text=text,
    )


def load_cases():
    cases = []
    missing = []  # CASE.md の無い案件ディレクトリ
    for area in ("cases", "private"):
        base = REPO / area
        if not base.is_dir():
            continue
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            f = d / "CASE.md"
            if f.is_file():
                cases.append(parse_case(f, area))
            else:
                missing.append(f"{area}/{d.name}")
    return cases, missing


def load_index(path):
    rows = {}
    if not path.is_file():
        return rows
    for l in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*\[([^\]]+)\]\([^)]*\)[^|]*\|\s*([^|]*)\|\s*([^|]*)\|\s*([^|]*)\|", l)
        if m:
            rows[m.group(1).strip()] = {
                "category": m.group(2).strip(), "state": m.group(3).strip(), "updated": m.group(4).strip(),
            }
    return rows


# ---------------------------------------------------------------- sessions

def scan_session(path):
    """1 本の記録（JSONL）から一覧に要る項目だけを拾う。大きい行は json.loads せず正規表現で済ませる。"""
    info = {"title": None, "first": None, "cwd": None, "branch": None, "turns": 0,
            "closed": False, "done_slug": None, "first_ts": None, "last_ts": None}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = TS_RE.search(line)
            if m:
                info["first_ts"] = info["first_ts"] or m.group(1)
                info["last_ts"] = m.group(1)
            if '"type":"ai-title"' in line:
                try:
                    info["title"] = json.loads(line).get("aiTitle") or info["title"]
                except ValueError:
                    pass
            elif '"type":"user"' in line:
                if DONE_RE.search(line):
                    info["closed"] = True
                    am = ARGS_RE.search(line)
                    if am:
                        info["done_slug"] = am.group(1)
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("isMeta") or d.get("isSidechain"):
                    continue
                c = (d.get("message") or {}).get("content")
                if isinstance(c, str):
                    info["turns"] += 1
                    if info["first"] is None:
                        info["first"] = c.strip().replace("\n", " ")[:80]
                        info["cwd"] = d.get("cwd")
                        info["branch"] = d.get("gitBranch")
    return info


def load_sessions(cases):
    """~/.claude/projects/*/<id>.jsonl を読み、更新時刻とサイズが同じものはキャッシュを使う。"""
    cache = {}
    if SESSIONS_CACHE.is_file():
        try:
            cache = json.loads(SESSIONS_CACHE.read_text(encoding="utf-8"))
        except ValueError:
            cache = {}
    running = {}
    if SESSIONS_REGISTRY.is_dir():
        for f in SESSIONS_REGISTRY.glob("*.json"):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            pid = r.get("pid")
            alive = False
            if pid:
                try:
                    os.kill(int(pid), 0)
                    alive = True
                except (OSError, ValueError):
                    alive = False
            if alive:
                running[r.get("session_id")] = r
            else:
                f.unlink(missing_ok=True)  # 落ちたセッションの登録は捨てる
    slugs = sorted((c.slug for c in cases), key=len, reverse=True)
    out, new_cache = [], {}
    if not CLAUDE_PROJECTS.is_dir():
        return out
    for path in CLAUDE_PROJECTS.glob("*/*.jsonl"):
        st = path.stat()
        key = str(path)
        ent = cache.get(key)
        if not ent or ent.get("mtime") != st.st_mtime or ent.get("size") != st.st_size:
            ent = {"mtime": st.st_mtime, "size": st.st_size, "info": scan_session(path)}
        new_cache[key] = ent
        info = ent["info"]
        if not info.get("turns"):
            continue
        sid = path.stem
        slug = info.get("done_slug")
        if not slug:
            text = f"{info.get('title') or ''} {info.get('first') or ''}"
            slug = next((x for x in slugs if x in text), None)
        status = "実行中" if sid in running else ("締め済み" if info.get("closed") else "未締め")
        out.append({
            "id": sid, "path": str(path), "status": status, "title": info.get("title") or info.get("first") or "(題名なし)",
            "first": info.get("first") or "", "cwd": info.get("cwd") or "", "branch": info.get("branch") or "",
            "turns": info.get("turns", 0), "slug": slug, "slug_known": bool(slug and slug in slugs),
            "last_ts": info.get("last_ts") or "", "first_ts": info.get("first_ts") or "",
            "started": (running.get(sid) or {}).get("started", ""),
        })
    try:
        SESSIONS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SESSIONS_CACHE.write_text(json.dumps(new_cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    out.sort(key=lambda x: (x["status"] != "実行中", x["last_ts"]), reverse=False)
    out.sort(key=lambda x: x["last_ts"], reverse=True)
    out.sort(key=lambda x: x["status"] != "実行中")
    return out


# ---------------------------------------------------------------- checks

def git_dirty_count():
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True, check=True).stdout
        return len([l for l in out.splitlines() if l.strip()])
    except Exception:
        return None


def consistency(cases, missing):
    problems = []
    for area, idx_path in (("cases", REPO / "INDEX.md"), ("private", REPO / "private" / "INDEX.md")):
        idx = load_index(idx_path)
        label = "INDEX.md" if area == "cases" else "private/INDEX.md"
        for c in (x for x in cases if x.area == area):
            row = idx.get(c.slug)
            if not row:
                problems.append(f"{label} に {c.slug} の行がない")
                continue
            if normalize_state(row["state"]) != c.state:
                problems.append(f"{c.slug}: 状態が {label}「{row['state']}」と CASE.md「{c.state_raw}」で違う")
            if c.last_date and row["updated"] and row["updated"] != c.last_date.isoformat():
                problems.append(f"{c.slug}: 更新日が {label}「{row['updated']}」と最新エントリ「{c.last_date}」で違う")
        known = {c.slug for c in cases if c.area == area}
        for slug in idx:
            if slug not in known:
                problems.append(f"{label} の {slug} に対応する CASE.md がない")
    for m in missing:
        problems.append(f"{m}/ に CASE.md がない")
    return problems


# ---------------------------------------------------------------- text (hook)

def text_rows(cases, today):
    rows = []
    for c in cases:
        if c.state != "進行中":
            continue
        area = " (private)" if c.private else ""
        last = c.last_date.isoformat() if c.last_date else "未記録"
        age = ""
        if c.last_date:
            days = (today - c.last_date).days
            if days >= STALE_DAYS:
                age = f"  ← {days} 日更新なし"
        rows.append(f"    {c.slug}{area}  [{c.category}]  {last}{age}")
    return "\n".join(rows)


# ---------------------------------------------------------------- html

def esc(s):
    return html.escape(str(s), quote=True)


def md_inline(s):
    """箇条書き 1 行分の最小限の Markdown（コード・太字・リンク）を HTML にする。"""
    s = esc(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", s)
    return s


def file_url(p):
    return "file://" + urllib.parse.quote(str(p))


def macdown_url(p):
    """MacDown 3000 の URL スキーム（x-macdown://open?url=<file URL>）。ブラウザから直接開ける。"""
    return "x-macdown://open?url=" + urllib.parse.quote(file_url(p), safe="")


def render_html(cases, missing, today):
    dirty = git_dirty_count()
    pending = (REPO / ".claude" / "pending")
    pending_text = pending.read_text(encoding="utf-8").strip() if pending.is_file() else ""
    problems = consistency(cases, missing)
    by_date = sorted(cases, key=lambda c: (c.last_date or date.min), reverse=True)

    stale = [c for c in by_date if c.state == "進行中" and c.last_date and (today - c.last_date).days >= STALE_DAYS]
    overdue = [(c, it) for c in cases for it in c.open_items if it.deadline and it.deadline < today]
    counts = {s: sum(1 for c in cases if c.state == s) for s in STATES}
    open_total = sum(len(c.open_items) for c in cases)

    # --- 注意
    notes = []
    if dirty:
        notes.append(f"未コミットの変更が {dirty} 件あります")
    if pending_text:
        notes.append(f"前回 /done していません: {esc(pending_text)}")
    for c in stale:
        notes.append(f"<a href=\"#view={esc(c.slug)}\" class=\"view\" data-slug=\"{esc(c.slug)}\">{esc(c.slug)}</a> は {(today - c.last_date).days} 日更新がありません")
    for c, it in overdue:
        notes.append(f"<a href=\"#view={esc(c.slug)}\" class=\"view\" data-slug=\"{esc(c.slug)}\">{esc(c.slug)}</a> の期限切れ: {md_inline(it.text)}")
    for p in problems:
        notes.append(f"整合: {esc(p)}")
    notes_html = "".join(f"<li>{n}</li>" for n in notes) or "<li class=\"muted\">なし</li>"

    # --- 案件一覧
    rows = []
    for c in by_date:
        days = (today - c.last_date).days if c.last_date else None
        age = "" if days is None else f"{days} 日"
        age_cls = " stale" if days is not None and days >= STALE_DAYS and c.state == "進行中" else ""
        n_open = len(c.open_items)
        periodic = c.fields.get("定期作業", "")
        rows.append(
            f"<tr data-state=\"{esc(c.state)}\" data-text=\"{esc((c.slug + ' ' + c.title + ' ' + c.category).lower())}\">"
            f"<td><a id=\"case-{esc(c.slug)}\" href=\"#view={esc(c.slug)}\" class=\"view\" data-slug=\"{esc(c.slug)}\">{esc(c.slug)}</a>"
            f"{' <span class=\"tag private\">private</span>' if c.private else ''}"
            f"<div class=\"muted small\">{esc(c.title)}</div></td>"
            f"<td>{esc(c.category)}</td>"
            f"<td><span class=\"state s-{esc(c.state)}\">{esc(c.state)}</span>"
            f"{'<div class=\"muted small\">' + esc(c.state_raw) + '</div>' if c.state_raw != c.state else ''}</td>"
            f"<td class=\"nowrap\">{esc(c.last_date or '未記録')}<div class=\"muted small{age_cls}\">{age}</div></td>"
            f"<td class=\"num\">{n_open or ''}</td>"
            f"<td class=\"small\">{md_inline(periodic) if periodic else ''}</td>"
            f"<td class=\"actions\"><a class=\"btn\" href=\"{esc(macdown_url(c.path))}\" title=\"MacDown で開く\"{'' if MACDOWN else ' style=\"display:none\"'}>MacDown</a>"
            f"<a class=\"btn\" href=\"casebook://case/{esc(c.slug)}?action=continue\" title=\"Terminal で claude を起動し、CASE.md を読ませて続きを始める\">Code</a>"
            f"<a class=\"btn\" href=\"casebook://case/{esc(c.slug)}?action=terminal\" title=\"Terminal を案件ディレクトリで開く\">Term</a>"
            f"<button type=\"button\" data-copy=\"{esc(str(c.path))}\" title=\"CASE.md の絶対パスをコピー\">パス</button></td>"
            "</tr>"
        )
    table_html = "\n".join(rows)

    # --- 未決事項の横断
    open_blocks = []
    for c in by_date:
        items = c.open_items
        if not items:
            continue
        lis = []
        for it in items:
            flags = []
            if it.waiting:
                flags.append("<span class=\"tag wait\">待ち</span>")
            if it.deadline and it.deadline < today:
                flags.append("<span class=\"tag over\">期限切れ</span>")
            lis.append(
                f"<li data-kind=\"{esc(it.kind)}\"><span class=\"kind k-{esc(it.kind)}\">{esc(it.kind)}</span> "
                f"{md_inline(it.text)} {' '.join(flags)}</li>"
            )
        open_blocks.append(
            f"<details open class=\"case-open\" data-state=\"{esc(c.state)}\"><summary>"
            f"<a href=\"#view={esc(c.slug)}&line={c.latest.line_no if c.latest else 1}\" class=\"view\" data-slug=\"{esc(c.slug)}\" data-line=\"{c.latest.line_no if c.latest else 1}\">{esc(c.slug)}</a>"
            f"{' <span class=\"tag private\">private</span>' if c.private else ''}"
            f" <span class=\"muted small\">{esc(c.state)} · {esc(c.last_date or '未記録')} · {len(items)} 件</span>"
            f"</summary><ul>{''.join(lis)}</ul></details>"
        )
    open_html = "\n".join(open_blocks) or "<p class=\"muted\">未決事項はありません</p>"

    # --- 時系列
    timeline = []
    for c in cases:
        for e in c.entries:
            if e.date:
                timeline.append((e.date, c, e))
    timeline.sort(key=lambda t: (t[0], t[1].slug), reverse=True)
    tl_rows = []
    for d, c, e in timeline:
        recent = (today - d).days <= RECENT_DAYS
        tl_rows.append(
            f"<tr class=\"{'recent' if recent else 'older'}\"><td class=\"nowrap\">{esc(d)}</td>"
            f"<td class=\"nowrap\">{esc(e.mode)}</td>"
            f"<td class=\"nowrap\"><a href=\"#view={esc(c.slug)}&line={e.line_no}\" class=\"view\" data-slug=\"{esc(c.slug)}\" data-line=\"{e.line_no}\">{esc(c.slug)}</a></td>"
            f"<td><a href=\"#view={esc(c.slug)}&line={e.line_no}\" class=\"view plain\" data-slug=\"{esc(c.slug)}\" data-line=\"{e.line_no}\">{md_inline(e.summary)}</a></td></tr>"
        )
    tl_html = "\n".join(tl_rows)
    n_recent = sum(1 for d, _, _ in timeline if (today - d).days <= RECENT_DAYS)

    # --- セッション
    sessions = load_sessions(cases)
    home = str(Path.home())
    s_rows = []
    for x in sessions:
        pending = x["status"] != "締め済み"
        short_cwd = x["cwd"].replace(home, "~")
        try:
            last = datetime.fromisoformat(x["last_ts"] + "+00:00").astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            last = x["last_ts"]
        ops = []
        if x["status"] == "実行中":
            ops.append(f"<a class=\"btn\" href=\"casebook://session/{esc(x['id'])}?action=terminal\" title=\"この作業ディレクトリで Terminal を開く\">Term</a>")
        else:
            ops.append(f"<a class=\"btn\" href=\"casebook://session/{esc(x['id'])}?action=resume\" title=\"Terminal で claude --resume（/done せず中断した直後の例外用）\">再開</a>")
            if x["slug_known"]:
                ops.append(f"<a class=\"btn\" href=\"casebook://case/{esc(x['slug'])}?action=continue\" title=\"新しいセッションで CASE.md を読ませて続きを始める（正規の道）\">Code</a>")
        case_cell = (f"<a href=\"#view={esc(x['slug'])}\" class=\"view\" data-slug=\"{esc(x['slug'])}\">{esc(x['slug'])}</a>" if x["slug_known"]
                     else (esc(x["slug"]) if x["slug"] else "<span class=\"muted\">不明</span>"))
        s_rows.append(
            f"<tr data-pending=\"{'1' if pending else '0'}\">"
            f"<td class=\"nowrap\"><span class=\"sess s-{esc(x['status'])}\">{esc(x['status'])}</span></td>"
            f"<td>{esc(x['title'])}<div class=\"muted small\" title=\"{esc(x['first'])}\">{esc(x['first'][:60])}</div></td>"
            f"<td class=\"nowrap\">{case_cell}</td>"
            f"<td class=\"small\"><code>{esc(short_cwd)}</code>{(' <span class=\"muted\">' + esc(x['branch']) + '</span>') if x['branch'] else ''}</td>"
            f"<td class=\"nowrap small\">{esc(last)}{('<div class=\"muted\">起動 ' + esc(x['started'][11:16]) + '</div>') if x['started'] else ''}</td>"
            f"<td class=\"num\">{x['turns']}</td>"
            f"<td class=\"actions\">{''.join(ops)}</td>"
            "</tr>"
        )
    sessions_html = "\n".join(s_rows) or "<tr><td colspan=\"7\" class=\"muted\">セッションの記録がありません</td></tr>"
    n_running = sum(1 for x in sessions if x["status"] == "実行中")
    n_pending = sum(1 for x in sessions if x["status"] == "未締め")

    # --- 検索用データ
    search_data = []
    for c in cases:
        files = []
        for f in sorted(c.path.parent.iterdir(), key=lambda x: (x.is_file(), x.name)):
            if f.name.startswith(".") or f.name == "CASE.md":
                continue
            files.append(f.name + ("/" if f.is_dir() else ""))
        search_data.append({
            "slug": c.slug, "area": c.area, "path": str(c.path), "dir": str(c.path.parent),
            "title": c.title, "state": c.state_raw, "files": files, "lines": c.text.splitlines(),
        })
    search_json = json.dumps(search_data, ensure_ascii=False).replace("</", "<\\/")

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return TEMPLATE.format(
        generated=esc(generated),
        n_cases=len(cases), n_notes=len(notes), notes_cls=("warn" if notes else ""),
        n_active=counts["進行中"], n_hold=counts["保留"], n_done=counts["完了"],
        n_open=open_total,
        notes=notes_html,
        table=table_html,
        open_items=open_html,
        timeline=tl_html,
        n_recent=n_recent, recent_days=RECENT_DAYS,
        search_json=search_json,
        repo=esc(str(REPO)), repo_name=esc(REPO.name), macdown_style=('' if MACDOWN else ' style="display:none"'),
        sessions=sessions_html, n_sessions=len(sessions), n_running=n_running, n_pending=n_pending,
    )


TEMPLATE = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{repo_name}</title>
<style>
:root {{
  --bg: #f7f7f5; --fg: #1d1d1b; --muted: #6b6b66; --line: #dedcd6; --card: #ffffff;
  --accent: #2a5db0; --ok: #2f7d4f; --warn: #b7791f; --bad: #b3261e; --hold: #6b6b66;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #161614; --fg: #e8e6e0; --muted: #9c9a93; --line: #33322e; --card: #1f1f1c;
    --accent: #7aa2e8; --ok: #6cc08b; --warn: #e0a84a; --bad: #ef7b73; --hold: #9c9a93;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.65 -apple-system, "Hiragino Sans", "Noto Sans JP", sans-serif; }}
header {{ padding: 20px 16px 8px; border-bottom: 1px solid var(--line); }}
header h1 {{ margin: 0 0 4px; font-size: 22px; }}
header .meta {{ color: var(--muted); font-size: 14px; }}
nav {{ padding: 0 16px; border-bottom: 1px solid var(--line); display: flex; gap: 4px; flex-wrap: wrap; position: sticky; top: 0; background: var(--bg); z-index: 5; }}
nav button {{ font: inherit; font-size: 15px; padding: 10px 14px 8px; border: 0; border-bottom: 2px solid transparent; background: none; color: var(--muted); cursor: pointer; }}
nav button.on {{ color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }}
nav button .count {{ color: var(--muted); font-weight: normal; font-size: 13px; margin-left: 4px; }}
nav button.warn .count {{ color: var(--warn); }}
main {{ padding: 0 16px 48px; max-width: 1320px; margin: 0 auto; }}
section {{ margin: 20px 0; display: none; }}
section.active {{ display: block; }}
h2 {{ font-size: 15px; margin: 0 0 8px; color: var(--muted); font-weight: 600; }}
h2 .count {{ color: var(--muted); font-weight: normal; font-size: 14px; margin-left: 8px; }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; }}
.muted {{ color: var(--muted); }}
.small {{ font-size: 14px; }}
.nowrap {{ white-space: nowrap; }}
.num {{ text-align: right; }}
.stale {{ color: var(--warn); font-weight: 600; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ text-align: left; vertical-align: top; padding: 6px 8px; border-bottom: 1px solid var(--line); }}
th {{ font-weight: 600; color: var(--muted); font-size: 14px; white-space: nowrap; }}
a {{ color: var(--accent); }}
code {{ font: 14px/1.4 ui-monospace, Menlo, monospace; background: var(--bg); padding: 1px 4px; border-radius: 3px; }}
.state {{ padding: 1px 9px; border-radius: 11px; font-size: 13px; color: #fff; white-space: nowrap; }}
.s-進行中 {{ background: var(--ok); }} .s-保留 {{ background: var(--hold); }} .s-完了 {{ background: var(--accent); }}
.tag {{ font-size: 13px; padding: 0 7px; border-radius: 8px; border: 1px solid var(--line); color: var(--muted); white-space: nowrap; }}
.tag.wait {{ color: var(--warn); border-color: var(--warn); }}
.tag.over {{ color: var(--bad); border-color: var(--bad); }}
.tag.private {{ color: var(--bad); border-color: var(--bad); }}
.kind {{ font-size: 13px; padding: 0 7px; border-radius: 3px; color: #fff; white-space: nowrap; }}
.sess {{ padding: 1px 9px; border-radius: 11px; font-size: 13px; color: #fff; white-space: nowrap; }}
.s-実行中 {{ background: var(--ok); }} .s-未締め {{ background: var(--warn); }} .s-締め済み {{ background: var(--hold); }}
.k-決める {{ background: var(--warn); }} .k-やる {{ background: var(--accent); }} .k-確かめる {{ background: var(--ok); }} .k-旧書式 {{ background: var(--hold); }}
.actions {{ white-space: nowrap; }}
.actions a.btn {{ display: inline-block; text-decoration: none; }}
.actions a.btn, .actions button, .filters button {{ font: inherit; font-size: 14px; padding: 3px 10px; margin: 0 4px 4px 0; border: 1px solid var(--line); background: var(--card); color: var(--fg); border-radius: 4px; cursor: pointer; }}
.filters button.on {{ border-color: var(--accent); color: var(--accent); }}
.filters input {{ font: inherit; padding: 3px 8px; border: 1px solid var(--line); border-radius: 4px; background: var(--card); color: var(--fg); min-width: 220px; }}
.filters {{ display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }}
details.case-open {{ border-bottom: 1px solid var(--line); padding: 6px 0; }}
details.case-open summary {{ cursor: pointer; }}
details.case-open ul {{ margin: 4px 0 4px 16px; padding: 0; }}
details.case-open li {{ margin: 2px 0; }}
tr.older {{ display: none; }} table.show-all tr.older {{ display: table-row; }}
#results {{ margin-top: 8px; }}
#results .hit {{ padding: 4px 0; border-bottom: 1px solid var(--line); }}
#results .hit .loc {{ color: var(--muted); font-size: 14px; }}
mark {{ background: #ffe58a; color: #1d1d1b; }}
.scroller {{ overflow-x: auto; }}
.toast {{ position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%); background: var(--fg); color: var(--bg); padding: 6px 14px; border-radius: 6px; font-size: 14px; opacity: 0; transition: opacity .2s; }}
.toast.show {{ opacity: 1; }}
a.plain {{ color: inherit; text-decoration: none; }} a.plain:hover {{ text-decoration: underline; }}
#viewer {{ position: fixed; inset: 0; background: rgba(0,0,0,.45); display: none; z-index: 10; }}
#viewer.open {{ display: block; }}
#viewer .pane {{ position: absolute; top: 0; right: 0; bottom: 0; width: min(1000px, 100%); background: var(--card); border-left: 1px solid var(--line); display: flex; flex-direction: column; }}
#viewer .bar {{ padding: 10px 16px; border-bottom: 1px solid var(--line); display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
#viewer .bar .ttl {{ font-weight: 600; margin-right: auto; }}
#viewer .bar a.btn, #viewer .bar button {{ font: inherit; font-size: 14px; padding: 3px 10px; border: 1px solid var(--line); background: var(--card); color: var(--fg); border-radius: 4px; cursor: pointer; text-decoration: none; }}
#viewer .files {{ padding: 6px 16px; border-bottom: 1px solid var(--line); font-size: 14px; color: var(--muted); }}
#viewer .files a {{ margin-right: 10px; }}
#viewer .body {{ overflow: auto; padding: 8px 24px 48px; flex: 1; }}
.md h1 {{ font-size: 22px; margin: 16px 0 8px; }} .md h2 {{ font-size: 18px; margin: 28px 0 8px; padding-top: 8px; border-top: 1px solid var(--line); }}
.md h3 {{ font-size: 16px; margin: 16px 0 4px; color: var(--muted); }} .md h4, .md h5, .md h6 {{ font-size: 16px; margin: 12px 0 4px; }}
.md p {{ margin: 6px 0; }} .md ul, .md ol {{ margin: 4px 0; padding-left: 22px; }} .md li {{ margin: 2px 0; }}
.md pre {{ background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; overflow: auto; font: 14px/1.5 ui-monospace, Menlo, monospace; }}
.md pre code {{ background: none; padding: 0; }}
.md table {{ width: auto; margin: 6px 0; font-size: 15px; }} .md th, .md td {{ padding: 4px 8px; border: 1px solid var(--line); }}
.md blockquote {{ margin: 6px 0; padding: 2px 12px; border-left: 3px solid var(--line); color: var(--muted); }}
.md hr {{ border: 0; border-top: 1px solid var(--line); margin: 12px 0; }}
.md .hl {{ background: #ffe58a33; outline: 2px solid var(--warn); outline-offset: 4px; border-radius: 4px; }}
@media (max-width: 720px) {{ .hide-m {{ display: none; }} }}
</style>
</head>
<body>
<header>
  <h1>{repo_name}</h1>
  <div class="meta">生成 {generated} · 案件 {n_cases}（進行中 {n_active} / 保留 {n_hold} / 完了 {n_done}）· 未決 {n_open} 件 · <code>{repo}</code> · <a href="casebook://dashboard?action=rebuild" id="rebuild" title="dashboard.py --html を実行して読み直す">再生成</a></div>
</header>
<nav id="tabs">
  <button type="button" data-tab="notes" class="{notes_cls}">注意<span class="count">{n_notes}</span></button>
  <button type="button" data-tab="sessions">セッション<span class="count">{n_pending}</span></button>
  <button type="button" data-tab="cases">案件<span class="count">{n_active}</span></button>
  <button type="button" data-tab="open">未決事項<span class="count">{n_open}</span></button>
  <button type="button" data-tab="timeline">時系列<span class="count">{n_recent}</span></button>
  <button type="button" data-tab="search">検索</button>
</nav>
<main>

<section id="notes">
  <h2>注意</h2>
  <div class="card"><ul>{notes}</ul></div>
</section>

<section id="sessions">
  <h2>セッション <span class="count">未完了 {n_pending} 件 · 実行中 {n_running} 件 · 全 {n_sessions} 件</span></h2>
  <div class="filters" data-target="sessions-table">
    <button type="button" class="on" data-pending="1">未完了だけ</button>
    <button type="button" data-pending="">すべて</button>
  </div>
  <div class="scroller"><table id="sessions-table">
    <thead><tr><th>状態</th><th>題名 / 最初の発話</th><th>案件</th><th>作業ディレクトリ</th><th>最終更新</th><th>発話</th><th>操作</th></tr></thead>
    <tbody>
{sessions}
    </tbody>
  </table></div>
  <p class="muted small">「実行中」は Code 起動時の hook が登録したもの（この workbench で起動したセッションだけ判定できる）。「未締め」は記録に <code>/done</code> も <code>/writeback</code> も無いもので、読むだけのセッションも含む。「再開」は <code>/done</code> せず中断した直後の例外用で、案件の続きは「Code」（新しいセッションで CASE.md を読ませる）が正規の道。</p>
</section>

<section id="cases">
  <h2>案件一覧 <span class="count">最終更新の新しい順</span></h2>
  <div class="filters" data-target="cases-table">
    <button type="button" class="on" data-state="">すべて</button>
    <button type="button" data-state="進行中">進行中</button>
    <button type="button" data-state="保留">保留</button>
    <button type="button" data-state="完了">完了</button>
    <input type="search" id="case-filter" placeholder="slug・題名・分類で絞り込み">
  </div>
  <div class="scroller"><table id="cases-table">
    <thead><tr><th>案件</th><th>分類</th><th>状態</th><th>最終更新</th><th>未決</th><th class="hide-m">定期作業</th><th>開く</th></tr></thead>
    <tbody>
{table}
    </tbody>
  </table></div>
  <p class="muted small">slug を押すとページ内のビューアで CASE.md を読める。「MacDown」は MacDown がインストールされているときだけ出て、<code>x-macdown://</code> で開く。「Code」は Terminal で <code>claude</code> を起動して CASE.md を読ませ、「Term」は Terminal を案件ディレクトリで開く（どちらも <code>casebook://</code>。受けるのは <code>~/Applications/Casebook Launcher.app</code>、作り方は <code>tools/launcher/build.sh</code>）。初回はブラウザが確認を出す。「パス」は CASE.md の絶対パスをコピーする。</p>
</section>

<section id="open">
  <h2>未決事項の横断 <span class="count">各案件の最新エントリ</span></h2>
  <div class="filters" data-target="open-list">
    <button type="button" class="on" data-kind="">すべて</button>
    <button type="button" data-kind="決める">決める</button>
    <button type="button" data-kind="やる">やる</button>
    <button type="button" data-kind="確かめる">確かめる</button>
    <button type="button" data-kind="旧書式">旧書式</button>
  </div>
  <div id="open-list" class="card">
{open_items}
  </div>
</section>

<section id="timeline">
  <h2>時系列 <span class="count">直近 {recent_days} 日で {n_recent} 件</span></h2>
  <div class="filters"><button type="button" id="tl-toggle">すべて表示</button></div>
  <div class="scroller"><table id="tl-table">
    <thead><tr><th>日付</th><th>モード</th><th>案件</th><th>要約</th></tr></thead>
    <tbody>
{timeline}
    </tbody>
  </table></div>
</section>

<section id="search">
  <h2>検索 <span class="count">CASE.md 本文（grep -ril の代わり）</span></h2>
  <div class="filters"><input type="search" id="q" placeholder="語を入力（2 文字以上、大文字小文字は区別しない）"><span class="muted small" id="q-count"></span></div>
  <div id="results"></div>
</section>

</main>
<div id="viewer" role="dialog" aria-modal="true">
  <div class="pane">
    <div class="bar">
      <span class="ttl" id="v-title"></span>
      <a id="v-macdown" class="btn" href="#"{macdown_style}>MacDown</a>
      <a id="v-code" class="btn" href="#" title="Terminal で claude を起動し、CASE.md を読ませて続きを始める">Code</a>
      <a id="v-term" class="btn" href="#" title="Terminal を案件ディレクトリで開く">Term</a>
      <a id="v-finder" class="btn" href="#" title="Finder で案件ディレクトリを開く">Finder</a>
      <a id="v-file" href="#" target="_blank">原本（file://）</a>
      <button type="button" id="v-copy">パス</button>
      <button type="button" id="v-close" title="閉じる (Esc)">閉じる</button>
    </div>
    <div class="files" id="v-files"></div>
    <div class="body md" id="v-body"></div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script id="data" type="application/json">{search_json}</script>
<script>
(function () {{
  var toast = document.getElementById('toast');
  function say(msg) {{ toast.textContent = msg; toast.classList.add('show'); setTimeout(function () {{ toast.classList.remove('show'); }}, 1500); }}
  document.querySelectorAll('button[data-copy]').forEach(function (b) {{
    b.addEventListener('click', function () {{
      var t = b.getAttribute('data-copy');
      if (navigator.clipboard) {{ navigator.clipboard.writeText(t).then(function () {{ say('コピーしました'); }}, function () {{ window.prompt('コピー:', t); }}); }}
      else {{ window.prompt('コピー:', t); }}
    }});
  }});

  // タブ
  var tabs = document.querySelectorAll('#tabs button');
  function showTab(id, push) {{
    if (!document.getElementById(id)) id = 'notes';
    document.querySelectorAll('main > section').forEach(function (s) {{ s.classList.toggle('active', s.id === id); }});
    tabs.forEach(function (b) {{ b.classList.toggle('on', b.dataset.tab === id); }});
    try {{ localStorage.setItem('wb-tab', id); }} catch (e) {{}}
    if (push && location.hash.indexOf('#view=') !== 0) history.replaceState(null, '', '#tab=' + id);
    window.scrollTo(0, 0);
  }}
  tabs.forEach(function (b) {{ b.addEventListener('click', function () {{ showTab(b.dataset.tab, true); }}); }});
  (function initTab() {{
    var m = /^#tab=([a-z]+)/.exec(location.hash), saved = null;
    try {{ saved = localStorage.getItem('wb-tab'); }} catch (e) {{}}
    showTab(m ? m[1] : (saved || 'notes'), false);
  }})();

  // 案件一覧の絞り込み
  var caseState = '', caseText = '';
  function applyCases() {{
    document.querySelectorAll('#cases-table tbody tr').forEach(function (tr) {{
      var ok = (!caseState || tr.dataset.state === caseState) && (!caseText || tr.dataset.text.indexOf(caseText) >= 0);
      tr.style.display = ok ? '' : 'none';
    }});
  }}
  document.querySelectorAll('.filters[data-target="cases-table"] button').forEach(function (b) {{
    b.addEventListener('click', function () {{
      document.querySelectorAll('.filters[data-target="cases-table"] button').forEach(function (x) {{ x.classList.remove('on'); }});
      b.classList.add('on'); caseState = b.dataset.state; applyCases();
    }});
  }});
  document.getElementById('case-filter').addEventListener('input', function (e) {{ caseText = e.target.value.trim().toLowerCase(); applyCases(); }});

  // セッションの絞り込み
  function applySessions(onlyPending) {{
    document.querySelectorAll('#sessions-table tbody tr[data-pending]').forEach(function (tr) {{ tr.style.display = (!onlyPending || tr.dataset.pending === '1') ? '' : 'none'; }});
  }}
  document.querySelectorAll('.filters[data-target="sessions-table"] button').forEach(function (b) {{
    b.addEventListener('click', function () {{
      document.querySelectorAll('.filters[data-target="sessions-table"] button').forEach(function (x) {{ x.classList.remove('on'); }});
      b.classList.add('on'); applySessions(b.dataset.pending === '1');
    }});
  }});
  applySessions(true);

  // 未決事項の種類フィルタ
  document.querySelectorAll('.filters[data-target="open-list"] button').forEach(function (b) {{
    b.addEventListener('click', function () {{
      document.querySelectorAll('.filters[data-target="open-list"] button').forEach(function (x) {{ x.classList.remove('on'); }});
      b.classList.add('on');
      var k = b.dataset.kind;
      document.querySelectorAll('#open-list details').forEach(function (d) {{
        var any = false;
        d.querySelectorAll('li').forEach(function (li) {{ var ok = !k || li.dataset.kind === k; li.style.display = ok ? '' : 'none'; any = any || ok; }});
        d.style.display = any ? '' : 'none';
      }});
    }});
  }});

  // 時系列の全件表示
  document.getElementById('tl-toggle').addEventListener('click', function () {{
    var t = document.getElementById('tl-table'); t.classList.toggle('show-all');
    this.textContent = t.classList.contains('show-all') ? '直近だけ表示' : 'すべて表示';
  }});

  // 本文検索
  var data = JSON.parse(document.getElementById('data').textContent);
  var results = document.getElementById('results'), qcount = document.getElementById('q-count');
  function escapeHtml(s) {{ return s.replace(/[&<>"]/g, function (c) {{ return {{'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}}[c]; }}); }}
  function search(q) {{
    results.innerHTML = ''; qcount.textContent = '';
    if (q.length < 2) return;
    var ql = q.toLowerCase(), hits = 0, LIMIT = 200, frag = document.createDocumentFragment();
    outer: for (var i = 0; i < data.length; i++) {{
      var c = data[i];
      for (var n = 0; n < c.lines.length; n++) {{
        var line = c.lines[n];
        if (line.toLowerCase().indexOf(ql) < 0) continue;
        hits++;
        if (hits > LIMIT) break outer;
        var div = document.createElement('div'); div.className = 'hit';
        var re = new RegExp(q.replace(/[.*+?^${{}}()|[\]\\]/g, '\\$&'), 'ig');
        div.innerHTML = '<div class="loc"><a href="#view=' + escapeHtml(c.slug) + '&line=' + (n + 1) + '" class="view" data-slug="' + escapeHtml(c.slug) + '" data-line="' + (n + 1) + '">' + escapeHtml(c.slug) + '</a>' + (c.area === 'private' ? ' <span class="tag private">private</span>' : '') + ' : ' + (n + 1) + '</div>' +
          '<div>' + escapeHtml(line).replace(re, function (m) {{ return '<mark>' + m + '</mark>'; }}) + '</div>';
        frag.appendChild(div);
      }}
    }}
    results.appendChild(frag);
    qcount.textContent = hits > LIMIT ? LIMIT + ' 件以上（先頭 ' + LIMIT + ' 件）' : hits + ' 件';
  }}
  var timer = null;
  document.getElementById('q').addEventListener('input', function (e) {{ clearTimeout(timer); var v = e.target.value.trim(); timer = setTimeout(function () {{ search(v); }}, 150); }});

  // ---- Markdown ビューア（CASE.md で使う記法だけ: 見出し・箇条書き・番号付き・表・コード・引用・罫線・太字・コード・リンク）
  function inline(s, dir) {{
    s = escapeHtml(s);
    s = s.replace(/`([^`]+)`/g, function (_, c) {{ return '<code>' + c + '</code>'; }});
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (_, t, u) {{
      var href = u;
      if (!/^[a-z]+:/i.test(u) && !u.startsWith('#')) {{ var p = dir + '/' + u; href = /\.md$/i.test(u) ? macdown(p) : fileUrl(p); }}
      return '<a href="' + href + '" target="_blank">' + t + '</a>';
    }});
    s = s.replace(/(^|[^"'>])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank">$2</a>');
    return s;
  }}
  function renderMd(lines, dir) {{
    var out = [], i = 0, n = lines.length;
    function isTableSep(l) {{ return /^\|?\s*:?-{{2,}}:?\s*(\|\s*:?-{{2,}}:?\s*)*\|?\s*$/.test(l); }}
    while (i < n) {{
      var l = lines[i], start = i + 1;
      if (/^```/.test(l)) {{
        var buf = []; i++;
        while (i < n && !/^```/.test(lines[i])) buf.push(lines[i++]);
        i++; out.push('<pre id="L' + start + '"><code>' + escapeHtml(buf.join('\n')) + '</code></pre>'); continue;
      }}
      var h = /^(#{{1,6}})\s+(.*)$/.exec(l);
      if (h) {{ out.push('<h' + h[1].length + ' id="L' + (i + 1) + '">' + inline(h[2], dir) + '</h' + h[1].length + '>'); i++; continue; }}
      if (/^\s*(-{{3,}}|\*{{3,}})\s*$/.test(l)) {{ out.push('<hr>'); i++; continue; }}
      if (/^\s*\|/.test(l) && i + 1 < n && isTableSep(lines[i + 1])) {{
        var cells = function (r) {{ r = r.trim().replace(/^\|/, '').replace(/\|$/, ''); return r.split(/\s\|\s|\|/).map(function (c) {{ return c.trim(); }}); }};
        var head = cells(l); i += 2; var rows = [];
        while (i < n && /^\s*\|/.test(lines[i])) rows.push(cells(lines[i++]));
        out.push('<table id="L' + start + '"><thead><tr>' + head.map(function (c) {{ return '<th>' + inline(c, dir) + '</th>'; }}).join('') + '</tr></thead><tbody>' +
          rows.map(function (r) {{ return '<tr>' + r.map(function (c) {{ return '<td>' + inline(c, dir) + '</td>'; }}).join('') + '</tr>'; }}).join('') + '</tbody></table>');
        continue;
      }}
      var li = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(l);
      if (li) {{
        var stack = [];  // {{indent, tag}}
        var html = '';
        while (i < n) {{
          var m = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(lines[i]), lineNo = i + 1;
          if (!m) {{
            if (lines[i].trim() && /^\s+/.test(lines[i]) && stack.length) {{ html += ' ' + inline(lines[i].trim(), dir); i++; continue; }}
            break;
          }}
          var ind = m[1].length, tag = /\d/.test(m[2]) ? 'ol' : 'ul';
          while (stack.length && ind < stack[stack.length - 1].indent) {{ html += '</li></' + stack.pop().tag + '>'; }}
          if (!stack.length || ind > stack[stack.length - 1].indent) {{ stack.push({{indent: ind, tag: tag}}); html += '<' + tag + '><li id="L' + lineNo + '">'; }}
          else {{ html += '</li><li id="L' + lineNo + '">'; }}
          html += inline(m[3], dir); i++;
        }}
        while (stack.length) html += '</li></' + stack.pop().tag + '>';
        out.push(html); continue;
      }}
      if (/^>\s?/.test(l)) {{
        var q = []; while (i < n && /^>\s?/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ''));
        out.push('<blockquote id="L' + start + '">' + inline(q.join(' '), dir) + '</blockquote>'); continue;
      }}
      if (!l.trim()) {{ i++; continue; }}
      var para = [];
      while (i < n && lines[i].trim() && !/^(#{{1,6}}\s|```|\s*\||\s*([-*]|\d+\.)\s|>)/.test(lines[i])) para.push(lines[i++]);
      if (para.length) {{
        // 「分類: 製品 X」のようなヘッダ行は 1 行ずつ改行して読めるようにする
        var ph = para.map(function (x, k) {{ return (k > 0 && /^[^\s:：]{{1,12}}[:：]\s/.test(x) ? '<br>' : (k > 0 ? ' ' : '')) + inline(x, dir); }}).join('');
        out.push('<p id="L' + start + '">' + ph + '</p>');
      }} else i++;
    }}
    return out.join('\n');
  }}
  function fileUrl(p) {{ return 'file://' + encodeURI(p); }}
  function macdown(p) {{ return 'x-macdown://open?url=' + encodeURIComponent(fileUrl(p)); }}
  var viewer = document.getElementById('viewer'), vBody = document.getElementById('v-body');
  var bySlug = {{}}; data.forEach(function (c) {{ bySlug[c.slug] = c; }});
  function openViewer(slug, line) {{
    var c = bySlug[slug]; if (!c) return;
    document.getElementById('v-title').innerHTML = escapeHtml(slug) + (c.area === 'private' ? ' <span class="tag private">private</span>' : '') + ' <span class="muted small">' + escapeHtml(c.title) + ' · ' + escapeHtml(c.state) + '</span>';
    document.getElementById('v-file').href = fileUrl(c.path);
    document.getElementById('v-copy').onclick = function () {{ navigator.clipboard && navigator.clipboard.writeText(c.path).then(function () {{ say('コピーしました'); }}); }};
    document.getElementById('v-macdown').href = macdown(c.path);
    document.getElementById('v-code').href = 'casebook://case/' + encodeURIComponent(slug) + '?action=continue';
    document.getElementById('v-term').href = 'casebook://case/' + encodeURIComponent(slug) + '?action=terminal';
    document.getElementById('v-finder').href = 'casebook://case/' + encodeURIComponent(slug) + '?action=finder';
    document.getElementById('v-files').innerHTML = c.files.length
      ? '同じディレクトリ: ' + c.files.map(function (f) {{ var p = c.dir + '/' + f; return /\.md$/i.test(f) ? '<a href="' + macdown(p) + '" title="MacDown 3000 で開く">' + escapeHtml(f) + '</a>' : '<a href="' + fileUrl(p) + '" target="_blank">' + escapeHtml(f) + '</a>'; }}).join('')
      : '同じディレクトリに他のファイルはない';
    vBody.innerHTML = renderMd(c.lines, c.dir);
    viewer.classList.add('open'); document.body.style.overflow = 'hidden';
    var target = line ? document.getElementById('L' + line) : null;
    vBody.scrollTop = 0;
    if (target) {{ target.classList.add('hl'); target.scrollIntoView(); vBody.scrollTop -= 12; }}
  }}
  function closeViewer() {{ viewer.classList.remove('open'); document.body.style.overflow = ''; if (location.hash.indexOf('#view=') === 0) {{ var cur = document.querySelector('main > section.active'); history.replaceState(null, '', '#tab=' + (cur ? cur.id : 'notes')); }} }}
  document.getElementById('v-close').addEventListener('click', closeViewer);
  viewer.addEventListener('click', function (e) {{ if (e.target === viewer) closeViewer(); }});
  document.addEventListener('keydown', function (e) {{ if (e.key === 'Escape' && viewer.classList.contains('open')) closeViewer(); }});
  document.querySelectorAll('a.view').forEach(function (a) {{
    a.addEventListener('click', function (e) {{ e.preventDefault(); history.replaceState(null, '', a.getAttribute('href')); openViewer(a.dataset.slug, parseInt(a.dataset.line || '0', 10)); }});
  }});
  results.addEventListener('click', function (e) {{
    var a = e.target.closest('a.view'); if (!a) return;
    e.preventDefault(); openViewer(a.dataset.slug, parseInt(a.dataset.line || '0', 10));
  }});
  function fromHash() {{
    var m = /^#view=([^&]+)(?:&line=(\d+))?/.exec(location.hash);
    if (m) {{ openViewer(decodeURIComponent(m[1]), parseInt(m[2] || '0', 10)); return; }}
    if (viewer.classList.contains('open')) closeViewer();
    var t = /^#tab=([a-z]+)/.exec(location.hash);
    if (t) showTab(t[1], false);
  }}
  document.getElementById('rebuild').addEventListener('click', function () {{ say('再生成中…'); setTimeout(function () {{ location.reload(); }}, 2500); }});
  window.addEventListener('hashchange', fromHash);
  fromHash();
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- main

def main(argv):
    today = date.today()
    if "--date" in argv:
        today = date.fromisoformat(argv[argv.index("--date") + 1])
    cases, missing = load_cases()
    if "--text" in argv:
        print(text_rows(cases, today))
        return 0
    if "--html" in argv:
        i = argv.index("--html")
        out = DEFAULT_HTML
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            out = Path(argv[i + 1]).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(cases, missing, today), encoding="utf-8")
        print(out)
        if "--open" in argv:
            subprocess.run(["open", str(out)], check=False)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
