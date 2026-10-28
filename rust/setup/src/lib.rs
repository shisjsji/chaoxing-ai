//! 安装器与卸载器共用的部分：路径、注册表、快捷方式、解压自带安装包。
//!
//! 行为要和原来的 Python 安装器保持一致，这样两种安装方式装出来的结果可以互换。

use std::ffi::OsStr;
use std::fs::{self, File};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use windows_sys::Win32::System::Registry::{
    RegCloseKey, RegCreateKeyExW, RegDeleteTreeW, RegSetValueExW, HKEY, HKEY_CURRENT_USER,
    KEY_WRITE, REG_DWORD, REG_SZ,
};

pub const APP_NAME: &str = "超星AI答题";
pub const APP_DISPLAY: &str = "超星 AI 答题";
pub const EXE_NAME: &str = "超星AI答题.exe";
pub const UNINSTALLER_NAME: &str = "卸载.exe";
pub const UPDATER_NAME: &str = "chaoxing-updater.exe";
pub const LEGACY_UNINSTALLER: &str = "uninstall.exe";
pub const REG_SUBKEY: &str = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\超星AI答题";

/// 程序自身的文件：覆盖安装时先删这些，其余（config.ini、题库等）留着
pub const VERSION_FILE: &str = "version.txt";
pub const PROGRAM_ITEMS: [&str; 6] = [
    "_internal",
    EXE_NAME,
    UNINSTALLER_NAME,
    LEGACY_UNINSTALLER,
    UPDATER_NAME,
    VERSION_FILE,
];

pub fn wide(text: &str) -> Vec<u16> {
    OsStr::new(text).encode_wide().chain(std::iter::once(0)).collect()
}

pub fn log(message: &str) {
    let path = std::env::temp_dir().join("chaoxing_setup.log");
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
    let local = now.as_secs() + 8 * 3600;
    let line = format!(
        "{:02}:{:02}:{:02}  {}\n",
        (local / 3600) % 24,
        (local / 60) % 60,
        local % 60,
        message
    );
    let _ = fs::OpenOptions::new().create(true).append(true).open(path).and_then(|mut f| {
        f.write_all(line.as_bytes())
    });
}

pub fn local_app_data() -> PathBuf {
    std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("C:\\"))
}

pub fn default_dir() -> PathBuf {
    local_app_data().join("Programs").join(APP_NAME)
}

pub fn desktop_dir() -> PathBuf {
    std::env::var_os("USERPROFILE")
        .map(|p| PathBuf::from(p).join("Desktop"))
        .unwrap_or_else(local_app_data)
}

pub fn start_menu_dir() -> PathBuf {
    std::env::var_os("APPDATA")
        .map(|p| PathBuf::from(p).join("Microsoft\\Windows\\Start Menu\\Programs"))
        .unwrap_or_else(local_app_data)
}

/// 装 App 目录时用：结束正在运行的程序，否则文件被占用改不动
pub fn kill_app() {
    let _ = Command::new("taskkill")
        .args(["/F", "/IM", EXE_NAME])
        .creation_flags_no_window()
        .output();
}

trait NoWindow {
    fn creation_flags_no_window(&mut self) -> &mut Self;
}

impl NoWindow for Command {
    fn creation_flags_no_window(&mut self) -> &mut Self {
        use std::os::windows::process::CommandExt;
        self.creation_flags(0x0800_0000) // CREATE_NO_WINDOW
    }
}

/// 用 WScript.Shell 建快捷方式。失败不算错误：只是少一个入口，不影响使用。
pub fn make_shortcut(link: &Path, target: &Path) {
    let script = format!(
        "$W = New-Object -ComObject WScript.Shell;$S = $W.CreateShortcut('{}');\
         $S.TargetPath = '{}';$S.WorkingDirectory = '{}';$S.IconLocation = '{}';$S.Save()",
        quote(link),
        quote(target),
        quote(target.parent().unwrap_or(target)),
        quote(target)
    );
    let _ = Command::new("powershell")
        .args(["-NoProfile", "-WindowStyle", "Hidden", "-Command", &script])
        .creation_flags_no_window()
        .output();
}

fn quote(path: &Path) -> String {
    path.to_string_lossy().replace('\'', "''")
}

fn set_string(key: HKEY, name: &str, value: &str) {
    let data: Vec<u16> = OsStr::new(value).encode_wide().chain(std::iter::once(0)).collect();
    unsafe {
        RegSetValueExW(
            key,
            wide(name).as_ptr(),
            0,
            REG_SZ,
            data.as_ptr() as *const u8,
            (data.len() * 2) as u32,
        );
    }
}

