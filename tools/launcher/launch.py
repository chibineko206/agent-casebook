#!/usr/bin/env python3
"""casebook:// URL を受けて、ダッシュボードからの操作を実行する。

`Casebook Launcher.app`（`build.sh` で作る）が `open location` で受けた URL をそのまま渡す。
受けるのは案件の slug と動作名だけで、コマンド文字列は受けない。
slug は英小文字・数字・ハイフンに限り、`cases/` か `private/` に実在することを確かめてから動く。

  casebook://case/<slug>?action=continue   Terminal を開き、claude に CASE.md を読ませて続きを始める
  casebook://case/<slug>?action=terminal   Terminal を案件ディレクトリで開く
  casebook://case/<slug>?action=finder     Finder で案件ディレクトリを開く
  casebook://dashboard?action=rebuild      dashboard.py --html を実行してダッシュボードを更新する
  casebook://session/<id>?action=resume    Terminal を開き、そのセッションの作業ディレクトリで claude --resume <id>
  casebook://session/<id>?action=terminal  Terminal をそのセッションの作業ディレクトリで開く

Terminal は Apple Event（AppleScript の do script）ではなく、`.command` ファイルを
`open -a Terminal` で開いて動かす。Apple Event だと macOS のオートメーション許可が要り、
URL スキームで起動したアプリからは許可の確認が出ずに拒否されたため。

使い方（手で試すとき）: python3 launch.py '<url>' [--dry-run]
実行結果は .tmp/launcher.log に 1 行ずつ残す（アプリ経由だと標準出力が見えないため）。
"""
import datetime
import os
import re
import shlex
import subprocess
import sys
import urllib.parse
from pathlib import Path



def find_repo(start):
    """CLAUDE.md と .claude/ を持つ最も近い親ディレクトリをリポジトリとみなす（置き場所に依存しない）。"""
    for d in (start, *start.parents):
        if (d / "CLAUDE.md").is_file() and (d / ".claude").is_dir():
            return d
    return start.parents[2]


REPO = find_repo(Path(__file__).resolve().parent)
TMP = REPO / ".tmp"
LOG = TMP / "launcher.log"
DASHBOARD = REPO / "tools" / "dashboard.py"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SESSION_RE = re.compile(r"^[0-9a-f-]{8,64}$")
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
ACTIONS = ("continue", "terminal", "finder", "rebuild", "resume")


class Reject(Exception):
    pass


def log(line):
    try:
        TMP.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
    except OSError:
        pass


def notify(message):
    """失敗を通知センターに出す（best effort。アプリ経由では標準出力が見えないため）。"""
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript", "-e", f'display notification "{safe}" with title "Casebook Launcher"'], check=False)


def case_dir(slug):
    if not SLUG_RE.match(slug):
        raise Reject(f"slug の形式が不正: {slug!r}")
    for area in ("cases", "private"):
        d = REPO / area / slug
        if d.is_dir():
            return d
    raise Reject(f"案件が見つからない: {slug}")


def session_cwd(sid):
    """セッション id から記録ファイルを探し、最初の発話の cwd を返す。"""
    if not SESSION_RE.match(sid):
        raise Reject(f"セッション id の形式が不正: {sid!r}")
    files = list(CLAUDE_PROJECTS.glob(f"*/{sid}.jsonl"))
    if not files:
        raise Reject(f"セッションの記録が見つからない: {sid}")
    import json
    with open(files[0], encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"cwd":"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            cwd = d.get("cwd")
            if cwd and Path(cwd).is_dir():
                return cwd
    raise Reject(f"セッションの作業ディレクトリが分からない: {sid}")


def plan(url):
    """URL を検証し、(種類, 中身) を返す。種類は terminal / finder / rebuild。"""
    u = urllib.parse.urlparse(url)
    if u.scheme != "casebook":
        raise Reject(f"スキームが違う: {url}")
    q = urllib.parse.parse_qs(u.query)
    action = (q.get("action") or [""])[0]
    if action not in ACTIONS:
        raise Reject(f"動作が不正: {action!r}")
    if u.netloc == "dashboard" and action == "rebuild":
        return "rebuild", None
    if u.netloc == "session" and action in ("resume", "terminal"):
        sid = u.path.strip("/")
        cwd = session_cwd(sid)
        if action == "terminal":
            return "terminal", (f"session-{sid[:8]}-terminal", f"cd {shlex.quote(cwd)}\nexec /bin/zsh -l")
        inner = f"claude --resume {shlex.quote(sid)}; exec /bin/zsh -l"
        return "terminal", (f"session-{sid[:8]}-resume", f"cd {shlex.quote(cwd)}\nexec /bin/zsh -l -i -c {shlex.quote(inner)}")
    if u.netloc != "case" or action in ("rebuild", "resume"):
        raise Reject(f"URL の形式が不正: {url}")
    d = case_dir(u.path.strip("/"))
    slug = d.name
    if action == "finder":
        return "finder", str(d)
    if action == "terminal":
        # 案件ディレクトリに移って対話シェルに入る
        return "terminal", (f"{slug}-terminal", f"cd {shlex.quote(str(d))}\nexec /bin/zsh -l")
    prompt = f"{d.relative_to(REPO)}/CASE.md を読んで、この案件の続きをやる。終わるときは /done {slug}"
    # ログインシェル（-l）と対話シェル（-i）の初期化を通して PATH を揃えてから claude を起動し、終了後はシェルに戻す
    inner = f"claude {shlex.quote(prompt)}; exec /bin/zsh -l"
    return "terminal", (f"{slug}-continue", f"cd {shlex.quote(str(REPO))}\nexec /bin/zsh -l -i -c {shlex.quote(inner)}")


def run_terminal(name, body, dry):
    """`.command` ファイルを書いて Terminal.app に開かせる（新しいウィンドウで実行される）。"""
    TMP.mkdir(parents=True, exist_ok=True)
    f = TMP / f"launch-{name}.command"
    content = "#!/bin/zsh\n" + body + "\n"
    if dry:
        print(f"open -a Terminal {shlex.quote(str(f))}")
        print(content, end="")
        return
    f.write_text(content, encoding="utf-8")
    os.chmod(f, 0o700)
    subprocess.run(["open", "-a", "Terminal", str(f)], check=True)


def run(url, dry):
    kind, payload = plan(url)
    if kind == "finder":
        if dry:
            print(f"open {shlex.quote(payload)}")
        else:
            subprocess.run(["open", payload], check=True)
    elif kind == "rebuild":
        cmd = [sys.executable, str(DASHBOARD), "--html"]
        if dry:
            print(" ".join(shlex.quote(c) for c in cmd))
        else:
            subprocess.run(cmd, check=True)
    else:
        name, body = payload
        run_terminal(name, body, dry)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    dry = "--dry-run" in argv
    url = next(a for a in argv if not a.startswith("--"))
    try:
        run(url, dry)
    except Reject as e:
        if dry:
            print(f"reject: {e}")
        else:
            log(f"reject {url}: {e}")
            notify(str(e))
        return 1
    except Exception as e:
        if dry:
            print(f"error: {e}")
        else:
            log(f"error {url}: {e!r}")
            notify(f"実行に失敗: {e}")
        return 1
    if not dry:
        log(f"ok {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
