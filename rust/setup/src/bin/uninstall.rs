//! 卸载程序：让用户选「只删程序」还是「连本地资料一起删」。
//!
//! 卸载器自己就在要删的目录里，没法一边运行一边删自己，所以真正的删除交给
//! 一个复制到 %TEMP% 的后台副本（`--cleanup` 模式）去做。

#![windows_subsystem = "windows"]

use std::ffi::c_void;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use setup_core::*;
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows_sys::Win32::Graphics::Gdi::{
    CreateFontW, UpdateWindow, CLEARTYPE_QUALITY, COLOR_WINDOW, DEFAULT_CHARSET, FW_NORMAL,
};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::UI::Controls::BST_CHECKED;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::EnableWindow;
use windows_sys::Win32::UI::WindowsAndMessaging::*;

const ID_KEEP: i32 = 1;
const ID_WIPE: i32 = 2;
const ID_GO: i32 = 3;
const ID_CANCEL: i32 = 4;
const WM_FINISH: u32 = WM_APP + 1;

const CLEANUP_MARK: &str = "--cleanup";
static WORKING: AtomicBool = AtomicBool::new(false);

fn install_dir() -> PathBuf {
    for (index, arg) in std::env::args().enumerate() {
        if arg == "--dir" {
            if let Some(value) = std::env::args().nth(index + 1) {
                return PathBuf::from(value);
            }
        }
    }
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_else(default_dir)
}

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
            wide(APP_DISPLAY).as_ptr(),
            MB_OK | MB_ICONINFORMATION,
        );
    }
}

/// 把真正的删除交给一个临时副本，等本进程退出后再动手
fn schedule_cleanup(target: &Path, keep_data: bool) -> Result<(), String> {
    let me = std::env::current_exe().map_err(|e| format!("定位自身失败：{e}"))?;
    let temp = std::env::temp_dir().join("chaoxing-uninstall-cleanup.exe");
    std::fs::copy(&me, &temp).map_err(|e| format!("复制清理程序失败：{e}"))?;

    let mode = if keep_data { "keep" } else { "all" };
    std::process::Command::new(&temp)
        .arg(CLEANUP_MARK)
        .arg(target)
        .arg(mode)
        .arg(std::process::id().to_string())
        .spawn()
        .map_err(|e| format!("启动清理程序失败：{e}"))?;
    Ok(())
}

/// `--cleanup <目录> <keep|all> <父进程PID>`
fn run_cleanup(args: &[String]) -> i32 {
    let (Some(target), Some(mode), Some(pid)) = (args.get(0), args.get(1), args.get(2)) else {
        return 2;
    };
    let target = PathBuf::from(target);
    let pid: u32 = pid.parse().unwrap_or(0);

    // 等卸载界面那一边退出，否则文件还被占着
    for _ in 0..40 {
        if !process_alive(pid) {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(300));
    }
    std::thread::sleep(std::time::Duration::from_millis(400));

    if mode == "all" {
        remove_everything(&target);
    } else {
        remove_program_files(&target);
        // 卸载器自身也属于程序文件，删完就把空目录留着不影响什么
        let _ = std::fs::remove_file(target.join(UNINSTALLER_NAME));
    }
    0
}

fn process_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    // 用一个最小的系统调用判断：能打开就还活着
    unsafe {
        use windows_sys::Win32::System::Threading::{OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION};
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return false;
        }
        windows_sys::Win32::Foundation::CloseHandle(handle);
        true
    }
}

fn uninstall(hwnd: HWND, target: PathBuf, keep_data: bool) {
    log(&format!(
        "开始卸载 {}（{}）",
        target.display(),
        if keep_data { "保留本地资料" } else { "同时删除本地资料" }
    ));
    kill_app();
    remove_shortcuts();
    delete_registry();
    match schedule_cleanup(&target, keep_data) {
        Ok(()) => unsafe {
            PostMessageW(hwnd, WM_FINISH, 0, 0);
        },
        Err(exc) => {
            log(&format!("卸载失败：{exc}"));
            message_dialog(hwnd, &format!("{exc}\n\n可以手动删除安装目录：{}", target.display()));
            WORKING.store(false, Ordering::SeqCst);
        }
    }
}

