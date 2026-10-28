//! 安装程序：解压自带安装包 → 建快捷方式 → 写注册表 → 启动程序。
//!
//! 安装包被追加在本 exe 末尾（`[exe][payload.zip][u64 长度]`），所以只有一个文件可分发，
//! 而且不像 PyInstaller 的 onefile 那样启动时要先把自己解压到 %TEMP%。

#![windows_subsystem = "windows"]

use std::ffi::c_void;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use setup_core::*;
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows_sys::Win32::Graphics::Gdi::{
    CreateFontW, UpdateWindow, CLEARTYPE_QUALITY, COLOR_WINDOW, DEFAULT_CHARSET, FW_NORMAL,
};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::EnableWindow;
use windows_sys::Win32::UI::Controls::{PBM_SETPOS, PBM_SETRANGE32};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

const ID_PATH: i32 = 1;
const ID_INSTALL: i32 = 2;
const ID_CANCEL: i32 = 3;
const ID_BAR: i32 = 4;
const ID_STATUS: i32 = 5;
const WM_PROGRESS: u32 = WM_APP + 1;
const WM_DONE: u32 = WM_APP + 2;
const WM_FAILED: u32 = WM_APP + 3;

const VERSION: &str = match option_env!("CX_VERSION") {
    Some(v) => v,
    None => "0.0.0",
};

static RUNNING: AtomicBool = AtomicBool::new(false);
static LAST_ERROR: Mutex<String> = Mutex::new(String::new());

fn set_child_text(hwnd: HWND, id: i32, text: &str) {
    unsafe {
        let child = GetDlgItem(hwnd, id);
        if !child.is_null() {
            SetWindowTextW(child, wide(text).as_ptr());
        }
    }
}

fn message_dialog(hwnd: HWND, text: &str) {
    unsafe {
        MessageBoxW(
            hwnd,
            wide(text).as_ptr(),
            wide("超星 AI 答题").as_ptr(),
            MB_OK | MB_ICONINFORMATION,
        );
    }
}

fn fail(hwnd: HWND, message: String) {
    log(&format!("安装失败：{message}"));
    if let Ok(mut slot) = LAST_ERROR.lock() {
        *slot = message;
    }
    unsafe { PostMessageW(hwnd, WM_FAILED, 0, 0) };
}

/// 真正干活的部分：静默安装和图形界面都走这里
fn do_install(target: &Path, progress: &dyn Fn(usize)) -> Result<(), String> {
    log(&format!("开始安装到 {}", target.display()));
    kill_app();
    // 只清程序文件，配置和题库保留（覆盖安装不丢资料）
    for item in PROGRAM_ITEMS {
        let path = target.join(item);
        let _ = if path.is_dir() {
            std::fs::remove_dir_all(&path)
        } else {
            std::fs::remove_file(&path)
        };
    }

    let payload = Payload::open_self().map_err(|e| format!("读取安装包失败：{e}"))?;
    extract_payload(&payload, target, &|done, total| {
        let percent = if total == 0 { 0 } else { (done * 100 / total) as usize };
        progress(percent);
    })?;
    verify_install(target)?;

    let version = payload_version(&payload).unwrap_or_else(|| VERSION.to_string());
    let exe = target.join(EXE_NAME);
    let uninstaller = target.join(UNINSTALLER_NAME);
    make_shortcut(&desktop_dir().join(format!("{APP_DISPLAY}.lnk")), &exe);
    make_shortcut(&start_menu_dir().join(format!("{APP_DISPLAY}.lnk")), &exe);
    write_registry(target, &version, &uninstaller, &exe);
    log(&format!("安装完成，版本 {version}"));
    Ok(())
}

fn install(hwnd: HWND, target: PathBuf) {
    set_child_text(hwnd, ID_STATUS, "正在关闭已运行的程序…");
    let post = hwnd as isize;
    let result = do_install(&target, &|percent| unsafe {
        PostMessageW(post as HWND, WM_PROGRESS, percent, 0);
    });
    match result {
        Ok(()) => {
            set_child_text(hwnd, ID_STATUS, "安装完成，正在启动…");
            unsafe { PostMessageW(hwnd, WM_DONE, 0, 0) };
        }
        Err(exc) => fail(hwnd, exc),
    }
}

