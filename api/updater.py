"""程序内更新：检查版本、下载更新包、替换并重启。

替换由独立的 PowerShell 脚本在主程序退出后完成。脚本用 UTF-8 BOM 保存，
避免批处理在 chcp 65001 下把中文安装路径解成乱码（历史 bug：更新包被解压
到乱码目录，正式目录纹丝不动）。

更新包先解压到临时目录，校验关键文件齐全后才与正式目录交换；交换失败会
回滚到原版本。任何一步出错都不会留下半新半旧、启动不了的状态。
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import requests

from api.logger import logger

APP_VERSION = "3.7.6"
APP_NAME = "超星AI答题"

CHECK_TIMEOUT = 8

# 解压结果里必须存在的文件，缺任何一个都说明包是坏的
REQUIRED_ENTRIES = ("_internal/python312.dll", "_internal/base_library.zip")

# 更新失败时写在程序目录里的说明文件，下次启动由界面读出来提示
FAILURE_NOTE = "update_failed.txt"

LOG_PATH = os.path.join(tempfile.gettempdir(), "chaoxing_update.log")
PS1_PATH = os.path.join(tempfile.gettempdir(), "chaoxing_update.ps1")

# 内置更新日志：断网时也能在界面里看到本版改了什么。
# 新增版本时同步更新 CHANGELOG.md 与服务端 version.json 的 highlights。
RELEASES = {
    "3.7.6": [
        "修复窄窗口下日志面板完全不可见：改成右侧抽屉，点「日志」就能拉出来",
        "修复课程列表为空会刷全部课程：现在会明确问你确认",
        "「开始运行」缺配置时自动切到设置页，不再只丢一句报错",
        "并发章节数不再被课程数量覆盖，标签改成「同时处理的章节数」",
        "设置页支持 Enter 保存，改动后顶部出现未保存提示条",
        "答题改为等页面就绪再操作，不再靠固定延时（点空的根因）",
        "全部题目答完后自动交卷；有未答的题不交，避免白卷",
        "刷课进行中不再允许启动考试，两套流程互斥",
        "清空日志加确认，两个「刷新」按钮改为「刷新本地 / 刷新共享」",
        "移除任意 JS 执行入口，废弃的考试通道改为明确报错",
    ],
    "3.7.5": [
        "答题全程改为等页面就绪再操作，不再靠固定延时（这是点空的根因）",
        "点完选项轮询等答案真正写进字段，慢页面也不会漏",
        "跳题后等题目区渲染完成再读，减少读空",
    ],
    "3.7.4": [
        "点击选项补齐左键事件标识，点击命中率更高",
        "主观题现在也能自动填写（以前只能跳过留空）",
        "修复取消勾选的课程仍被执行：手填框被历史课程回填，导致永远取消不掉",
        "修复正在进行的任务没有置顶：现在运行中的章节一律排在最前",
        "没点「获取课程」就保存时，沿用手上已有的课程，不会把选好的课清空",
    ],
    "3.7.3": [
        "考试入口只剩一条路：点「开始/重考」直接打开考试窗口，你过验证码，程序自动答题交卷",
        "每次点完选项都回读确认，没写进去自动重点，不再出现点空了还往下翻",
        "判断题按 true/false 正确匹配（之前因此漏答大量题目）",
        "按答题卡复查，漏答的题逐个跳过去补上，跑完还会再核对一遍",
        "答过的题自动进题库，题库开始真正被复用（以前只进不出）",
        "界面动画恢复：切页、列表逐行、日志、运行状态呼吸点",
        "官网加了 3D 粒子背景，换上新图标",
    ],
    "3.7.2": [
        "考试改成一键跑完：自动开始、逐题作答、最后交卷，日志实时滚动",
        "修复提交把整卷交掉：逐题提交现在是暂存，只有最后才交卷",
        "补齐试卷页上的 paperId、openc 等字段，不再报「无效操作」",
        "支持重考：已交卷且允许重考的场次，点按钮重新自动答一遍",
        "修复开考接口：平台返回 302 跳转，enc 签名参数现在能正确拿到",
        "主观题会生成参考答案并存成文件，程序仍不替你提交这类题",
        "刷课答题也支持思考强度，模型不支持时自动降级",
        "重装不再清空安装目录，题库、cookie 和配置都会保留",
    ],
    "3.7.1": [
        "修复热更新失败：中文安装路径被编码打坏，更新包解压到了乱码目录",
        "更新改为校验后再替换，失败自动回滚，不再出现启动不了的半成品",
        "修复考试点开始报「未能解析考试信息」（漏传 classId 和 cpi）",
        "启动后自动检查新版本，弹窗列出更新内容",
    ],
    "3.7.0": [
        "新增考试自动答题，题库优先、模型兜底，答完自动交卷",
        "支持题型扩展到 8 种，主观题跳过并提示手动作答",
        "界面重构为三栏布局，补齐加载、空、错误、禁用状态",
    ],
}


def changelog(version: str = "") -> list:
    """取某个版本的更新要点，缺省为当前版本。"""
    return list(RELEASES.get(version or APP_VERSION, []))


def current_version() -> str:
    return APP_VERSION


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.getcwd()


def _request_once(url: str, **kwargs):
    """带重试的 GET，缓解 DNS 缓存与链路抖动。"""
    from api.remote_library import friendly_error

    last = None
    for attempt in range(3):
        try:
            return requests.get(url, **kwargs)
        except requests.exceptions.RequestException as exc:
            last = exc
            if attempt < 2:
                time.sleep(1.0 + attempt)
    return {"__error__": friendly_error(last)}


def check_update(endpoint: str, token: str = "") -> dict:
    """查询服务端最新版本。

    返回 {ok, has_update, version, current, url, sha256, notes, highlights, error}。
    """
    endpoint = (endpoint or "").rstrip("/")
    if not endpoint:
        return {"ok": False, "error": "未配置更新地址"}
    headers = {"X-Token": token} if token else {}
    try:
        resp = _request_once(endpoint + "/api/v1/latest", headers=headers, timeout=CHECK_TIMEOUT)
        if isinstance(resp, dict):
            return {"ok": False, "error": resp["__error__"]}
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        from api.remote_library import friendly_error

        return {"ok": False, "error": friendly_error(exc)}

    latest = str(data.get("version", "")).strip()
    if not latest:
        return {"ok": False, "error": "服务端未返回版本号"}

    highlights = data.get("highlights") or []
    if not isinstance(highlights, list):
        highlights = []
    highlights = [str(x) for x in highlights][:8] or changelog(latest)

    return {
        "ok": True,
        "has_update": _version_tuple(latest) > _version_tuple(APP_VERSION),
        "version": latest,
        "current": APP_VERSION,
        "url": data.get("url", ""),
        "sha256": data.get("sha256", ""),
        "notes": data.get("notes", ""),
        "highlights": highlights,
        "current_changelog": changelog(),
        "last_failure": pending_failure(),
    }


def _version_tuple(text: str) -> tuple:
    parts = []
    for piece in str(text).replace("-", ".").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def inspect_package(path: str) -> list:
    """检查更新包结构，返回问题列表；空列表表示可用。"""
    problems = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = {n.replace("\\", "/") for n in zf.namelist()}
    except Exception as exc:
        return [f"无法读取更新包：{exc}"]

    if not any(n.lower().endswith(".exe") for n in names):
        problems.append("包里没有主程序 exe")
    for entry in REQUIRED_ENTRIES:
        if entry not in names:
            problems.append(f"包里缺少 {entry}")
    return problems


def download(url: str, sha256: str = "", progress=None) -> str:
    """下载更新包到临时目录，校验后返回路径。失败抛异常。"""
    if not url:
        raise ValueError("缺少下载地址")
    target = os.path.join(tempfile.gettempdir(), "chaoxing_update.zip")

    free = shutil.disk_usage(tempfile.gettempdir()).free
    if free < 400 * 1024 * 1024:
        raise ValueError(f"系统盘剩余空间不足（{free // 1024 // 1024} MB），无法下载更新包")

    digest = hashlib.sha256()
    total = 0
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(target, "wb") as fp:
            for chunk in resp.iter_content(1024 * 256):
                if not chunk:
                    continue
                fp.write(chunk)
                digest.update(chunk)
                total += len(chunk)
                if progress and total % (1024 * 1024) < 1024 * 256:
                    progress(total)

    if sha256 and digest.hexdigest().lower() != sha256.lower():
        try:
            os.remove(target)
        except OSError:
            pass
        raise ValueError("更新包校验失败（sha256 不匹配）")

    problems = inspect_package(target)
    if problems:
        try:
            os.remove(target)
        except OSError:
            pass
        raise ValueError("更新包不完整：" + "；".join(problems))

    logger.info(f"更新包已下载并通过校验: {target} ({total // 1024} KB)")
    return target


def _ps_quote(text: str) -> str:
    """PowerShell 单引号字符串里的单引号要写成两个。"""
    return str(text).replace("'", "''")


def _build_script(zip_path: str, app_dir: str, exe_name: str) -> str:
    app = _ps_quote(app_dir)
    zip_q = _ps_quote(zip_path)
    exe = _ps_quote(exe_name)
    note = _ps_quote(FAILURE_NOTE)
    log = _ps_quote(LOG_PATH)
    process = _ps_quote(os.path.splitext(exe_name)[0])

    return f"""$ErrorActionPreference = 'Stop'
