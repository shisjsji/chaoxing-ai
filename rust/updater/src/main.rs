//! 更新器：等主程序退出后，把已经解压好的新版换上去并重新启动。
//!
//! 由主程序在退出前拉起（`api/updater.py` 优先用这个，找不到才回退到 PowerShell 脚本）。
//! 之所以用 Rust 而不是 PowerShell：不依赖系统的脚本执行策略、不会被杀软拦在
//! `.ps1` 上，出错时能明确回滚并写清原因。
//!
//! 只用标准库，不引第三方 crate：产物小，构建也不需要联网。
//!
//! 用法：
//!   chaoxing-updater.exe --pid <主程序PID> --app <安装目录> --exe <主程序名>
//!                        --stage <已解压的新版目录> --log <日志文件>

#![windows_subsystem = "windows"]

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread::sleep;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[link(name = "kernel32")]
extern "system" {
    fn OpenProcess(desired_access: u32, inherit: i32, pid: u32) -> isize;
    fn WaitForSingleObject(handle: isize, millis: u32) -> u32;
    fn TerminateProcess(handle: isize, code: u32) -> i32;
    fn CloseHandle(handle: isize) -> i32;
}

const SYNCHRONIZE: u32 = 0x0010_0000;
const PROCESS_TERMINATE: u32 = 0x0001;
const WAIT_TIMEOUT: u32 = 258;
const LOCK_NAME: &str = "update.lock";

struct Args {
    pid: u32,
    app: PathBuf,
    exe: String,
    stage: PathBuf,
    log: PathBuf,
}

fn parse_args() -> Option<Args> {
    let mut pid: u32 = 0;
    let mut app = PathBuf::new();
    let mut exe = String::new();
    let mut stage = PathBuf::new();
    let mut log = PathBuf::new();

    let argv: Vec<String> = env::args().collect();
    let mut i = 1;
    while i + 1 < argv.len() {
        let value = argv[i + 1].clone();
        match argv[i].as_str() {
            "--pid" => pid = value.parse().unwrap_or(0),
            "--app" => app = PathBuf::from(value),
            "--exe" => exe = value,
            "--stage" => stage = PathBuf::from(value),
            "--log" => log = PathBuf::from(value),
            _ => {}
        }
        i += 2;
    }
    if app.as_os_str().is_empty() || exe.is_empty() || stage.as_os_str().is_empty() {
        return None;
    }
    Some(Args { pid, app, exe, stage, log })
}

fn stamp() -> String {
    // 只用东八区的小时分钟秒，够和主程序日志对时间；不引时区库
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
    let local = now.as_secs() + 8 * 3600;
    format!("{:02}:{:02}:{:02}", (local / 3600) % 24, (local / 60) % 60, local % 60)
}

fn note(log: &Path, message: &str) {
    let line = format!("{}  {}\n", stamp(), message);
    let _ = fs::OpenOptions::new().create(true).append(true).open(log).and_then(|mut f| {
        use std::io::Write;
        f.write_all(line.as_bytes())
    });
}

fn process_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    let handle = unsafe { OpenProcess(SYNCHRONIZE, 0, pid) };
    if handle == 0 {
        return false;
    }
    let code = unsafe { WaitForSingleObject(handle, 0) };
    unsafe { CloseHandle(handle) };
    code == WAIT_TIMEOUT
}

fn kill(pid: u32) {
    if pid == 0 {
        return;
    }
    let handle = unsafe { OpenProcess(PROCESS_TERMINATE, 0, pid) };
    if handle != 0 {
        unsafe {
            TerminateProcess(handle, 1);
            CloseHandle(handle);
        }
    }
}

/// 等主程序彻底退出。它通常会自己退，这里只是兜底。
fn wait_exit(pid: u32, log: &Path) {
    for _ in 0..80 {
        if !process_alive(pid) {
            return;
        }
        sleep(Duration::from_millis(500));
    }
    note(log, "主程序没有按时退出，强制结束");
    kill(pid);
    sleep(Duration::from_millis(800));
}

/// 目录改名偶尔会被残留进程占着（杀软扫描、WebView2 收尾），等几秒再试。
fn rename_with_retry(from: &Path, to: &Path, log: &Path, what: &str) -> Result<(), String> {
    let mut last = String::new();
    for attempt in 1..=6 {
        match fs::rename(from, to) {
            Ok(()) => return Ok(()),
            Err(exc) => {
                last = exc.to_string();
                note(log, &format!("{}第 {} 次失败：{}", what, attempt, last));
                sleep(Duration::from_secs(2));
            }
        }
    }
    Err(format!("{}失败：{}", what, last))
}

fn run(args: &Args) -> Result<(), String> {
    let log = &args.log;
    let dest = args.app.join("_internal");
    let old = args.app.join("_internal_old");
    let staged_internal = args.stage.join("_internal");
    let staged_exe = args.stage.join(&args.exe);
    let exe_path = args.app.join(&args.exe);

    note(log, "=== 更新开始（Rust 更新器）===");
    wait_exit(args.pid, log);
    note(log, "主程序已退出");

    for required in [&staged_exe, &staged_internal.join("python312.dll"),
                     &staged_internal.join("base_library.zip")] {
        if !required.exists() {
            return Err(format!("准备好的文件不完整，缺少 {}", required.display()));
        }
    }
    note(log, "校验通过");

    if old.exists() {
        fs::remove_dir_all(&old).map_err(|e| format!("清理上次的备份失败：{e}"))?;
    }
    rename_with_retry(&dest, &old, log, "备份旧目录")?;
    if let Err(exc) = fs::rename(&staged_internal, &dest)
        .and_then(|_| fs::copy(&staged_exe, &exe_path).map(|_| ()))
    {
        note(log, &format!("替换失败，回滚中：{exc}"));
        let _ = fs::remove_dir_all(&dest);
        let _ = fs::rename(&old, &dest);
        return Err(format!("替换失败：{exc}"));
    }
    note(log, "文件已替换");

    // 卸载入口跟着换：包里带 卸载.exe 就一起更新
    let staged_un = args.stage.join("卸载.exe");
    if staged_un.exists() {
        let _ = fs::copy(&staged_un, args.app.join("卸载.exe"));
        note(log, "卸载入口已更新");
    }

    Ok(())
}

fn cleanup(args: &Args) {
    let _ = fs::remove_dir_all(args.app.join("_internal_old"));
    let _ = fs::remove_dir_all(&args.stage);
    let _ = fs::remove_file(args.app.join("update_pending.json"));
    // 先解锁再启动：新进程看到锁会以为还在更新，直接退出
    let _ = fs::remove_file(args.app.join(LOCK_NAME));
}

fn main() {
    let Some(args) = parse_args() else { return };
    let exe_path = args.app.join(&args.exe);

    let result = run(&args);
    match &result {
        Ok(()) => note(&args.log, "更新成功，重新启动"),
        Err(exc) => {
            note(&args.log, &format!("更新失败：{}", exc));
            let failure = format!(
                "更新失败，已保留原版本。\n时间：{}\n原因：{}\n详细日志：{}",
                stamp(),
                exc,
                args.log.display()
            );
            let _ = fs::write(args.app.join("update_failed.txt"), failure);
        }
    }
    cleanup(&args);

    if exe_path.exists() {
        let _ = Command::new(&exe_path).current_dir(&args.app).spawn();
    }
    drop(result);
}