unsafe extern "system" fn wnd_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match msg {
        WM_CREATE => {
            let target = install_dir();
            let font = CreateFontW(
                -14, 0, 0, 0, FW_NORMAL as i32, 0, 0, 0, DEFAULT_CHARSET as u32, 0, 0,
                CLEARTYPE_QUALITY as u32, 0, wide("Microsoft YaHei").as_ptr(),
            );
            let mut controls = Vec::new();
            controls.push(make_child(hwnd, "STATIC", "安装位置", WS_VISIBLE, 0, 18, 14, 380, 16));
            controls.push(make_child(hwnd, "STATIC", &target.display().to_string(),
                                     WS_VISIBLE, 0, 18, 32, 380, 18));
            controls.push(make_child(hwnd, "BUTTON", "只卸载程序，保留本地资料（账号、题库、配置）",
                                     WS_VISIBLE as u32 | BS_AUTORADIOBUTTON as u32 | WS_GROUP, ID_KEEP,
                                     18, 62, 380, 20));
            controls.push(make_child(hwnd, "BUTTON", "同时删除本地资料，无法恢复",
                                     WS_VISIBLE as u32 | BS_AUTORADIOBUTTON as u32, ID_WIPE,
                                     18, 86, 380, 20));
            controls.push(make_child(hwnd, "STATIC", "", WS_VISIBLE, 5, 18, 112, 380, 18));
            controls.push(make_child(hwnd, "BUTTON", "卸载", BS_DEFPUSHBUTTON as u32, ID_GO,
                                     222, 138, 82, 30));
            controls.push(make_child(hwnd, "BUTTON", "取消", 0, ID_CANCEL, 312, 138, 82, 30));
            for control in controls {
                SendMessageW(control, WM_SETFONT, font as WPARAM, 1);
            }
            // 默认选安全的那一项
            SendMessageW(GetDlgItem(hwnd, ID_KEEP), BM_SETCHECK, BST_CHECKED as usize, 0);
            0
        }

        WM_COMMAND => {
            let id = (wparam & 0xffff) as i32;
            if id == ID_GO {
                if WORKING.swap(true, Ordering::SeqCst) {
                    return 0;
                }
                let wipe = SendMessageW(GetDlgItem(hwnd, ID_WIPE), BM_GETCHECK, 0, 0) == BST_CHECKED as isize;
                set_child_text(hwnd, 5, "正在清理…");
                EnableWindow(GetDlgItem(hwnd, ID_GO), 0);
                EnableWindow(GetDlgItem(hwnd, ID_CANCEL), 0);
                let target = install_dir();
                let post = hwnd as isize;
                std::thread::spawn(move || uninstall(post as HWND, target, !wipe));
            } else if id == ID_CANCEL {
                DestroyWindow(hwnd);
            }
            0
        }
        WM_FINISH => {
            DestroyWindow(hwnd);
            0
        }
        WM_CLOSE => {
            if !WORKING.load(Ordering::SeqCst) {
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

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if let Some(index) = args.iter().position(|a| a == CLEANUP_MARK) {
        let rest: Vec<String> = args[index + 1..].to_vec();
        std::process::exit(run_cleanup(&rest));
    }

    let target = install_dir();
    if !target.join(EXE_NAME).exists() {
        message_dialog(
            std::ptr::null_mut(),
            &format!("这个目录里没有 {EXE_NAME}，未做任何删除：\n{}", target.display()),
        );
        return;
    }

    let silent = args.iter().any(|a| a == "--silent");
    if silent {
        let keep = !args.iter().any(|a| a == "--delete-data");
        kill_app();
        remove_shortcuts();
        delete_registry();
        // 和图形界面一样交给后台副本删：卸载器正在运行，删不掉自己
        match schedule_cleanup(&target, keep) {
            Ok(()) => log("已安排删除"),
            Err(exc) => log(&format!("启动清理程序失败：{exc}")),
        }
        return;
    }

    unsafe {
        let instance = GetModuleHandleW(std::ptr::null());
        let class_name = wide("ChaoxingUninstallWindow");
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
            wide("卸载 超星 AI 答题").as_ptr(),
            WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
            CW_USEDEFAULT, CW_USEDEFAULT, 430, 215,
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