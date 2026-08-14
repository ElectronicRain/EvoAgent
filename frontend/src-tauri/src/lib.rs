use std::sync::Mutex;
use std::path::Path;
use base64::Engine;
use serde::Serialize;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

#[cfg(windows)]
use winreg::{enums::HKEY_CURRENT_USER, RegKey};

struct BackendProcess(Mutex<Option<CommandChild>>);

#[derive(Serialize)]
struct ResearchBrowserCookie {
    name: String,
    value: String,
    domain: Option<String>,
    path: Option<String>,
}

#[derive(Serialize)]
struct SelectedLatexFile {
    name: String,
    relative_path: String,
    content_base64: String,
}

#[tauri::command]
fn system_update_proxy() -> Option<String> {
    #[cfg(windows)]
    {
        let internet_settings = RegKey::predef(HKEY_CURRENT_USER)
            .open_subkey("Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings")
            .ok()?;
        let enabled: u32 = internet_settings.get_value("ProxyEnable").ok()?;
        if enabled == 0 {
            return None;
        }
        let raw: String = internet_settings.get_value("ProxyServer").ok()?;
        let selected = raw
            .split(';')
            .find_map(|entry| entry.strip_prefix("https="))
            .or_else(|| raw.split(';').find_map(|entry| entry.strip_prefix("http=")))
            .unwrap_or(raw.as_str())
            .trim();
        if selected.is_empty() {
            return None;
        }
        return Some(if selected.contains("://") {
            selected.to_string()
        } else {
            format!("http://{selected}")
        });
    }

    #[cfg(not(windows))]
    None
}

fn is_latex_project_file(path: &Path) -> bool {
    matches!(
        path.extension().and_then(|value| value.to_str()).unwrap_or_default().to_ascii_lowercase().as_str(),
        "zip" | "tex" | "bib" | "cls" | "sty" | "bst" | "bbx" | "cbx" | "cfg" |
        "def" | "clo" | "txt" | "md" | "csv" | "tsv" | "png" | "jpg" | "jpeg" |
        "pdf" | "eps" | "svg"
    )
}

fn selected_file(path: &Path, relative_path: String) -> Result<SelectedLatexFile, String> {
    let metadata = std::fs::metadata(path).map_err(|error| error.to_string())?;
    if metadata.len() > 25_000_000 {
        return Err(format!("文件过大（上限 25 MB）：{}", path.display()));
    }
    let bytes = std::fs::read(path).map_err(|error| error.to_string())?;
    Ok(SelectedLatexFile {
        name: path.file_name().and_then(|value| value.to_str()).unwrap_or("file").to_string(),
        relative_path: relative_path.replace('\\', "/"),
        content_base64: base64::engine::general_purpose::STANDARD.encode(bytes),
    })
}

fn collect_project_files(root: &Path, folder: &Path, output: &mut Vec<SelectedLatexFile>) -> Result<(), String> {
    for entry in std::fs::read_dir(folder).map_err(|error| error.to_string())? {
        let path = entry.map_err(|error| error.to_string())?.path();
        if path.is_dir() {
            collect_project_files(root, &path, output)?;
        } else if is_latex_project_file(&path) {
            if output.len() >= 300 {
                return Err("项目文件数超过 300 个，请先整理后再导入".to_string());
            }
            let relative = path.strip_prefix(root).map_err(|error| error.to_string())?;
            output.push(selected_file(&path, relative.to_string_lossy().to_string())?);
        }
    }
    Ok(())
}

#[tauri::command]
async fn select_latex_files() -> Result<Vec<SelectedLatexFile>, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let paths = rfd::FileDialog::new()
            .add_filter("LaTeX 项目", &["zip", "tex", "bib", "cls", "sty", "bst", "png", "jpg", "jpeg", "pdf", "eps", "svg"])
            .pick_files()
            .unwrap_or_default();
        paths.into_iter().filter(|path| is_latex_project_file(path)).map(|path| {
            let relative = path.file_name().and_then(|value| value.to_str()).unwrap_or("file").to_string();
            selected_file(&path, relative)
        }).collect()
    }).await.map_err(|error| error.to_string())?
}

