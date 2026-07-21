use std::{
    net::{TcpStream, ToSocketAddrs},
    path::Path,
    time::Duration,
};

use anyhow::Result;

use crate::{
    config::{AppConfig, RuntimePaths},
    service::{HealthCheck, ServiceSpec},
    state::{ServiceRuntimeState, service_is_alive},
};

#[derive(Debug, Clone)]
pub struct DiagnosticItem {
    pub label: String,
    pub status: String,
}

pub fn collect_diagnostics(
    config: &AppConfig,
    paths: &RuntimePaths,
    specs: &[ServiceSpec],
) -> Vec<DiagnosticItem> {
    let mut items = vec![
        DiagnosticItem {
            label: "Config file".to_string(),
            status: format!("{}", paths.config_file.display()),
        },
        DiagnosticItem {
            label: "State root".to_string(),
            status: format!("{}", paths.state_root.display()),
        },
        DiagnosticItem {
            label: "Display".to_string(),
            status: config.display.clone(),
        },
    ];

    for spec in specs {
        items.push(DiagnosticItem {
            label: format!("Command {}", spec.name.as_str()),
            status: command_status(&spec.command),
        });

        if let Ok(state) = ServiceRuntimeState::load_from(&spec.state_file) {
            let alive = if service_is_alive(state.pid) {
                "alive"
            } else {
                "stale"
            };
            items.push(DiagnosticItem {
                label: format!("Pid {}", spec.name.as_str()),
                status: format!("{alive} ({})", state.pid),
            });
        }

        match &spec.health_check {
            HealthCheck::TcpPort(port) => items.push(DiagnosticItem {
                label: format!("Port {}", port),
                status: port_status(*port),
            }),
            HealthCheck::XSocket(path) => items.push(DiagnosticItem {
                label: "X socket".to_string(),
                status: path_status(path),
            }),
            HealthCheck::None => {}
        }
    }

    items
}

fn command_status(command: &str) -> String {
    if command.contains('/') {
        return if Path::new(command).is_file() {
            "ok".to_string()
        } else {
            "missing".to_string()
        };
    }

    match std::env::var_os("PATH") {
        Some(paths) => {
            for path in std::env::split_paths(&paths) {
                if path.join(command).is_file() {
                    return "ok".to_string();
                }
            }
            "missing".to_string()
        }
        None => "PATH missing".to_string(),
    }
}

fn port_status(port: u16) -> String {
    let address = ("127.0.0.1", port)
        .to_socket_addrs()
        .ok()
        .and_then(|mut iter| iter.next());
    let Some(address) = address else {
        return "invalid".to_string();
    };
    match TcpStream::connect_timeout(&address, Duration::from_millis(200)) {
        Ok(_) => "listening".to_string(),
        Err(_) => "closed".to_string(),
    }
}

fn path_status(path: &Path) -> String {
    if path.exists() {
        "present".to_string()
    } else {
        "missing".to_string()
    }
}

pub fn tail_log(path: &Path, max_lines: usize) -> Result<Vec<String>> {
    if !path.exists() {
        return Ok(vec!["<log file not created yet>".to_string()]);
    }
    let content = std::fs::read_to_string(path)?;
    let mut lines: Vec<String> = content.lines().map(ToString::to_string).collect();
    if lines.len() > max_lines {
        lines = lines.split_off(lines.len() - max_lines);
    }
    if lines.is_empty() {
        lines.push("<log file is empty>".to_string());
    }
    Ok(lines)
}
