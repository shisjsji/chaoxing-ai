"""把 dist 里的程序、Rust 卸载器/更新器、version.txt 组装成 payload.zip。

发版流程里的第二步（第一步是 `pyinstaller 超星AI答题.spec`）：
    python pack_release.py
版本号取自 api/updater.py 的 APP_VERSION，避免打包和程序内版本对不上。
"""

import hashlib
import os
import re
import shutil
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "dist", "超星AI答题")
STAGE = os.path.join(ROOT, "release", "setup", "payload_src")
ZIP = os.path.join(ROOT, "release", "setup", "payload.zip")


def current_version() -> str:
    text = open(os.path.join(ROOT, "api", "updater.py"), encoding="utf-8").read()
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("读不到 api/updater.py 里的 APP_VERSION")
    return match.group(1)


VERSION = current_version()

if not os.path.isdir(APP):
    raise SystemExit("先跑 pyinstaller 超星AI答题.spec 生成 dist/超星AI答题")

shutil.rmtree(STAGE, ignore_errors=True)
os.makedirs(STAGE)

for name in os.listdir(APP):
    src = os.path.join(APP, name)
    dst = os.path.join(STAGE, name)
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)

shutil.copy2(os.path.join(ROOT, "rust", "setup", "target", "release", "chaoxing-uninstall.exe"),
             os.path.join(STAGE, "卸载.exe"))
shutil.copy2(os.path.join(ROOT, "rust", "updater", "target", "release", "chaoxing-updater.exe"),
             os.path.join(STAGE, "chaoxing-updater.exe"))
with open(os.path.join(STAGE, "version.txt"), "w", encoding="utf-8", newline="\n") as fp:
    fp.write(VERSION)

with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for base, _dirs, files in os.walk(STAGE):
        for f in files:
            full = os.path.join(base, f)
            zf.write(full, os.path.relpath(full, STAGE))

digest = hashlib.sha256()
with open(ZIP, "rb") as fp:
    for chunk in iter(lambda: fp.read(1024 * 1024), b""):
        digest.update(chunk)

print("版本", VERSION)
print("payload.zip", round(os.path.getsize(ZIP) / 1024 / 1024, 1), "MB")
print("sha256", digest.hexdigest())