#[tauri::command]
async fn select_latex_folder() -> Result<Vec<SelectedLatexFile>, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let Some(root) = rfd::FileDialog::new().pick_folder() else { return Ok(Vec::new()); };
        let mut files = Vec::new();
        collect_project_files(&root, &root, &mut files)?;
        Ok(files)
    }).await.map_err(|error| error.to_string())?
}

fn public_web_url(raw_url: &str) -> Result<tauri::Url, String> {
    let url = tauri::Url::parse(raw_url).map_err(|error| error.to_string())?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err("只允许在联网访问中心打开 HTTP/HTTPS 站点".to_string());
    }
    Ok(url)
}

fn same_research_site(left: &str, right: &str) -> bool {
    left.eq_ignore_ascii_case(right)
        || (left.to_ascii_lowercase().ends_with(".google.com")
            && right.to_ascii_lowercase().ends_with(".google.com"))
}

#[tauri::command]
async fn open_research_browser(
    app: tauri::AppHandle,
    url: String,
    title: String,
) -> Result<String, String> {
    let parsed = public_web_url(&url)?;
    let label = "research-browser";
    if let Some(window) = app.get_webview_window(label) {
        window.navigate(parsed).map_err(|error| error.to_string())?;
        window
            .set_title(&format!("EvoAgent 联网访问中心 · {}", title))
            .map_err(|error| error.to_string())?;
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
    } else {
        WebviewWindowBuilder::new(&app, label, WebviewUrl::External(parsed))
            .title(format!("EvoAgent 联网访问中心 · {}", title))
            .inner_size(1280.0, 840.0)
            .min_inner_size(900.0, 620.0)
            .center()
            .resizable(true)
            .build()
            .map_err(|error| error.to_string())?;
    }
    Ok(label.to_string())
}

#[tauri::command]
async fn research_browser_cookies(
    app: tauri::AppHandle,
    url: String,
) -> Result<Vec<ResearchBrowserCookie>, String> {
    let parsed = public_web_url(&url)?;
    let expected_host = parsed.host_str().unwrap_or_default().to_string();
    let window = app
        .get_webview_window("research-browser")
        .ok_or_else(|| "请先在联网访问中心打开待验证站点".to_string())?;
    let current = window.url().map_err(|error| error.to_string())?;
    if !same_research_site(current.host_str().unwrap_or_default(), &expected_host) {
        return Err("当前验证窗口与选中站点不匹配".to_string());
    }
    let cookies = tauri::async_runtime::spawn_blocking(move || window.cookies_for_url(parsed))
        .await
        .map_err(|error| error.to_string())?
        .map_err(|error| error.to_string())?;
    Ok(cookies
        .into_iter()
        .map(|cookie| ResearchBrowserCookie {
            name: cookie.name().to_string(),
            value: cookie.value().to_string(),
            domain: cookie.domain().map(str::to_string),
            path: cookie.path().map(str::to_string),
        })
        .collect())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(BackendProcess(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            open_research_browser,
            research_browser_cookies,
            select_latex_files,
            select_latex_folder,
            system_update_proxy
        ])
        .setup(|app| {
            #[cfg(debug_assertions)]
            let command = {
                let project_root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .and_then(|path| path.parent())
                    .expect("project root")
                    .to_path_buf();
                let python = project_root.join(".venv").join("Scripts").join("python.exe");
                app.shell()
                    .command(python)
                    .current_dir(project_root)
                    .args(["-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000"])
            };

            #[cfg(not(debug_assertions))]
            let command = {
                let resource_dir = app.path().resource_dir()?;
                let bundled_tectonic = [
                    resource_dir.join("tectonic.exe"),
                    resource_dir.join("binaries").join("tectonic.exe"),
                ]
                .into_iter()
                .find(|path| path.is_file());
                let command = app.shell().sidecar("evoagent-backend")?;
                if let Some(path) = bundled_tectonic {
                    command.env("EVO_BUNDLED_TECTONIC", path)
                } else {
                    command
                }
            };

            let (_events, child) = command.spawn()?;
            *app.state::<BackendProcess>().0.lock().expect("backend process lock") = Some(child);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build EvoAgent desktop application");

    app.run(|handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            if let Some(child) = handle
                .state::<BackendProcess>()
                .0
                .lock()
                .expect("backend process lock")
                .take()
            {
                let _ = child.kill();
            }
        }
    });
}
