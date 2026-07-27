use std::sync::Mutex;
use serde::Serialize;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

struct BackendProcess(Mutex<Option<CommandChild>>);

#[derive(Serialize)]
struct ResearchBrowserCookie {
    name: String,
    value: String,
    domain: Option<String>,
    path: Option<String>,
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
        .manage(BackendProcess(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            open_research_browser,
            research_browser_cookies
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
            let command = app.shell().sidecar("evoagent-backend")?;

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
