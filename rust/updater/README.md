# 更新器（Rust）

主程序在退出前会优先拉起这个程序做文件替换；找不到它时回退到 `api/updater.py` 生成的 PowerShell 脚本。

## 构建

```powershell
cd rust/updater
cargo build --release
```

产物是 `target/release/chaoxing-updater.exe`，约 330 KB。只用标准库，构建不需要联网拉依赖。

## 放进安装目录

打包时复制到 `dist/超星AI答题/` 根目录，和主程序、`卸载.exe` 并排：

```powershell
Copy-Item rust/updater/target/release/chaoxing-updater.exe dist/超星AI答题/ -Force
```

## 参数

```
chaoxing-updater.exe --pid <主程序PID> --app <安装目录> --exe <主程序名>
                     --stage <已解压的新版目录> --log <日志文件>
```

新版由主程序自己解压到 `_update_tmp`（界面上有进度），这个程序只做「等主程序退出 → 校验 →
备份旧目录 → 换入新版 → 起新进程」，所以替换只要一两秒。

## 日志与失败处理

每一步都写进 `--log` 指定的文件。任何一步失败都会回滚到旧版、写 `update_failed.txt`，
下次启动时主程序会弹窗告知。目录被残留进程占用时会等 2 秒重试，最多 6 次。