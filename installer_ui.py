"""图形化安装程序。

界面用 pywebview 打开，但业务全部走本地 HTTP 接口（Flask），
不依赖 pywebview 的 JS 桥接 —— 那套桥接在部分环境下会不可用。

用法：
  无参数        图形安装向导
  --silent 路径 静默安装
  --uninstall   卸载
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

from flask import Flask, jsonify, request

from install_common import (
    APP_DISPLAY,
    APP_NAME,
    DEFAULT_DIR,
    EXE_NAME,
    LEGACY_UNINSTALLER,
    PROGRAM_ITEMS,
    UNINSTALLER_NAME,
    UNINSTALL_KEY,
    desktop_dir,
    dir_size,
    human_size,
    read_installed,
    start_menu_dir,
    taskkill_app,
)

APP_VERSION = "3.7.15"

app = Flask("installer", static_folder=None)

STATE = {
    "window": None,
    "progress": 0,
    "message": "准备中…",
    "done": False,
    "error": "",
    "path": "",
}


def base_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


def find_payload() -> Path:
    bundled = base_dir() / "payload.zip"
    if bundled.exists():
        return bundled
    return Path(__file__).parent / "payload.zip"


def make_shortcut(link: Path, target: Path) -> None:
    def q(value) -> str:  # PowerShell 单引号里的单引号要写两遍
        return str(value).replace("'", "''")

    script = (
        "$W = New-Object -ComObject WScript.Shell;"
        f"$S = $W.CreateShortcut('{q(link)}');"
        f"$S.TargetPath = '{q(target)}';"
        f"$S.WorkingDirectory = '{q(target.parent)}';"
        f"$S.IconLocation = '{q(target)}';"
        "$S.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script],
                   capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def write_registry(uninstall_path: Path) -> None:
    """登记「已安装的应用」里的卸载入口。

    UninstallString 指向卸载器本体（不带参数），控制面板点一下就出卸载界面；
    QuietUninstallString 是静默卸载，默认保留本地资料 —— 静默那条路不该替用户
    决定删掉账号和题库。
    """
    try:
        import datetime
        import winreg

        app_exe = uninstall_path.parent / EXE_NAME
        quoted = f'"{uninstall_path}"'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_DISPLAY)
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "lingluandf")
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(uninstall_path.parent))
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, quoted)
            winreg.SetValueEx(key, "QuietUninstallString", 0, winreg.REG_SZ, quoted + " --silent")
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ,
                              str(app_exe if app_exe.exists() else uninstall_path))
            winreg.SetValueEx(key, "InstallDate", 0, winreg.REG_SZ,
                              datetime.date.today().strftime("%Y%m%d"))
            try:
                estimated = (dir_size(uninstall_path.parent / "_internal")
                             + dir_size(app_exe)) // 1024
                winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, max(1, estimated))
            except Exception:
                pass
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
    except Exception:
        pass


def detect_installed(target: Path = None) -> dict:
    """看看这台机器上是不是已经装过，装过就报出位置和版本。"""
    info = read_installed()
    location = Path(info["location"]) if info.get("location") else None
    if location and (location / EXE_NAME).exists():
        return {"installed": True, "version": info.get("version", ""),
                "location": str(location)}
    for candidate in ([target] if target else []) + [DEFAULT_DIR]:
        if candidate and (candidate / EXE_NAME).exists():
            return {"installed": True, "version": info.get("version", ""),
                    "location": str(candidate)}
    return {"installed": False, "version": "", "location": ""}


def choose_folder(current: str) -> str:
    """调用系统文件夹选择框。失败时返回空串，前端会提示手动输入。"""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            picked = filedialog.askdirectory(
                title="选择安装位置",
                initialdir=current or str(DEFAULT_DIR.parent),
                mustexist=True,
            )
        finally:
            root.destroy()
        if not picked:
            return ""
        path = Path(picked)
        if path.name.lower() != APP_NAME.lower():
            path = path / APP_NAME
        return str(path)
    except Exception:
        return ""


def install_files(target: Path, report=None) -> None:
    """唯一的安装实现：清程序文件 → 解压 → 快捷方式 → 注册表。"""
    def say(pct: int, msg: str) -> None:
        if report:
            report(pct, msg)

    payload = find_payload()
    if not payload.exists():
        raise RuntimeError("安装包内缺少程序文件")

    say(5, "正在准备安装目录…")
    target.mkdir(parents=True, exist_ok=True)
    # 覆盖安装时程序可能还在托盘里跑着，占着 _internal 里的 dll 会让解压失败
    taskkill_app()
    time.sleep(1.0)
    # 只删本程序的目录和可执行文件。配置、cookie、题库是用户的，重装不能清掉；
    # 万一目录选错了，也只会碰到这几个固定名字，不会把别人的文件删光。
    for item in target.iterdir():
        if item.name not in PROGRAM_ITEMS:
            continue
        try:
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        except Exception:
            pass

    say(12, "正在解压程序文件…")
    with zipfile.ZipFile(payload) as zf:
        members = zf.infolist()
        total = len(members) or 1
        for idx, member in enumerate(members, 1):
            zf.extract(member, target)
            if idx % 60 == 0 or idx == total:
                say(12 + int(idx / total * 72), f"正在解压 {idx}/{total} 个文件…")

    say(88, "正在创建快捷方式…")
    exe = target / EXE_NAME
    make_shortcut(desktop_dir() / f"{APP_DISPLAY}.lnk", exe)
    make_shortcut(start_menu_dir() / f"{APP_DISPLAY}.lnk", exe)

    say(94, "正在注册卸载信息…")
    uninstaller = target / UNINSTALLER_NAME
    if not uninstaller.exists():
        # 老安装包里没有独立卸载器，退回旧做法：把自己复制过去当卸载程序
        try:
            shutil.copy2(Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__),
                         target / LEGACY_UNINSTALLER)
            uninstaller = target / LEGACY_UNINSTALLER
        except Exception:
            pass
    write_registry(uninstaller)

    say(100, "安装完成")


def do_install(target: Path) -> None:
    try:
        install_files(target, lambda pct, msg: STATE.update(progress=pct, message=msg))
        STATE.update(done=True, path=str(target))
    except Exception as exc:
        STATE.update(error=str(exc), message="安装失败：" + str(exc))


@app.get("/")
def index():
    return HTML.replace("__VERSION__", APP_VERSION).replace(
        "__DEFAULT_DIR__", json.dumps(str(DEFAULT_DIR))
    )


@app.get("/api/default_dir")
def api_default_dir():
    return jsonify(path=str(DEFAULT_DIR))


@app.get("/api/detect")
def api_detect():
    """界面上要明确告诉用户：这是覆盖安装，资料不会丢。"""
    return jsonify(detect_installed())


@app.get("/api/progress")
def api_progress():
    return jsonify(
        progress=STATE["progress"], message=STATE["message"],
        done=STATE["done"], error=STATE["error"], path=STATE["path"],
    )


@app.post("/api/install")
def api_install():
    data = request.get_json(silent=True) or {}
    target = str(data.get("path", "")).strip()
    if not target:
        return jsonify(ok=False, error="请填写安装位置")
    STATE.update(progress=1, message="开始安装…", done=False, error="", path="")
    threading.Thread(target=do_install, args=(Path(target),), daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/browse")
def api_browse():
    data = request.get_json(silent=True) or {}
    return jsonify(path=choose_folder(str(data.get("current", ""))))


@app.post("/api/launch")
def api_launch():
    data = request.get_json(silent=True) or {}
    target = Path(str(data.get("path", "")))
    try:
        exe = target / EXE_NAME
        if exe.exists():
            subprocess.Popen([str(exe)], cwd=str(exe.parent),
                             creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    except Exception:
        pass
    return jsonify(ok=True)


@app.post("/api/close")
def api_close():
    window = STATE.get("window")
    if window is not None:
        threading.Timer(0.4, lambda: window.destroy()).start()
    return jsonify(ok=True)


HTML = """
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
  :root{
    --ink:#1d1d1f; --ink2:#6e6e73; --ink3:#8e8e93; --line:rgba(0,0,0,.08);
    --bg:#f5f5f7; --card:#fff; --blue:#007aff; --green:#34c759;
    --ease:cubic-bezier(.22,.61,.36,1);
  }
  *{box-sizing:border-box}
  body{
    margin:0;height:100vh;background:var(--bg);color:var(--ink);overflow:hidden;
    font:13px/1.55 "SF Pro Text",-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
    -webkit-font-smoothing:antialiased;display:flex;flex-direction:column;
  }
  header{display:flex;align-items:center;gap:12px;padding:22px 26px 16px}
  .mark{
    width:46px;height:46px;border-radius:12px;background:var(--blue);flex:0 0 auto;
    display:flex;align-items:center;justify-content:center;
    box-shadow:0 8px 20px -8px rgba(0,122,255,.6);
  }
  .mark svg{width:24px;height:24px}
  header h1{margin:0;font-size:16px;font-weight:600;letter-spacing:-.01em}
  header p{margin:2px 0 0;font-size:11.5px;color:var(--ink2)}
  main{flex:1;padding:0 26px;overflow:auto}
  .pane{display:none;animation:fade .28s var(--ease)}
  .pane.on{display:block}
  @keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
  .card{background:var(--card);border-radius:12px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 6px 18px -8px rgba(0,0,0,.12);padding:16px 18px;margin-bottom:12px}
  .card h2{margin:0 0 10px;font-size:12.5px;font-weight:600;color:var(--ink2)}
  ul{margin:0;padding-left:18px;font-size:12.5px;color:var(--ink2);line-height:1.85}
  li::marker{color:var(--blue)}
  .path-row{display:flex;gap:8px}
  input[type=text]{flex:1;height:32px;padding:0 10px;border-radius:8px;border:1px solid var(--line);background:#fff;font:12.5px inherit;color:var(--ink)}
  input[type=text]:focus{outline:2px solid rgba(0,122,255,.22);border-color:var(--blue)}
  button{height:32px;padding:0 14px;border-radius:8px;border:1px solid var(--line);background:#fff;cursor:pointer;font:12.5px inherit;color:var(--ink);transition:background .16s,transform .1s,border-color .16s}
  button:hover{background:#fafafa;border-color:rgba(0,0,0,.16)}
  button:active{transform:scale(.97)}
  button:disabled{opacity:.45;cursor:not-allowed}
  button.primary{background:var(--blue);border-color:transparent;color:#fff;font-weight:500}
  button.primary:hover{background:#0069db}
  footer{padding:14px 26px 20px;display:flex;align-items:center;gap:10px}
  footer .spacer{flex:1}
  .tip{font-size:11.5px;color:var(--ink3)}
  .note{font-size:11.5px;color:var(--ink3);margin:8px 0 0;line-height:1.7}
  .warn{color:#c76a00}
  .mono{font-family:"Cascadia Mono",Consolas,monospace;font-size:11.5px;word-break:break-all;color:var(--ink);margin-top:4px}
  .kv{display:flex;justify-content:space-between;gap:12px;font-size:12.5px;color:var(--ink2);padding:2px 0}
  .kv b{color:var(--ink);font-weight:500}
  .kv span{min-width:0;overflow-wrap:anywhere}
  .card[hidden]{display:none}
  .progress{height:6px;border-radius:3px;background:rgba(0,0,0,.07);overflow:hidden;margin:14px 0 10px}
  .bar{height:100%;width:0;background:var(--blue);border-radius:3px;transition:width .25s var(--ease)}
  .status{font-size:12px;color:var(--ink2);min-height:18px}
  .done-mark{width:54px;height:54px;border-radius:50%;background:rgba(52,199,89,.12);display:flex;align-items:center;justify-content:center;margin:6px auto 14px}
  .done-mark svg{width:28px;height:28px;stroke:var(--green)}
  .center{text-align:center}
  .center h3{margin:0 0 6px;font-size:15px;font-weight:600}
  .center p{margin:0;font-size:12.5px;color:var(--ink2);word-break:break-all}
  label.chk{display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--ink);margin-top:14px}
  label.chk input{width:15px;height:15px;accent-color:var(--blue)}
</style></head>
<body>
  <header>
    <div class="mark">
      <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round">
        <path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v5h-5"/>
      </svg>
    </div>
    <div>
      <h1>超星 AI 答题</h1>
      <p>学习通任务助手 · v__VERSION__</p>
    </div>
  </header>

  <main>
    <section class="pane on" id="pane-welcome">
      <div class="card" id="card-exist" hidden>
        <h2>这台电脑上已经装过了</h2>
        <div class="kv"><span>已安装版本</span><b id="exist-version">—</b></div>
        <div class="mono" id="exist-path">—</div>
        <p class="note" id="exist-note">继续就是覆盖安装：程序文件换成新版本，本地账号、题库和配置都会保留。</p>
      </div>
      <div class="card">
        <h2>安装后会得到</h2>
        <ul>
          <li>自动播放与切节，视频看完自动进入下一小节</li>
          <li>AI 智能答题，支持任意 OpenAI 兼容模型</li>
          <li>共享题库，整卷全对才收录，答题前优先复用</li>
          <li>密码与 API Key 用 Windows 加密保存在本机</li>
        </ul>
      </div>
      <p class="note">安装位置将放在你的用户目录下，不需要管理员权限。</p>
    </section>

    <section class="pane" id="pane-path">
      <div class="card">
        <h2>安装位置</h2>
        <div class="path-row">
          <input type="text" id="path" spellcheck="false">
          <button id="browse">浏览…</button>
        </div>
        <p class="note" id="path-note">卸载时可以选择是否保留本地资料。</p>
      </div>
      <div class="card">
        <h2>将创建</h2>
        <ul>
          <li>桌面快捷方式</li>
          <li>开始菜单项</li>
          <li>系统「已安装的应用」中的卸载入口</li>
        </ul>
      </div>
    </section>

    <section class="pane" id="pane-install">
      <div class="card">
        <h2>正在安装</h2>
        <div class="progress"><div class="bar" id="bar"></div></div>
        <div class="status" id="status">准备中…</div>
        <p class="note">正在解压程序文件，请勿关闭窗口。</p>
      </div>
    </section>

    <section class="pane" id="pane-done">
      <div class="card center">
        <div class="done-mark">
          <svg viewBox="0 0 24 24" fill="none" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 6 9 17l-5-5"/>
          </svg>
        </div>
        <h3>安装完成</h3>
        <p id="done-path">已安装到默认位置</p>
        <label class="chk"><input type="checkbox" id="launch" checked>立即启动超星 AI 答题</label>
      </div>
    </section>
  </main>

  <footer>
    <span class="tip" id="tip"></span>
    <span class="spacer"></span>
    <button id="back" style="display:none">上一步</button>
    <button class="primary" id="next">下一步</button>
  </footer>

<script>
const $ = id => document.getElementById(id);
const PANES = ["pane-welcome", "pane-path", "pane-install", "pane-done"];
let step = 0;
let target = __DEFAULT_DIR__;
let polling = null;
let existing = {installed: false, version: "", location: ""};

async function detect() {
  try {
    const d = await fetch("/api/detect").then(r => r.json());
    existing = d;
    if (!d.installed) return;
    $("card-exist").hidden = false;
    $("exist-version").textContent = d.version ? ("v" + d.version) : "未知";
    $("exist-path").textContent = d.location || "—";
    if (d.location) {
      target = d.location;
      $("path").value = target;
    }
    $("path-note").textContent = "覆盖安装：程序文件换成新版本，本地账号、题库、配置都会保留。";
  } catch (e) {}
}

function show(i) {
  step = i;
  PANES.forEach((id, idx) => $(id).classList.toggle("on", idx === i));
  $("back").style.display = i === 1 ? "" : "none";
  $("next").style.display = i === 2 ? "none" : "";
  if (i === 0) { $("next").textContent = "下一步"; $("tip").textContent = "整个过程大约 30 秒"; }
  if (i === 1) { $("next").textContent = existing.installed ? "覆盖安装" : "安装";
    $("tip").textContent = existing.installed ? "会保留本地资料" : "确认位置后开始安装"; }
  if (i === 2) { $("tip").textContent = "安装中，请勿关闭窗口"; }
  if (i === 3) { $("next").textContent = "完成"; $("tip").textContent = ""; }
}

async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

$("path").value = target;
detect();

$("back").onclick = () => { if (step === 1) show(0); };

$("browse").onclick = async () => {
  $("path-note").classList.remove("warn");
  $("path-note").textContent = "正在打开文件夹选择…";
  try {
    const d = await post("/api/browse", {current: $("path").value.trim() || target});
    if (d.path) $("path").value = d.path;
    $("path-note").textContent = existing.installed
      ? "覆盖安装：程序文件换成新版本，本地账号、题库、配置都会保留。"
      : "卸载时可以选择是否保留本地资料。";
  } catch (e) {
    $("path-note").textContent = "无法打开文件夹选择，请直接手动输入路径";
    $("path-note").classList.add("warn");
  }
};

$("next").onclick = async () => {
  if (step === 0) { show(1); return; }

  if (step === 1) {
    target = $("path").value.trim();
    if (!target) { $("path-note").textContent = "请填写安装位置"; $("path-note").classList.add("warn"); return; }
    try {
      const res = await post("/api/install", {path: target});
      if (!res.ok) { $("path-note").textContent = res.error || "无法安装到该位置"; $("path-note").classList.add("warn"); return; }
    } catch (e) {
      $("path-note").textContent = "无法连接安装服务：" + (e && e.message ? e.message : e);
      $("path-note").classList.add("warn");
      return;
    }
    show(2);
    startPolling();
    return;
  }

  if (step === 3) {
    if ($("launch").checked) { try { await post("/api/launch", {path: target}); } catch (e) {} }
    try { await post("/api/close", {}); } catch (e) {}
  }
};

function startPolling() {
  if (polling) clearInterval(polling);
  polling = setInterval(async () => {
    try {
      const r = await fetch("/api/progress");
      const d = await r.json();
      $("bar").style.width = Math.max(0, Math.min(100, d.progress || 0)) + "%";
      if (d.message) $("status").textContent = d.message;
      if (d.error) {
        clearInterval(polling);
        $("status").textContent = d.error;
        $("status").classList.add("warn");
        $("next").disabled = false;
        $("next").textContent = "重试";
        $("next").style.display = "";
        show(2);
        return;
      }
      if (d.done) {
        clearInterval(polling);
        target = d.path || target;
        $("done-path").textContent = "已安装到 " + target;
        show(3);
      }
    } catch (e) { /* 轮询失败不打断 */ }
  }, 400);
}
</script>
</body></html>
"""


def silent_install(target: Path) -> int:
    try:
        install_files(target, lambda pct, msg: print(f"[{pct:3d}%] {msg}"))
    except Exception as exc:
        print(f"安装失败：{exc}")
        return 1
    print(f"installed: {target}")
    return 0


def do_uninstall() -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        ok = messagebox.askyesno("卸载", f"确定要卸载 {APP_DISPLAY} 吗？\n\n程序文件会被删除。")
        root.destroy()
        if not ok:
            return
    except Exception:
        pass

    target = Path(sys.executable).parent if getattr(sys, "frozen", False) else DEFAULT_DIR

    # 确认这是本程序的安装目录再删。uninstall.exe 被复制到别处运行时，
    # 没有这道检查就会把那个目录整个删掉。
    if not (target / EXE_NAME).exists():
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "卸载", f"这里不像安装目录，未做任何删除：\n{target}\n\n"
                        f"（目录里没有 {EXE_NAME}）")
            root.destroy()
        except Exception:
            pass
        return

    try:
        import winreg

        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except Exception:
        pass

    for link in (desktop_dir() / f"{APP_DISPLAY}.lnk", start_menu_dir() / f"{APP_DISPLAY}.lnk"):
        try:
            if link.exists():
                link.unlink()
        except Exception:
            pass

    subprocess.run(["taskkill", "/F", "/IM", EXE_NAME], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    import tempfile

    bat = Path(tempfile.gettempdir()) / "chaoxing_uninstall.bat"
    bat.write_text(
        "@echo off\r\ntimeout /t 2 /nobreak >nul\r\n"
        f'rmdir /s /q "{target}"\r\n'
        'del "%~f0"\r\n',
        encoding="gbk",
    )
    subprocess.Popen(["cmd", "/c", str(bat)],
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    if "--uninstall" in sys.argv:
        do_uninstall()
        return 0

    if "--silent" in sys.argv:
        idx = sys.argv.index("--silent")
        dest = Path(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else DEFAULT_DIR
        return silent_install(dest)

    import webview

    # --windowed 打包后 stdout/stderr 可能为 None，Flask 写日志会直接抛错
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    port = free_port()

    def serve() -> None:
        try:
            app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)
        except Exception:
            import tempfile
            import traceback

            try:
                Path(tempfile.gettempdir(), "chaoxing_installer_error.log").write_text(
                    traceback.format_exc(), encoding="utf-8"
                )
            except Exception:
                pass

    threading.Thread(target=serve, daemon=True).start()
    time.sleep(0.9)

    window = webview.create_window(
        f"安装 {APP_DISPLAY}",
        f"http://127.0.0.1:{port}/",
        width=620,
        height=580,
        resizable=False,
        background_color="#f5f5f7",
        text_select=False,
    )
    STATE["window"] = window
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())