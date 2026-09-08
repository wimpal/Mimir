mod brain_client;
mod commands;
mod launcher;
mod settings;

use std::sync::Arc;

use commands::AppState;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let state = Arc::new(AppState::default());
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            commands::get_app_settings,
            commands::save_app_settings,
            commands::set_active_conversation,
            commands::get_health_badge,
            commands::list_convos,
            commands::load_messages,
            commands::start_chat,
            commands::cancel_chat,
            commands::ensure_brain,
            commands::copy_text,
            commands::list_preferences,
            commands::save_preference,
            commands::transcribe_wav,
            commands::log_voice_latency,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