fn set_dword(key: HKEY, name: &str, value: u32) {
    unsafe {
        RegSetValueExW(
            key,
            wide(name).as_ptr(),
            0,
            REG_DWORD,
            &value as *const u32 as *const u8,
            4,
        );
    }
}

/// 登记「已安装的应用」里的卸载入口，字段和 Python 安装器写的一致
pub fn write_registry(app_dir: &Path, version: &str, uninstaller: &Path, app_exe: &Path) {
    unsafe {
        let mut key: HKEY = std::ptr::null_mut();
        let mut disposition = 0u32;
        let rc = RegCreateKeyExW(
            HKEY_CURRENT_USER,
            wide(REG_SUBKEY).as_ptr(),
            0,
            std::ptr::null(),
            0,
            KEY_WRITE,
            std::ptr::null(),
            &mut key,
            &mut disposition,
        );
        if rc != 0 {
            log(&format!("写注册表失败，返回码 {rc}"));
            return;
        }
        let quoted = format!("\"{}\"", uninstaller.display());
        set_string(key, "DisplayName", APP_DISPLAY);
        set_string(key, "DisplayVersion", version);
        set_string(key, "Publisher", "lingluandf");
        set_string(key, "InstallLocation", &app_dir.display().to_string());
        set_string(key, "UninstallString", &quoted);
        set_string(key, "QuietUninstallString", &format!("{quoted} --silent"));
        set_string(
            key,
            "DisplayIcon",
            &if app_exe.exists() {
                app_exe.display().to_string()
            } else {
                uninstaller.display().to_string()
            },
        );
        let today = {
            let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
            let days = now.as_secs() / 86400;
            // 只用于显示安装日期，粗略按 1970 起算的日数换算
            let (y, m, d) = civil_from_days(days as i64);
            format!("{y:04}{m:02}{d:02}")
        };
        set_string(key, "InstallDate", &today);
        let size_kb = dir_size(&app_dir.join("_internal")) + fs::metadata(app_exe).map(|m| m.len()).unwrap_or(0);
        set_dword(key, "EstimatedSize", (size_kb / 1024).max(1) as u32);
        set_dword(key, "NoModify", 1);
        set_dword(key, "NoRepair", 1);
        RegCloseKey(key);
    }
}

pub fn remove_shortcuts() {
    for link in [
        desktop_dir().join(format!("{APP_DISPLAY}.lnk")),
        start_menu_dir().join(format!("{APP_DISPLAY}.lnk")),
    ] {
        let _ = fs::remove_file(link);
    }
}

pub fn delete_registry() {
    unsafe {
        RegDeleteTreeW(HKEY_CURRENT_USER, wide(REG_SUBKEY).as_ptr());
    }
}

/// 1970-01-01 起的天数 → (年, 月, 日)
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

pub fn dir_size(path: &Path) -> u64 {
    let mut total = 0;
    if let Ok(entries) = fs::read_dir(path) {
        for entry in entries.flatten() {
            let meta = match entry.metadata() {
                Ok(m) => m,
                Err(_) => continue,
            };
            total += if meta.is_dir() {
                dir_size(&entry.path())
            } else {
                meta.len()
            };
        }
    }
    total
}

/// 只删程序文件，用户资料原地留下
pub fn remove_program_files(app_dir: &Path) {
    for item in PROGRAM_ITEMS {
        let path = app_dir.join(item);
        let _ = if path.is_dir() {
            fs::remove_dir_all(&path)
        } else {
            fs::remove_file(&path)
        };
    }
    log("程序文件已删除，本地资料保留");
}

pub fn remove_everything(app_dir: &Path) {
    let _ = fs::remove_dir_all(app_dir);
    // 程序内 WebView2 的配置目录（考场登录缓存）在 LOCALAPPDATA 下，不在安装目录里
    let _ = fs::remove_dir_all(local_app_data().join(APP_NAME));
    log("程序与本地资料已全部删除");
}

// ---------------------------------------------------------------- 自带安装包

/// 安装包被追加在自己的 exe 末尾：`[exe][payload.zip][u64 长度]`
pub struct Payload {
    file: File,
    start: u64,
    len: u64,
}

