//! App config (URL + conversation_id) + Windows Credential Manager for token.

use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

use crate::brain_client::{normalize_brain_url, BrainError, DEFAULT_BRAIN_URL};

const KEYRING_SERVICE: &str = "mimir-desktop";
const KEYRING_USER: &str = "client-token";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppSettings {
    pub brain_url: String,
    #[serde(default)]
    pub conversation_id: Option<String>,
    /// Present only when returning to the UI; never written to disk.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub token: Option<String>,
    /// True when a token is stored in the keyring (UI does not need the value).
    #[serde(default)]
    pub has_token: bool,
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            brain_url: DEFAULT_BRAIN_URL.to_string(),
            conversation_id: None,
            token: None,
            has_token: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct DiskSettings {
    brain_url: String,
    conversation_id: Option<String>,
}

fn config_path(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_config_dir()
        .map_err(|e| format!("config dir: {e}"))?;
    fs::create_dir_all(&dir).map_err(|e| format!("create config dir: {e}"))?;
    Ok(dir.join("settings.json"))
}

fn read_disk(app: &AppHandle) -> DiskSettings {
    let Ok(path) = config_path(app) else {
        return DiskSettings {
            brain_url: DEFAULT_BRAIN_URL.to_string(),
            conversation_id: None,
        };
    };
    match fs::read_to_string(&path) {
        Ok(text) => serde_json::from_str(&text).unwrap_or(DiskSettings {
            brain_url: DEFAULT_BRAIN_URL.to_string(),
            conversation_id: None,
        }),
        Err(_) => DiskSettings {
            brain_url: DEFAULT_BRAIN_URL.to_string(),
            conversation_id: None,
        },
    }
}

fn write_disk(app: &AppHandle, disk: &DiskSettings) -> Result<(), String> {
    let path = config_path(app)?;
    let text = serde_json::to_string_pretty(disk).map_err(|e| e.to_string())?;
    fs::write(path, text).map_err(|e| format!("write settings: {e}"))
}

fn keyring_entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER).map_err(|e| format!("keyring: {e}"))
}

pub fn load_token() -> Option<String> {
    let entry = keyring_entry().ok()?;
    entry.get_password().ok().filter(|s| !s.trim().is_empty())
}

pub fn save_token(token: &str) -> Result<(), String> {
    let entry = keyring_entry()?;
    let trimmed = token.trim();
    if trimmed.is_empty() {
        let _ = entry.delete_credential();
        return Ok(());
    }
    entry
        .set_password(trimmed)
        .map_err(|e| format!("keyring save: {e}"))
}

pub fn get_settings(app: &AppHandle) -> AppSettings {
    let disk = read_disk(app);
    let token = load_token();
    let has_token = token.is_some();
    AppSettings {
        brain_url: if disk.brain_url.trim().is_empty() {
            DEFAULT_BRAIN_URL.to_string()
        } else {
            disk.brain_url
        },
        conversation_id: disk.conversation_id.filter(|s| !s.trim().is_empty()),
        // Return token so the UI can show a masked field / allow edit without re-entry
        // only when user opens settings — still never logged.
        token,
        has_token,
    }
}

#[derive(Debug, Deserialize)]
pub struct SaveSettingsInput {
    pub brain_url: String,
    pub token: Option<String>,
    pub conversation_id: Option<String>,
    /// When true, leave existing keyring token unchanged if `token` is None/empty.
    #[serde(default)]
    pub keep_existing_token: bool,
}

pub fn save_settings(app: &AppHandle, input: SaveSettingsInput) -> Result<AppSettings, String> {
    let url = normalize_brain_url(&input.brain_url).map_err(|e: BrainError| e.to_string())?;
    match &input.token {
        Some(t) if !t.trim().is_empty() => save_token(t)?,
        Some(_) if !input.keep_existing_token => save_token("")?,
        None if !input.keep_existing_token => save_token("")?,
        _ => {}
    }
    let disk = DiskSettings {
        brain_url: url,
        conversation_id: input
            .conversation_id
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty()),
    };
    write_disk(app, &disk)?;
    Ok(get_settings(app))
}

pub fn set_conversation_id(app: &AppHandle, conversation_id: Option<String>) -> Result<(), String> {
    let mut disk = read_disk(app);
    disk.conversation_id = conversation_id
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty());
    if disk.brain_url.trim().is_empty() {
        disk.brain_url = DEFAULT_BRAIN_URL.to_string();
    }
    write_disk(app, &disk)
}
