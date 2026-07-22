use std::sync::Mutex;
use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

struct BackendProcess(Mutex<Option<CommandChild>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendProcess(Mutex::new(None)))
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