impl Payload {
    pub fn open_self() -> io::Result<Self> {
        let path = std::env::current_exe()?;
        let mut file = File::open(&path)?;
        let total = file.metadata()?.len();
        if total < 8 {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "文件不完整"));
        }
        file.seek(SeekFrom::End(-8))?;
        let mut buf = [0u8; 8];
        file.read_exact(&mut buf)?;
        let len = u64::from_le_bytes(buf);
        if len == 0 || len + 8 > total {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "这个 exe 里没有附带安装包",
            ));
        }
        Ok(Payload { file, start: total - 8 - len, len })
    }

    pub fn reader(&self) -> io::Result<SliceReader> {
        let mut file = self.file.try_clone()?;
        file.seek(SeekFrom::Start(self.start))?;
        Ok(SliceReader { file, start: self.start, len: self.len, pos: 0 })
    }
}

/// 把文件的一段当成独立的流，zip 解析就不必把 183MB 全读进内存
pub struct SliceReader {
    file: File,
    start: u64,
    len: u64,
    pos: u64,
}

impl Read for SliceReader {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if self.pos >= self.len {
            return Ok(0);
        }
        let want = ((self.len - self.pos) as usize).min(buf.len());
        self.file.seek(SeekFrom::Start(self.start + self.pos))?;
        let read = self.file.read(&mut buf[..want])?;
        self.pos += read as u64;
        Ok(read)
    }
}

impl Seek for SliceReader {
    fn seek(&mut self, from: SeekFrom) -> io::Result<u64> {
        let target = match from {
            SeekFrom::Start(p) => p as i64,
            SeekFrom::End(p) => self.len as i64 + p,
            SeekFrom::Current(p) => self.pos as i64 + p,
        };
        if target < 0 {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "越界"));
        }
        self.pos = target as u64;
        Ok(self.pos)
    }
}

/// 解压自带的安装包到目标目录，每完成一部分调用一次 progress(已完成, 总数)
pub fn extract_payload(payload: &Payload, target: &Path, progress: &dyn Fn(u64, u64)) -> Result<(), String> {
    let reader = payload.reader().map_err(|e| format!("读取安装包失败：{e}"))?;
    let mut archive = zip::ZipArchive::new(reader).map_err(|e| format!("安装包损坏：{e}"))?;
    let total = archive.len() as u64;
    fs::create_dir_all(target).map_err(|e| format!("创建安装目录失败：{e}"))?;

    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|e| format!("解压第 {index} 个文件失败：{e}"))?;
        let Some(rel) = entry.enclosed_name() else {
            continue;
        };
        let dest = target.join(rel);
        if entry.is_dir() {
            fs::create_dir_all(&dest).map_err(|e| format!("创建目录失败：{e}"))?;
        } else {
            if let Some(parent) = dest.parent() {
                fs::create_dir_all(parent).map_err(|e| format!("创建目录失败：{e}"))?;
            }
            let mut out = File::create(&dest).map_err(|e| format!("写入 {} 失败：{e}", dest.display()))?;
            io::copy(&mut entry, &mut out).map_err(|e| format!("写入 {} 失败：{e}", dest.display()))?;
        }
        progress(index as u64 + 1, total);
    }
    Ok(())
}

/// 从安装包里读 version.txt。发版时把它写进包根目录，
/// 这样安装器自己不用为了版本号重新编译。
pub fn payload_version(payload: &Payload) -> Option<String> {
    let reader = payload.reader().ok()?;
    let mut archive = zip::ZipArchive::new(reader).ok()?;
    let mut entry = archive.by_name("version.txt").ok()?;
    let mut text = String::new();
    entry.read_to_string(&mut text).ok()?;
    let trimmed = text.trim().to_string();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed)
    }
}

/// 解压后必须存在的文件，缺一个说明包是坏的
pub fn verify_install(target: &Path) -> Result<(), String> {
    let mut missing = Vec::new();
    if !target.join(EXE_NAME).exists() {
        missing.push(EXE_NAME.to_string());
    }
    for entry in ["_internal/python312.dll", "_internal/base_library.zip"] {
        if !target.join(entry).exists() {
            missing.push(entry.to_string());
        }
    }
    if missing.is_empty() {
        Ok(())
    } else {
        Err(format!("解压结果缺少：{}", missing.join("、")))
    }
}

/// 读出内部版本号：从程序目录的 version.txt 读不到时用固定串
pub fn app_version(exe_dir: &Path) -> String {
    fs::read_to_string(exe_dir.join("version.txt"))
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|_| "0.0.0".to_string())
}