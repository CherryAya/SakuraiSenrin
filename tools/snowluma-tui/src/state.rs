use std::{
    fs,
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
};

use anyhow::{Context, Result};
use chrono::{DateTime, Local};
use nix::{
    errno::Errno,
    sys::signal::{Signal, kill},
    unistd::Pid,
};
use serde::{Deserialize, Serialize};

use crate::service::ServiceName;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceRuntimeState {
    pub service: String,
    pub pid: i32,
    pub pgid: i32,
    pub command: String,
    pub args: Vec<String>,
    pub cwd: Option<PathBuf>,
    pub log_file: PathBuf,
    pub started_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceExitState {
    pub service: String,
    pub exit_code: Option<i32>,
    pub signal: Option<i32>,
    pub expected_stop: bool,
    pub finished_at: String,
    pub output_tail: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ServiceStatus {
    Stopped,
    #[allow(dead_code)]
    Starting,
    Running,
    #[allow(dead_code)]
    Failed(String),
    #[allow(dead_code)]
    Stopping,
}

#[derive(Debug, Clone)]
pub struct ServiceStatusSnapshot {
    pub name: ServiceName,
    pub label: String,
    pub status: ServiceStatus,
    pub pid: Option<i32>,
    pub started_at: Option<String>,
    pub log_file: PathBuf,
    pub exit_state: Option<ServiceExitState>,
}

impl ServiceRuntimeState {
    pub fn new(
        service: ServiceName,
        pid: i32,
        command: String,
        args: Vec<String>,
        cwd: Option<PathBuf>,
        log_file: PathBuf,
    ) -> Self {
        Self {
            service: service.as_str().to_string(),
            pid,
            pgid: pid,
            command,
            args,
            cwd,
            log_file,
            started_at: DateTime::<Local>::from(Local::now()).to_rfc3339(),
        }
    }

    pub fn save_to(&self, path: &Path) -> Result<()> {
        let content =
            serde_json::to_string_pretty(self).context("failed to serialize runtime state")?;
        fs::write(path, content).with_context(|| format!("failed to write {}", path.display()))
    }

    pub fn load_from(path: &Path) -> Result<Self> {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("failed to read {}", path.display()))?;
        serde_json::from_str(&raw).with_context(|| format!("failed to parse {}", path.display()))
    }
}

pub fn service_is_alive(pid: i32) -> bool {
    kill(Pid::from_raw(pid), None).is_ok()
}

pub fn terminate_process_group(pgid: i32, signal: Signal) -> Result<()> {
    match kill(Pid::from_raw(-pgid), signal) {
        Ok(()) => Ok(()),
        Err(Errno::EPERM) => kill(Pid::from_raw(pgid), signal).or_else(ignore_missing_process),
        Err(error) => ignore_missing_process(error),
    }
}

pub fn clean_state_file(path: &Path) -> Result<()> {
    if path.exists() {
        fs::remove_file(path).with_context(|| format!("failed to remove {}", path.display()))?;
    }
    Ok(())
}

pub fn save_json<T: Serialize>(value: &T, path: &Path) -> Result<()> {
    let content = serde_json::to_string_pretty(value).context("failed to serialize json")?;
    fs::write(path, content).with_context(|| format!("failed to write {}", path.display()))
}

pub fn load_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T> {
    let raw =
        fs::read_to_string(path).with_context(|| format!("failed to read {}", path.display()))?;
    serde_json::from_str(&raw).with_context(|| format!("failed to parse {}", path.display()))
}

pub fn remove_file_if_exists(path: &Path) -> Result<()> {
    if path.exists() {
        fs::remove_file(path).with_context(|| format!("failed to remove {}", path.display()))?;
    }
    Ok(())
}

pub fn read_tail_lines(path: &Path, max_lines: usize) -> Result<Vec<String>> {
    if !path.exists() {
        return Ok(Vec::new());
    }

    const TAIL_READ_BYTES: u64 = 256 * 1024;
    let mut file =
        File::open(path).with_context(|| format!("failed to open {}", path.display()))?;
    let file_len = file
        .metadata()
        .with_context(|| format!("failed to stat {}", path.display()))?
        .len();
    let start = file_len.saturating_sub(TAIL_READ_BYTES);
    file.seek(SeekFrom::Start(start))
        .with_context(|| format!("failed to seek {}", path.display()))?;

    let mut bytes = Vec::with_capacity((file_len - start) as usize);
    file.read_to_end(&mut bytes)
        .with_context(|| format!("failed to read {}", path.display()))?;

    // A window can begin in the middle of a line. Drop that partial line so
    // callers never mistake truncated content for a complete log entry.
    if start > 0 {
        if let Some(newline) = bytes.iter().position(|byte| *byte == b'\n') {
            bytes.drain(..=newline);
        } else {
            bytes.clear();
        }
    }

    let content = String::from_utf8_lossy(&bytes);
    let mut lines: Vec<String> = content
        .split('\n')
        .map(|line| line.strip_suffix('\r').unwrap_or(line).to_string())
        .collect();
    if lines.last().is_some_and(String::is_empty) {
        lines.pop();
    }
    if lines.len() > max_lines {
        lines = lines.split_off(lines.len() - max_lines);
    }
    Ok(lines)
}

fn ignore_missing_process(error: Errno) -> Result<()> {
    if error == Errno::ESRCH {
        return Ok(());
    }
    Err(anyhow::anyhow!(error))
}

#[cfg(test)]
mod tests {
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::*;

    fn temp_log_path(name: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time went backwards")
            .as_nanos();
        std::env::temp_dir().join(format!("snowluma-tui-{name}-{stamp}.log"))
    }

    #[test]
    fn missing_process_is_treated_as_not_running() {
        assert!(!service_is_alive(999_999));
    }

    #[test]
    fn read_tail_lines_reads_only_the_end_of_large_logs() {
        let path = temp_log_path("large-tail");
        let mut content = vec![b'x'; 512 * 1024];
        content.extend_from_slice(b"\nold-tail\nlatest\n");
        fs::write(&path, content).expect("log should be written");

        let lines = read_tail_lines(&path, 2).expect("tail should be readable");

        assert_eq!(lines, vec!["old-tail", "latest"]);
        fs::remove_file(path).expect("temporary log should be removable");
    }

    #[test]
    fn read_tail_lines_replaces_invalid_utf8() {
        let path = temp_log_path("invalid-utf8");
        fs::write(&path, b"ok\ninvalid: \xFF\nlatest\n").expect("log should be written");

        let lines = read_tail_lines(&path, 3).expect("invalid utf8 should be tolerated");

        assert_eq!(lines, vec!["ok", "invalid: \u{FFFD}", "latest"]);
        fs::remove_file(path).expect("temporary log should be removable");
    }
}
