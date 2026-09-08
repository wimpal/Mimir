//! Tauri command surface for the desktop GUI.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use serde::Serialize;
use serde_json::json;
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::sync::{watch, Mutex};

use crate::brain_client::{
    get_health, get_preferences, list_conversations, list_messages, post_stt, put_preference,
    stream_chat, ChatMessage, ConversationSummary, HealthBadge, PreferenceRow, SttResult,
    StreamChatArgs, TURN_TIMEOUT_S,
};
use crate::launcher::{ensure_brain_running, LaunchResult};
use crate::settings::{
    get_settings, save_settings, set_conversation_id, AppSettings, SaveSettingsInput,
};

pub struct AppState {
    pub next_stream_id: AtomicU64,
    pub cancel_txs: Mutex<HashMap<u64, watch::Sender<bool>>>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            next_stream_id: AtomicU64::new(1),
            cancel_txs: Mutex::new(HashMap::new()),
        }
    }
}

#[derive(Clone, Serialize)]
struct ChatEventEnvelope {
    stream_id: u64,
    event: serde_json::Value,
}

#[derive(Clone, Serialize)]
struct ChatClosedEnvelope {
    stream_id: u64,
    reason: String,
}

#[tauri::command]
pub fn get_app_settings(app: AppHandle) -> AppSettings {
    get_settings(&app)
}

#[tauri::command]
pub fn save_app_settings(app: AppHandle, input: SaveSettingsInput) -> Result<AppSettings, String> {
    save_settings(&app, input)
}

#[tauri::command]
pub fn set_active_conversation(
    app: AppHandle,
    conversation_id: Option<String>,
) -> Result<(), String> {
    set_conversation_id(&app, conversation_id)
}

#[tauri::command]
pub async fn get_health_badge(app: AppHandle) -> Result<HealthBadge, String> {
    let s = get_settings(&app);
    let token = s.token.unwrap_or_default();
    match get_health(&s.brain_url, &token).await {
        Ok(h) => Ok(h),
        Err(e) => Ok(HealthBadge {
            badge: "unreachable".into(),
            status: "unreachable".into(),
            detail: e.to_string(),
        }),
    }
}