unsafe extern "system" fn wnd_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match msg {
        WM_CREATE => {
            let font = CreateFontW(
                -14, 0, 0, 0, FW_NORMAL as i32, 0, 0, 0, DEFAULT_CHARSET as u32, 0, 0,
                CLEARTYPE_QUALITY as u32, 0, wide("Microsoft YaHei").as_ptr(),
            );
            let mut controls = Vec::new();
            controls.push(make_child(hwnd, "STATIC", "安装位置（可以改成别的目录）",
                                     WS_VISIBLE, 0, 18, 14, 390, 18));
            controls.push(make_child(hwnd, "EDIT", &default_dir().display().to_string(),
                                     WS_BORDER | ES_AUTOHSCROLL as u32, ID_PATH, 18, 36, 390, 26));
            controls.push(make_child(hwnd, "STATIC", "点「开始安装」，大约半分钟。",
                                     WS_VISIBLE, ID_STATUS, 18, 74, 390, 18));
            controls.push(make_child(hwnd, "msctls_progress32", "", 0, ID_BAR, 18, 98, 390, 16));
            controls.push(make_child(hwnd, "BUTTON", "开始安装", BS_DEFPUSHBUTTON as u32,
                                     ID_INSTALL, 228, 130, 86, 30));
            controls.push(make_child(hwnd, "BUTTON", "取消", 0, ID_CANCEL, 322, 130, 86, 30));
            for control in controls {
                SendMessageW(control, WM_SETFONT, font as WPARAM, 1);
            }
            SendMessageW(GetDlgItem(hwnd, ID_BAR), PBM_SETRANGE32, 0, 100isize);
            0
        }
        WM_COMMAND => {
            let id = (wparam & 0xffff) as i32;
            if id == ID_INSTALL {
                if RUNNING.swap(true, Ordering::SeqCst) {
                    return 0;
                }
                let path_text = read_text(GetDlgItem(hwnd, ID_PATH));
                if path_text.is_empty() {
                    message_dialog(hwnd, "请先填写安装位置");
                    RUNNING.store(false, Ordering::SeqCst);
                    return 0;
                }
                EnableWindow(GetDlgItem(hwnd, ID_INSTALL), 0);
                EnableWindow(GetDlgItem(hwnd, ID_CANCEL), 0);
                let target = PathBuf::from(path_text);
                let post = hwnd as isize;
                std::thread::spawn(move || install(post as HWND, target));
            } else if id == ID_CANCEL {
                DestroyWindow(hwnd);
            }
            0
        }
        WM_PROGRESS => {
            SendMessageW(GetDlgItem(hwnd, ID_BAR), PBM_SETPOS, wparam, 0);
            set_child_text(hwnd, ID_STATUS, &format!("正在解压程序文件… {}%", wparam));
            0
        }
        WM_DONE => {
            let target = PathBuf::from(read_text(GetDlgItem(hwnd, ID_PATH)));
            let exe = target.join(EXE_NAME);
            // 先把窗口关掉再启动，别让用户以为卡住了
            DestroyWindow(hwnd);
            let _ = std::process::Command::new(&exe).current_dir(&target).spawn();
            0
        }
        WM_FAILED => {
            let message = LAST_ERROR
                .lock()
                .map(|s| s.clone())
                .unwrap_or_else(|_| "未知错误".to_string());
            message_dialog(
                hwnd,
                &format!("{message}\n\n详细过程见 %TEMP%\\chaoxing_setup.log"),
            );
            DestroyWindow(hwnd);
            0
        }
        WM_CLOSE => {
            if !RUNNING.load(Ordering::SeqCst) {
                DestroyWindow(hwnd);
            }
            0
        }
        WM_DESTROY => {
            PostQuitMessage(0);
            0
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

unsafe fn make_child(parent: HWND, class: &str, text: &str, extra_style: u32, id: i32,
                     x: i32, y: i32, w: i32, h: i32) -> HWND {
    CreateWindowExW(
        0,
        wide(class).as_ptr(),
        wide(text).as_ptr(),
        WS_CHILD | extra_style,
        x, y, w, h,
        parent,
        id as usize as *mut c_void,
        GetModuleHandleW(std::ptr::null()),
        std::ptr::null(),
    )
}

fn read_text(hwnd: HWND) -> String {
    unsafe {
        if hwnd.is_null() {
            return String::new();
        }
        let len = GetWindowTextLengthW(hwnd);
        let mut buf = vec![0u16; (len + 1) as usize];
        let copied = GetWindowTextW(hwnd, buf.as_mut_ptr(), len + 1);
        String::from_utf16_lossy(&buf[..copied.max(0) as usize]).trim().to_string()
    }
}

fn main() {
    log(&format!("安装程序启动，版本 {VERSION}"));

    let args: Vec<String> = std::env::args().collect();
    if let Some(index) = args.iter().position(|a| a == "--silent") {
        // 静默安装：自动化测试和以后可能的命令行安装都走这条
        let target = args
            .get(index + 1)
            .map(PathBuf::from)
            .unwrap_or_else(default_dir);
        match do_install(&target, &|_| {}) {
            Ok(()) => log(&format!("静默安装完成：{}", target.display())),
            Err(exc) => log(&format!("静默安装失败：{exc}")),
        }
        return;
    }

    unsafe {
        let instance = GetModuleHandleW(std::ptr::null());
        let class_name = wide("ChaoxingSetupWindow");
        let wc = WNDCLASSW {
            style: CS_HREDRAW | CS_VREDRAW,
            lpfnWndProc: Some(wnd_proc),
            cbClsExtra: 0,
            cbWndExtra: 0,
            hInstance: instance,
            hIcon: std::ptr::null_mut(),
            hCursor: LoadCursorW(std::ptr::null_mut(), IDC_ARROW),
            hbrBackground: (COLOR_WINDOW + 1) as *mut c_void,
            lpszMenuName: std::ptr::null(),
            lpszClassName: class_name.as_ptr(),
        };
        if RegisterClassW(&wc) == 0 {
            return;
        }

        let hwnd = CreateWindowExW(
            0,
            class_name.as_ptr(),
            wide("安装 超星 AI 答题").as_ptr(),
            WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
            CW_USEDEFAULT, CW_USEDEFAULT, 448, 210,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            instance,
            std::ptr::null(),
        );
        if hwnd.is_null() {
            return;
        }
        ShowWindow(hwnd, SW_SHOW);
        UpdateWindow(hwnd);

        let mut msg: MSG = std::mem::zeroed();
        while GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0) > 0 {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
}