$log = '{log}'
function Note($m) {{ try {{ "$(Get-Date -Format 'HH:mm:ss')  $m" | Out-File -LiteralPath $log -Append -Encoding utf8 }} catch {{}} }}

$app  = '{app}'
$zip  = '{zip_q}'
$exe  = '{exe}'
$proc = '{process}'
$note = Join-Path $app '{note}'
$tmp  = Join-Path $app '_update_tmp'
$old  = Join-Path $app '_internal_old'
$dest = Join-Path $app '_internal'
$exePath = Join-Path $app $exe

Note '=== 更新开始 ==='

try {{
    # 1) 等主程序（含子进程）彻底退出
    for ($i = 0; $i -lt 40; $i++) {{
        $p = Get-Process -Name $proc -ErrorAction SilentlyContinue
        if (-not $p) {{ break }}
        $p | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 400
    }}
    Start-Sleep -Seconds 1
    if (Get-Process -Name $proc -ErrorAction SilentlyContinue) {{
        throw '主程序仍在运行，无法替换文件'
    }}
    Note '主程序已退出'

    # 2) 解压到临时目录（与正式目录同盘，交换是改名操作，很快）
    if (Test-Path -LiteralPath $tmp) {{ Remove-Item -LiteralPath $tmp -Recurse -Force }}
    Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
    Note '解压完成'

    # 3) 校验解压结果
    $newExe  = Join-Path $tmp $exe
    $newDll  = Join-Path $tmp '_internal\\python312.dll'
    $newBase = Join-Path $tmp '_internal\\base_library.zip'
    $newDir  = Join-Path $tmp '_internal'
    foreach ($f in @($newExe, $newDll, $newBase)) {{
        if (-not (Test-Path -LiteralPath $f)) {{ throw "解压结果缺少 $f" }}
    }}
    Note '校验通过'

    # 4) 备份旧目录后交换，失败则回滚
    if (Test-Path -LiteralPath $old) {{ Remove-Item -LiteralPath $old -Recurse -Force }}
    Move-Item -LiteralPath $dest -Destination $old -Force
    try {{
        Move-Item -LiteralPath $newDir -Destination $dest -Force
        Copy-Item -LiteralPath $newExe -Destination $exePath -Force
    }} catch {{
        Note "替换失败，回滚中: $_"
        if (Test-Path -LiteralPath $dest) {{ Remove-Item -LiteralPath $dest -Recurse -Force }}
        Move-Item -LiteralPath $old -Destination $dest -Force
        throw
    }}
    Note '文件已替换'

    # 5) 清理并重启
    Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $note) {{ Remove-Item -LiteralPath $note -Force -ErrorAction SilentlyContinue }}
    Note '更新成功，重新启动'
    Start-Process -FilePath $exePath -WorkingDirectory $app
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}}
catch {{
    Note "更新失败: $_"
    try {{
        "更新失败，已保留原版本。`r`n时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`r`n原因：$_`r`n详细日志：$log" |
            Out-File -LiteralPath $note -Encoding utf8
        if (Test-Path -LiteralPath $tmp) {{ Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }}
        Start-Process -FilePath $exePath -WorkingDirectory $app
    }} catch {{}}
}}
"""


def apply_and_restart(zip_path: str) -> bool:
    """生成独立更新脚本：等主程序退出后校验、替换并重启。"""
    if not getattr(sys, "frozen", False):
        logger.warning(f"开发模式下不执行自动替换，更新包位于: {zip_path}")
        return False

    problems = inspect_package(zip_path)
    if problems:
        raise ValueError("更新包不完整：" + "；".join(problems))

    app_dir = base_dir()
    exe_name = os.path.basename(sys.executable)

    free = shutil.disk_usage(app_dir).free
    if free < 900 * 1024 * 1024:
        raise ValueError(f"程序所在磁盘剩余空间不足（{free // 1024 // 1024} MB），"
                         "解压需要约 900 MB")

    script = _build_script(zip_path, app_dir, exe_name)
    # UTF-8 BOM 是关键：PowerShell 靠 BOM 识别中文路径，缺了会按 ANSI 解析成乱码
    with open(PS1_PATH, "w", encoding="utf-8-sig", newline="\r\n") as fp:
        fp.write(script)

    try:
        with open(LOG_PATH, "w", encoding="utf-8") as fp:
            fp.write("")
    except OSError:
        pass

    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-WindowStyle", "Hidden", "-File", PS1_PATH],
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0),
        close_fds=True,
    )
    logger.info("已启动更新脚本，程序即将退出并重启")
    return True


def pending_failure() -> str:
    """上次更新失败留下的说明；没有则返回空串。"""
    path = os.path.join(base_dir(), FAILURE_NOTE)
    try:
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fp:
            text = fp.read().strip()
        os.remove(path)
        return text
    except OSError:
        return ""