#[tauri::command]
pub async fn list_convos(
    app: AppHandle,
    limit: Option<u32>,
) -> Result<Vec<ConversationSummary>, String> {
    let s = get_settings(&app);
    let token = s
        .token
        .filter(|t| !t.trim().is_empty())
        .ok_or_else(|| "Bad token — check Settings.".to_string())?;
    list_conversations(&s.brain_url, &token, limit.unwrap_or(50))
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn load_messages(
    app: AppHandle,
    conversation_id: String,
) -> Result<Vec<ChatMessage>, String> {
    let s = get_settings(&app);
    let token = s
        .token
        .filter(|t| !t.trim().is_empty())
        .ok_or_else(|| "Bad token — check Settings.".to_string())?;
    list_messages(&s.brain_url, &token, &conversation_id)
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn start_chat(
    app: AppHandle,
    state: State<'_, Arc<AppState>>,
    message: String,
    conversation_id: Option<String>,
) -> Result<u64, String> {
    let s = get_settings(&app);
    let token = s
        .token
        .filter(|t| !t.trim().is_empty())
        .ok_or_else(|| "Bad token — check Settings.".to_string())?;
    if message.trim().is_empty() {
        return Err("empty message".into());
    }

    let stream_id = state.next_stream_id.fetch_add(1, Ordering::SeqCst);
    let (tx, rx) = watch::channel(false);
    {
        let mut map = state.cancel_txs.lock().await;
        map.insert(stream_id, tx);
    }

    let app_handle = app.clone();
    let state_clone = Arc::clone(&state);
    let args = StreamChatArgs {
        base_url: s.brain_url,
        token,
        message,
        conversation_id,
        turn_timeout_s: TURN_TIMEOUT_S,
    };

    tauri::async_runtime::spawn(async move {
        let mut token_buf = String::new();
        let mut last_flush = std::time::Instant::now();

        let result = stream_chat(
            args,
            |event| {
                let ty = event.get("type").and_then(|v| v.as_str()).unwrap_or("");
                if ty == "token" {
                    if let Some(text) = event.get("text").and_then(|v| v.as_str()) {
                        token_buf.push_str(text);
                        if last_flush.elapsed().as_millis() >= 40 {
                            let text = std::mem::take(&mut token_buf);
                            last_flush = std::time::Instant::now();
                            let _ = app_handle.emit(
                                "chat:event",
                                ChatEventEnvelope {
                                    stream_id,
                                    event: json!({ "type": "token", "text": text }),
                                },
                            );
                        }
                    }
                    return;
                }
                if !token_buf.is_empty() {
                    let text = std::mem::take(&mut token_buf);
                    let _ = app_handle.emit(
                        "chat:event",
                        ChatEventEnvelope {
                            stream_id,
                            event: json!({ "type": "token", "text": text }),
                        },
                    );
                }
                let _ = app_handle.emit(
                    "chat:event",
                    ChatEventEnvelope { stream_id, event },
                );
            },
            rx,
        )
        .await;

        if !token_buf.is_empty() {
            let text = std::mem::take(&mut token_buf);
            let _ = app_handle.emit(
                "chat:event",
                ChatEventEnvelope {
                    stream_id,
                    event: json!({ "type": "token", "text": text }),
                },
            );
        }

        let reason = match result {
            Ok(()) => "ok".to_string(),
            Err(e) => {
                let msg = e.to_string();
                let _ = app_handle.emit(
                    "chat:event",
                    ChatEventEnvelope {
                        stream_id,
                        event: json!({ "type": "error", "message": msg }),
                    },
                );
                "error".to_string()
            }
        };
        let _ = app_handle.emit(
            "chat:closed",
            ChatClosedEnvelope { stream_id, reason },
        );
        let mut map = state_clone.cancel_txs.lock().await;
        map.remove(&stream_id);
    });

    Ok(stream_id)
}

#[tauri::command]
pub async fn cancel_chat(state: State<'_, Arc<AppState>>, stream_id: u64) -> Result<(), String> {
    let map = state.cancel_txs.lock().await;
    if let Some(tx) = map.get(&stream_id) {
        let _ = tx.send(true);
    }
    Ok(())
}

#[tauri::command]
pub async fn ensure_brain(app: AppHandle) -> Result<LaunchResult, String> {
    let s = get_settings(&app);
    Ok(ensure_brain_running(&s.brain_url).await)
}

#[tauri::command]
pub fn copy_text(text: String) -> Result<(), String> {
    let mut clipboard =
        arboard::Clipboard::new().map_err(|e| format!("clipboard unavailable: {e}"))?;
    clipboard
        .set_text(text)
        .map_err(|e| format!("clipboard write failed: {e}"))
}

#[tauri::command]
pub async fn list_preferences(app: AppHandle) -> Result<Vec<PreferenceRow>, String> {
    let s = get_settings(&app);
    let token = s
        .token
        .filter(|t| !t.trim().is_empty())
        .ok_or_else(|| "Bad token — check /connect.".to_string())?;
    get_preferences(&s.brain_url, &token)
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn save_preference(
    app: AppHandle,
    key: String,
    value: String,
) -> Result<PreferenceRow, String> {
    let s = get_settings(&app);
    let token = s
        .token
        .filter(|t| !t.trim().is_empty())
        .ok_or_else(|| "Bad token — check /connect.".to_string())?;
    put_preference(&s.brain_url, &token, &key, &value)
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn transcribe_wav(app: AppHandle, wav: Vec<u8>) -> Result<SttResult, String> {
    let s = get_settings(&app);
    let token = s
        .token
        .filter(|t| !t.trim().is_empty())
        .ok_or_else(|| "Bad token — check /connect.".to_string())?;
    post_stt(&s.brain_url, &token, wav)
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn log_voice_latency(
    app: AppHandle,
    record_ms: u64,
    stt_ms: u64,
    ok: bool,
) -> Result<(), String> {
    use std::io::Write;
    let dir = app
        .path()
        .app_log_dir()
        .map_err(|e| format!("log dir: {e}"))?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("create log dir: {e}"))?;
    let path = dir.join("voice.jsonl");
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("open voice log: {e}"))?;
    let line = json!({
        "ts": chrono_timestamp(),
        "record_ms": record_ms,
        "stt_ms": stt_ms,
        "ok": ok,
    });
    writeln!(file, "{line}").map_err(|e| format!("write voice log: {e}"))?;
    Ok(())
}

fn chrono_timestamp() -> String {
    // RFC3339-ish UTC without extra deps
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("{secs}")
}
