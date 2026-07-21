use std::{
    fs,
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
    let content =
        fs::read_to_string(path).with_context(|| format!("failed to read {}", path.display()))?;
    let mut lines: Vec<String> = content.lines().map(ToString::to_string).collect();
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
    use super::*;

    #[test]
    fn missing_process_is_treated_as_not_running() {
        assert!(!service_is_alive(999_999));
    }
}
