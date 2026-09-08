//! HTTP + SSE client for the Mimir brain (parity with clients/tui/brain_client.py).

use std::time::Duration;

use futures_util::StreamExt;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use thiserror::Error;
use url::Url;

pub const DEFAULT_BRAIN_URL: &str = "http://127.0.0.1:8000";
pub const CONNECT_TIMEOUT_S: u64 = 5;
pub const CONTROL_TIMEOUT_S: u64 = 10;
pub const TURN_TIMEOUT_S: u64 = 180;
pub const STT_TIMEOUT_S: u64 = 90;

#[derive(Debug, Error)]
pub enum BrainError {
    #[error("{0}")]
    Message(String),
}

impl BrainError {
    pub fn msg(s: impl Into<String>) -> Self {
        Self::Message(s.into())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthBadge {
    /// ready | degraded | unreachable
    pub badge: String,
    pub status: String,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConversationSummary {
    pub id: String,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
    pub preview: Option<String>,
    pub message_count: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
    pub created_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PreferenceRow {
    pub key: String,
    pub value: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SttResult {
    pub text: String,
    pub language: Option<String>,
}

/// Normalize brain base URL: trim, strip trailing slash, strip accidental `/v1`.
pub fn normalize_brain_url(url: &str) -> Result<String, BrainError> {
    let text = url.trim();
    if text.is_empty() {
        return Err(BrainError::msg("brain URL is empty"));
    }
    let mut parsed = Url::parse(text).map_err(|_| BrainError::msg(format!("invalid brain URL: {url}")))?;
    if parsed.scheme().is_empty() || parsed.host_str().is_none() {
        return Err(BrainError::msg(format!("invalid brain URL: {url}")));
    }
    let path = parsed.path().trim_end_matches('/').to_string();
    let cleaned_path = if path == "/v1" || path.ends_with("/v1") {
        if path == "/v1" {
            "".to_string()
        } else {
            path[..path.len() - 3].trim_end_matches('/').to_string()
        }
    } else {
        path
    };
    parsed.set_path(&cleaned_path);
    parsed.set_query(None);
    parsed.set_fragment(None);
    let mut out = parsed.to_string();
    if out.ends_with('/') {
        out.pop();
    }
    Ok(out)
}

pub fn is_loopback_url(url: &str) -> bool {
    match Url::parse(url) {
        Ok(u) => matches!(u.host_str(), Some("127.0.0.1") | Some("localhost") | Some("::1")),
        Err(_) => false,
    }
}

pub fn host_port_from_url(url: &str) -> Result<(String, u16), BrainError> {
    let parsed = Url::parse(&normalize_brain_url(url)?).map_err(|e| BrainError::msg(e.to_string()))?;
    let host = parsed.host_str().unwrap_or("127.0.0.1").to_string();
    let port = parsed
        .port()
        .unwrap_or(if parsed.scheme() == "https" { 443 } else { 80 });
    Ok((host, port))
}

/// Split an SSE text buffer into complete JSON events; return remainder.
pub fn parse_sse_chunk(buffer: &str) -> (Vec<Value>, String) {
    let mut events = Vec::new();
    let mut rest = buffer.to_string();
    loop {
        let mut sep: Option<(usize, usize)> = None;
        for candidate in ["\r\n\r\n", "\n\n"] {
            if let Some(idx) = rest.find(candidate) {
                if sep.map(|(i, _)| idx < i).unwrap_or(true) {
                    sep = Some((idx, candidate.len()));
                }
            }
        }
        let Some((idx, sep_len)) = sep else {
            break;
        };
        let raw = rest[..idx].to_string();
        rest = rest[idx + sep_len..].to_string();
        let mut data_lines: Vec<&str> = Vec::new();
        for line in raw.split('\n') {
            let line = line.trim_end_matches('\r');
            if let Some(rest_line) = line.strip_prefix("data:") {
                data_lines.push(rest_line.trim_start());
            }
        }
        if data_lines.is_empty() {
            continue;
        }
        let joined = data_lines.join("\n");
        match serde_json::from_str::<Value>(&joined) {
            Ok(Value::Object(map)) => events.push(Value::Object(map)),
            _ => continue,
        }
    }
    (events, rest)
}

/// Decode UTF-8 incrementally; keep incomplete trailing bytes.
pub fn push_utf8(pending: &mut Vec<u8>, chunk: &[u8]) -> String {
    pending.extend_from_slice(chunk);
    match std::str::from_utf8(pending) {
        Ok(s) => {
            let out = s.to_string();
            pending.clear();
            out
        }
        Err(e) => {
            let valid_up_to = e.valid_up_to();
            let out = String::from_utf8_lossy(&pending[..valid_up_to]).into_owned();
            let remainder = pending[valid_up_to..].to_vec();
            *pending = remainder;
            out
        }
    }
}

fn http_client(turn_timeout_s: u64) -> Client {
    Client::builder()
        .connect_timeout(Duration::from_secs(CONNECT_TIMEOUT_S))
        .timeout(Duration::from_secs(turn_timeout_s.max(1)))
        .build()
        .expect("reqwest client")
}

fn auth_headers(token: &str) -> reqwest::header::HeaderMap {
    let mut headers = reqwest::header::HeaderMap::new();
    headers.insert(
        reqwest::header::ACCEPT,
        reqwest::header::HeaderValue::from_static("application/json"),
    );
    let token = token.trim();
    if !token.is_empty() {
        if let Ok(v) = reqwest::header::HeaderValue::from_str(&format!("Bearer {token}")) {
            headers.insert(reqwest::header::AUTHORIZATION, v);
        }
    }
    headers
}

fn map_http_error(status: u16, body: &str, fallback: &str) -> BrainError {
    if status == 401 {
        return BrainError::msg("Bad token — check Settings.");
    }
    let mut detail = body.chars().take(200).collect::<String>();
    if let Ok(err) = serde_json::from_str::<Value>(body) {
        if let Some(d) = err.get("detail").and_then(|v| v.as_str()) {
            detail = d.to_string();
        }
    }
    if detail.is_empty() {
        BrainError::msg(format!("{fallback} HTTP {status}"))
    } else {
        BrainError::msg(detail)
    }
}

pub async fn get_health(base_url: &str, token: &str) -> Result<HealthBadge, BrainError> {
    let base = normalize_brain_url(base_url)?;
    let client = Client::builder()
        .connect_timeout(Duration::from_secs(CONNECT_TIMEOUT_S))
        .timeout(Duration::from_secs(CONTROL_TIMEOUT_S))
        .build()
        .map_err(|e| BrainError::msg(e.to_string()))?;
    let url = format!("{base}/health");
    let resp = client
        .get(&url)
        .headers(auth_headers(token))
        .send()
        .await
        .map_err(|e| BrainError::msg(format!("brain unreachable at {base}: {e}")))?;
    if resp.status().as_u16() >= 500 {
        return Ok(HealthBadge {
            badge: "unreachable".into(),
            status: "fail".into(),
            detail: format!("HTTP {}", resp.status()),
        });
    }
    let body: Value = resp
        .json()
        .await
        .map_err(|_| BrainError::msg("health returned non-JSON"))?;
    let status = body
        .get("status")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();
    let mut detail = String::new();
    if let Some(ollama) = body.get("ollama") {
        if ollama.get("reachable") == Some(&Value::Bool(false)) {
            detail = "ollama unreachable".into();
        }
    }
    if detail.is_empty() && status != "ok" {
        detail = format!("status={status}");
    }
    let badge = match status.as_str() {
        "ok" => "ready",
        "degraded" | "fail" => "degraded",
        _ => "degraded",
    };
    Ok(HealthBadge {
        badge: badge.into(),
        status,
        detail,
    })
}

pub async fn list_conversations(
    base_url: &str,
    token: &str,
    limit: u32,
) -> Result<Vec<ConversationSummary>, BrainError> {
    let base = normalize_brain_url(base_url)?;
    let client = Client::builder()
        .connect_timeout(Duration::from_secs(CONNECT_TIMEOUT_S))
        .timeout(Duration::from_secs(CONTROL_TIMEOUT_S))
        .build()
        .map_err(|e| BrainError::msg(e.to_string()))?;
    let url = format!("{base}/v1/conversations");
    let resp = client
        .get(&url)
        .query(&[("limit", limit.max(1))])
        .headers(auth_headers(token))
        .send()
        .await
        .map_err(|e| BrainError::msg(format!("list conversations failed: {e}")))?;
    let status = resp.status().as_u16();
    let text = resp.text().await.unwrap_or_default();
    if status >= 400 {
        return Err(map_http_error(status, &text, "list conversations"));
    }
    let body: Value =
        serde_json::from_str(&text).map_err(|_| BrainError::msg("list conversations returned non-JSON"))?;
    let convos = body
        .get("conversations")
        .and_then(|v| v.as_array())
        .ok_or_else(|| BrainError::msg("list conversations returned unexpected JSON"))?;
    let mut out = Vec::new();
    for item in convos {
        let id = item
            .get("id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if id.is_empty() {
            continue;
        }
        out.push(ConversationSummary {
            id,
            created_at: item
                .get("created_at")
                .and_then(|v| v.as_str())
                .map(str::to_string),
            updated_at: item
                .get("updated_at")
                .and_then(|v| v.as_str())
                .map(str::to_string),
            preview: item
                .get("preview")
                .and_then(|v| v.as_str())
                .map(str::to_string),
            message_count: item.get("message_count").and_then(|v| v.as_u64()),
        });
    }
    Ok(out)
}

pub async fn list_messages(
    base_url: &str,
    token: &str,
    conversation_id: &str,
) -> Result<Vec<ChatMessage>, BrainError> {
    let cid = conversation_id.trim();
    if cid.is_empty() {
        return Ok(vec![]);
    }
    let base = normalize_brain_url(base_url)?;
    let client = Client::builder()
        .connect_timeout(Duration::from_secs(CONNECT_TIMEOUT_S))
        .timeout(Duration::from_secs(CONTROL_TIMEOUT_S))
        .build()
        .map_err(|e| BrainError::msg(e.to_string()))?;
    let encoded = urlencoding_encode(cid);
    let url = format!("{base}/v1/conversations/{encoded}/messages");
    let resp = client
        .get(&url)
        .headers(auth_headers(token))
        .send()
        .await
        .map_err(|e| BrainError::msg(format!("list messages failed: {e}")))?;
    let status = resp.status().as_u16();
    let text = resp.text().await.unwrap_or_default();
    if status >= 400 {
        return Err(map_http_error(status, &text, "list messages"));
    }
    let body: Value =
        serde_json::from_str(&text).map_err(|_| BrainError::msg("list messages returned non-JSON"))?;
    let msgs = body
        .get("messages")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let mut out = Vec::new();
    for item in msgs {
        let role = item
            .get("role")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if role != "user" && role != "assistant" {
            continue;
        }
        let content = item
            .get("content")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        out.push(ChatMessage {
            role,
            content,
            created_at: item
                .get("created_at")
                .and_then(|v| v.as_str())
                .map(str::to_string),
        });
    }
    Ok(out)
}

fn urlencoding_encode(s: &str) -> String {
    let mut out = String::new();
    for b in s.as_bytes() {
        match *b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*b as char);
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

pub async fn get_preferences(
    base_url: &str,
    token: &str,
) -> Result<Vec<PreferenceRow>, BrainError> {
    let base = normalize_brain_url(base_url)?;
    let client = Client::builder()
        .connect_timeout(Duration::from_secs(CONNECT_TIMEOUT_S))
        .timeout(Duration::from_secs(CONTROL_TIMEOUT_S))
        .build()
        .map_err(|e| BrainError::msg(e.to_string()))?;
    let url = format!("{base}/v1/preferences");
    let resp = client
        .get(&url)
        .headers(auth_headers(token))
        .send()
        .await
        .map_err(|e| BrainError::msg(format!("get preferences failed: {e}")))?;
    let status = resp.status().as_u16();
    let text = resp.text().await.unwrap_or_default();
    if status >= 400 {
        return Err(map_http_error(status, &text, "get preferences"));
    }
    let body: Value =
        serde_json::from_str(&text).map_err(|_| BrainError::msg("get preferences returned non-JSON"))?;
    let prefs = body
        .get("preferences")
        .and_then(|v| v.as_array())
        .ok_or_else(|| BrainError::msg("get preferences returned unexpected JSON"))?;
    let mut out = Vec::new();
    for item in prefs {
        let key = item
            .get("key")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if key.is_empty() {
            continue;
        }
        let value = item.get("value").and_then(|v| {
            if v.is_null() {
                None
            } else if let Some(s) = v.as_str() {
                Some(s.to_string())
            } else {
                Some(v.to_string())
            }
        });
        out.push(PreferenceRow { key, value });
    }
    Ok(out)
}

pub async fn put_preference(
    base_url: &str,
    token: &str,
    key: &str,
    value: &str,
) -> Result<PreferenceRow, BrainError> {
    let key = key.trim();
    if key.is_empty() {
        return Err(BrainError::msg("preference key is empty"));
    }
    let base = normalize_brain_url(base_url)?;
    let client = Client::builder()
        .connect_timeout(Duration::from_secs(CONNECT_TIMEOUT_S))
        .timeout(Duration::from_secs(CONTROL_TIMEOUT_S))
        .build()
        .map_err(|e| BrainError::msg(e.to_string()))?;
    let encoded = urlencoding_encode(key);
    let url = format!("{base}/v1/preferences/{encoded}");
    let resp = client
        .put(&url)
        .headers(auth_headers(token))
        .json(&json!({ "value": value }))
        .send()
        .await
        .map_err(|e| BrainError::msg(format!("put preference failed: {e}")))?;
    let status = resp.status().as_u16();
    let text = resp.text().await.unwrap_or_default();
    if status >= 400 {
        return Err(map_http_error(status, &text, "put preference"));
    }
    let body: Value =
        serde_json::from_str(&text).map_err(|_| BrainError::msg("put preference returned non-JSON"))?;
    let out_key = body
        .get("key")
        .and_then(|v| v.as_str())
        .unwrap_or(key)
        .to_string();
    let out_value = body.get("value").and_then(|v| {
        if v.is_null() {
            None
        } else if let Some(s) = v.as_str() {
            Some(s.to_string())
        } else {
            Some(v.to_string())
        }
    });
    Ok(PreferenceRow {
        key: out_key,
        value: out_value,
    })
}

pub async fn post_stt(
    base_url: &str,
    token: &str,
    wav: Vec<u8>,
) -> Result<SttResult, BrainError> {
    if wav.is_empty() {
        return Err(BrainError::msg("empty audio"));
    }
    let base = normalize_brain_url(base_url)?;
    let client = Client::builder()
        .connect_timeout(Duration::from_secs(CONNECT_TIMEOUT_S))
        .timeout(Duration::from_secs(STT_TIMEOUT_S))
        .build()
        .map_err(|e| BrainError::msg(e.to_string()))?;
    let url = format!("{base}/v1/stt");
    let mut headers = auth_headers(token);
    headers.insert(
        reqwest::header::CONTENT_TYPE,
        reqwest::header::HeaderValue::from_static("audio/wav"),
    );
    let resp = client
        .post(&url)
        .headers(headers)
        .body(wav)
        .send()
        .await
        .map_err(|e| {
            if e.is_timeout() {
                BrainError::msg("speech recognition timed out")
            } else {
                BrainError::msg(format!("speech recognition failed: {e}"))
            }
        })?;
    let status = resp.status().as_u16();
    let text = resp.text().await.unwrap_or_default();
    if status >= 400 {
        if status == 401 {
            return Err(BrainError::msg("Bad token — check /connect."));
        }
        let mut detail = text.chars().take(200).collect::<String>();
        if let Ok(err) = serde_json::from_str::<Value>(&text) {
            if let Some(nested) = err.get("error").and_then(|e| e.get("message")).and_then(|m| m.as_str()) {
                detail = nested.to_string();
            } else if let Some(d) = err.get("detail").and_then(|v| v.as_str()) {
                detail = d.to_string();
            }
        }
        return Err(BrainError::msg(format!(
            "speech recognition HTTP {status}: {detail}"
        )));
    }
    let body: Value =
        serde_json::from_str(&text).map_err(|_| BrainError::msg("speech recognition returned non-JSON"))?;
    let transcript = body
        .get("text")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if transcript.is_empty() {
        return Err(BrainError::msg("No speech detected."));
    }
    let language = body
        .get("language")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    Ok(SttResult {
        text: transcript,
        language,
    })
}

pub struct StreamChatArgs {
    pub base_url: String,
    pub token: String,
    pub message: String,
    pub conversation_id: Option<String>,
    pub turn_timeout_s: u64,
}

/// Run SSE chat; call `on_event` for each JSON object. Returns Ok when stream ends cleanly.
pub async fn stream_chat(
    args: StreamChatArgs,
    mut on_event: impl FnMut(Value),
    mut cancelled: tokio::sync::watch::Receiver<bool>,
) -> Result<(), BrainError> {
    let base = normalize_brain_url(&args.base_url)?;
    let client = http_client(args.turn_timeout_s);
    let url = format!("{base}/v1/chat");
    let mut payload = json!({
        "message": args.message,
        "stream": true,
    });
    if let Some(cid) = args
        .conversation_id
        .as_ref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
    {
        payload["conversation_id"] = Value::String(cid.to_string());
    }

    let mut headers = auth_headers(&args.token);
    headers.insert(
        reqwest::header::ACCEPT,
        reqwest::header::HeaderValue::from_static("text/event-stream"),
    );

    let resp = client
        .post(&url)
        .headers(headers)
        .json(&payload)
        .send()
        .await
        .map_err(|e| {
            if e.is_timeout() {
                BrainError::msg(format!(
                    "chat timed out after {}s",
                    args.turn_timeout_s
                ))
            } else {
                BrainError::msg(format!("chat failed: {e}"))
            }
        })?;

    let status = resp.status().as_u16();
    if status >= 400 {
        let text = resp.text().await.unwrap_or_default();
        return Err(map_http_error(status, &text, "chat"));
    }

    let mut stream = resp.bytes_stream();
    let mut utf8_pending: Vec<u8> = Vec::new();
    let mut sse_buffer = String::new();
    let mut saw_terminal = false;

    loop {
        if *cancelled.borrow() {
            return Ok(());
        }
        let next = tokio::select! {
            biased;
            _ = cancelled.changed() => {
                if *cancelled.borrow() {
                    return Ok(());
                }
                continue;
            }
            chunk = stream.next() => chunk,
        };
        match next {
            Some(Ok(bytes)) => {
                let text = push_utf8(&mut utf8_pending, &bytes);
                if text.is_empty() {
                    continue;
                }
                sse_buffer.push_str(&text);
                let (events, rest) = parse_sse_chunk(&sse_buffer);
                sse_buffer = rest;
                for event in events {
                    let ty = event.get("type").and_then(|v| v.as_str()).unwrap_or("");
                    if ty == "done" || ty == "error" {
                        saw_terminal = true;
                    }
                    on_event(event);
                }
            }
            Some(Err(e)) => {
                if *cancelled.borrow() {
                    return Ok(());
                }
                if e.is_timeout() {
                    return Err(BrainError::msg(format!(
                        "chat timed out after {}s",
                        args.turn_timeout_s
                    )));
                }
                return Err(BrainError::msg(format!("chat failed: {e}")));
            }
            None => break,
        }
    }

    if !sse_buffer.trim().is_empty() {
        let flush = if sse_buffer.ends_with("\n\n") || sse_buffer.ends_with("\r\n\r\n") {
            sse_buffer.clone()
        } else {
            format!("{sse_buffer}\n\n")
        };
        let (events, _) = parse_sse_chunk(&flush);
        for event in events {
            let ty = event.get("type").and_then(|v| v.as_str()).unwrap_or("");
            if ty == "done" || ty == "error" {
                saw_terminal = true;
            }
            on_event(event);
        }
    }

    if !saw_terminal && !*cancelled.borrow() {
        return Err(BrainError::msg("Brain closed the stream"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_strips_v1() {
        assert_eq!(
            normalize_brain_url("http://127.0.0.1:8000/v1/").unwrap(),
            "http://127.0.0.1:8000"
        );
        assert_eq!(
            normalize_brain_url("http://127.0.0.1:8000/").unwrap(),
            "http://127.0.0.1:8000"
        );
    }

    #[test]
    fn parse_sse_basic() {
        let (events, rest) = parse_sse_chunk("data: {\"type\":\"meta\",\"conversation_id\":\"abc\"}\n\n");
        assert_eq!(events.len(), 1);
        assert_eq!(events[0]["type"], "meta");
        assert!(rest.is_empty());
    }

    #[test]
    fn parse_sse_skips_malformed() {
        let (events, _) = parse_sse_chunk("data: not-json\n\ndata: {\"type\":\"token\",\"text\":\"hi\"}\n\n");
        assert_eq!(events.len(), 1);
        assert_eq!(events[0]["text"], "hi");
    }

    #[test]
    fn utf8_split_across_chunks() {
        let euro = "€".as_bytes();
        assert_eq!(euro.len(), 3);
        let mut pending = Vec::new();
        let a = push_utf8(&mut pending, &euro[..1]);
        assert!(a.is_empty());
        assert!(!pending.is_empty());
        let b = push_utf8(&mut pending, &euro[1..]);
        assert_eq!(b, "€");
        assert!(pending.is_empty());
    }

    #[test]
    fn loopback_detection() {
        assert!(is_loopback_url("http://127.0.0.1:8000"));
        assert!(is_loopback_url("http://localhost:8000"));
        assert!(!is_loopback_url("http://192.168.1.5:8000"));
    }
}
