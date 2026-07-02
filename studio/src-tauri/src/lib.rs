use std::sync::Mutex;
use tauri::{Emitter, Manager};

/// Last app state pushed by the webview (see report_state); served on GET /state.
struct RemoteState(Mutex<String>);

#[tauri::command]
fn report_state(state: tauri::State<RemoteState>, json: String) {
    *state.0.lock().unwrap() = json;
}

/// First CLI argument that looks like an EDL path: `studio <path/to/edl.json>`.
#[tauri::command]
fn initial_project() -> Option<String> {
    std::env::args().skip(1).find(|a| a.ends_with(".json"))
}

/// Localhost control server so agents can drive the UI:
///   GET  /state                       → last reported app state (JSON)
///   POST /cmd  {"op": "...", ...}     → forwarded to the webview as a "remote-cmd" event
/// Ops: open{path} toggle play pause seek{t} select{i} undo redo export{preview?}
const CTL_ADDR: &str = "127.0.0.1:4859";

fn run_control_server(handle: tauri::AppHandle) {
    let server = match tiny_http::Server::http(CTL_ADDR) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("studio: control server unavailable on {CTL_ADDR}: {e}");
            return;
        }
    };
    for mut request in server.incoming_requests() {
        let url = request.url().to_string();
        let is_post = *request.method() == tiny_http::Method::Post;
        let (code, body) = if !is_post && url.starts_with("/state") {
            (200, handle.state::<RemoteState>().0.lock().unwrap().clone())
        } else if is_post && url.starts_with("/cmd") {
            let mut buf = String::new();
            let _ = request.as_reader().read_to_string(&mut buf);
            match handle.emit("remote-cmd", buf) {
                Ok(()) => (200, r#"{"ok":true}"#.to_string()),
                Err(e) => (500, format!(r#"{{"error":"{e}"}}"#)),
            }
        } else {
            (404, r#"{"error":"use GET /state or POST /cmd {\"op\":...}"}"#.to_string())
        };
        let header: tiny_http::Header = "Content-Type: application/json".parse().unwrap();
        let _ = request.respond(
            tiny_http::Response::from_string(body)
                .with_status_code(code)
                .with_header(header),
        );
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .manage(RemoteState(Mutex::new("{}".to_string())))
        .invoke_handler(tauri::generate_handler![report_state, initial_project])
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || run_control_server(handle));
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
