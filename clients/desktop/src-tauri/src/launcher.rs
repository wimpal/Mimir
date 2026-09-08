//! Auto-start the Mimir brain when unreachable (parity with TUI brain_launcher).

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use serde::Serialize;
use tokio::process::Command;
use tokio::sync::Mutex;

use crate::brain_client::{get_health, host_port_from_url, is_loopback_url, normalize_brain_url};

const READY_TIMEOUT_S: u64 = 60;
const POLL_MS: u64 = 500;

static LAUNCH_LOCK: Mutex<()> = Mutex::const_new(());

#[derive(Debug, Clone, Serialize)]
pub struct LaunchResult {
    pub already_running: bool,
    pub started: bool,
    pub message: String,
    pub pid: Option<u32>,
}

fn find_mimir_repo_root() -> Option<PathBuf> {
    if let Ok(env) = std::env::var("MIMIR_REPO_ROOT") {
        let p = PathBuf::from(env);
        if looks_like_repo(&p) {
            return Some(p);
        }
    }
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            candidates.push(parent.to_path_buf());
            if let Some(grand) = parent.parent() {
                candidates.push(grand.to_path_buf());
            }
        }
    }
    for start in candidates {
        let mut cur = Some(start.as_path());
        while let Some(dir) = cur {
            if looks_like_repo(dir) {
                return Some(dir.to_path_buf());
            }
            if dir.file_name().and_then(|s| s.to_str()) == Some("desktop") {
                if let Some(clients) = dir.parent() {
                    if let Some(repo) = clients.parent() {
                        if looks_like_repo(repo) {
                            return Some(repo.to_path_buf());
                        }
                    }
                }
            }
            cur = dir.parent();
        }
    }
    None
}

fn looks_like_repo(path: &Path) -> bool {
    path.join("pyproject.toml").is_file() && path.join("brain").is_dir()
}

fn which_uv() -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join("uv");
        if candidate.is_file() {
            return Some(candidate);
        }
        let with_exe = dir.join("uv.exe");
        if with_exe.is_file() {
            return Some(with_exe);
        }
    }
    None
}

async fn brain_reachable(base_url: &str) -> bool {
    match get_health(base_url, "").await {
        Ok(h) => h.badge != "unreachable",
        Err(_) => false,
    }
}

pub async fn ensure_brain_running(base_url: &str) -> LaunchResult {
    let url = match normalize_brain_url(base_url) {
        Ok(u) => u,
        Err(e) => {
            return LaunchResult {
                already_running: false,
                started: false,
                message: e.to_string(),
                pid: None,
            };
        }
    };

    if !is_loopback_url(&url) {
        return LaunchResult {
            already_running: false,
            started: false,
            message: "Auto-launch only works for localhost brain URLs.".into(),
            pid: None,
        };
    }

    if brain_reachable(&url).await {
        return LaunchResult {
            already_running: true,
            started: false,
            message: "Brain already running.".into(),
            pid: None,
        };
    }

    let _guard = LAUNCH_LOCK.lock().await;

    if brain_reachable(&url).await {
        return LaunchResult {
            already_running: true,
            started: false,
            message: "Brain already running.".into(),
            pid: None,
        };
    }

    let Some(root) = find_mimir_repo_root() else {
        return LaunchResult {
            already_running: false,
            started: false,
            message:
                "Could not find the Mimir repo (pyproject.toml + brain/). Set MIMIR_REPO_ROOT."
                    .into(),
            pid: None,
        };
    };

    let Some(uv) = which_uv() else {
        return LaunchResult {
            already_running: false,
            started: false,
            message: "Could not find `uv` on PATH. Install uv or start the brain manually.".into(),
            pid: None,
        };
    };

    let (host, port) = match host_port_from_url(&url) {
        Ok(hp) => hp,
        Err(e) => {
            return LaunchResult {
                already_running: false,
                started: false,
                message: e.to_string(),
                pid: None,
            };
        }
    };

    let log_dir = root.join("data").join("logs");
    if let Err(e) = fs::create_dir_all(&log_dir) {
        return LaunchResult {
            already_running: false,
            started: false,
            message: format!("Could not create log dir: {e}"),
            pid: None,
        };
    }
    let log_path = log_dir.join("brain_launch.log");
    if let Ok(mut log_file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
    {
        let _ = writeln!(log_file, "\n--- launching brain {host}:{port} (desktop) ---");
        let _ = log_file.flush();
    }

    let stdout = match OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
    {
        Ok(f) => Stdio::from(f),
        Err(e) => {
            return LaunchResult {
                already_running: false,
                started: false,
                message: format!("Could not open brain_launch.log: {e}"),
                pid: None,
            };
        }
    };
    let stderr = match OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
    {
        Ok(f) => Stdio::from(f),
        Err(e) => {
            return LaunchResult {
                already_running: false,
                started: false,
                message: format!("Could not open brain_launch.log: {e}"),
                pid: None,
            };
        }
    };

    let mut cmd = Command::new(&uv);
    cmd.args([
        "run",
        "uvicorn",
        "brain.main:app",
        "--host",
        &host,
        "--port",
        &port.to_string(),
    ])
    .current_dir(&root)
    .stdout(stdout)
    .stderr(stderr)
    .kill_on_drop(false);

    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            return LaunchResult {
                already_running: false,
                started: false,
                message: format!("Failed to spawn brain: {e}"),
                pid: None,
            };
        }
    };
    let pid = child.id();
    // Detach — health poll owns readiness; do not kill on drop.
    std::mem::forget(child);

    let deadline = Instant::now() + Duration::from_secs(READY_TIMEOUT_S);
    while Instant::now() < deadline {
        if brain_reachable(&url).await {
            return LaunchResult {
                already_running: false,
                started: true,
                message: "Brain started.".into(),
                pid,
            };
        }
        tokio::time::sleep(Duration::from_millis(POLL_MS)).await;
    }

    LaunchResult {
        already_running: false,
        started: false,
        message: format!(
            "Brain did not become ready within {READY_TIMEOUT_S}s. See data/logs/brain_launch.log"
        ),
        pid,
    }
}
