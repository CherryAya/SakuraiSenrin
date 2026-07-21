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

#[cfg(test)]
mod tests {
    use std::{
        collections::BTreeMap,
        fs,
        path::PathBuf,
        time::{SystemTime, UNIX_EPOCH},
    };

    use super::*;
    use crate::{
        config::RuntimePaths,
        service::{HealthCheck, ServiceName, ServiceSpec},
        state::ServiceRuntimeState,
    };

    fn unique_temp_dir(name: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time went backwards")
            .as_nanos();
        std::env::temp_dir().join(format!("snowluma-tui-{name}-{stamp}"))
    }

    #[test]
    fn diagnostics_report_missing_command_and_stale_pid() {
        let temp_dir = unique_temp_dir("diagnostics");
        let paths = RuntimePaths {
            config_dir: temp_dir.join("config"),
            config_file: temp_dir.join("config").join("config.toml"),
            state_root: temp_dir.join("state"),
            log_dir: temp_dir.join("logs"),
            run_dir: temp_dir.join("run"),
        };
        paths.ensure().expect("paths should be created");

        let spec = ServiceSpec {
            name: ServiceName::Xvfb,
            command: "/definitely/missing-binary".to_string(),
            args: Vec::new(),
            env: BTreeMap::new(),
            deps: Vec::new(),
            cwd: None,
            health_check: HealthCheck::None,
            startup_timeout_secs: 1,
            log_file: paths.log_dir.join("xvfb.log"),
            state_file: paths.run_dir.join("xvfb.json"),
            exit_file: paths.run_dir.join("xvfb.exit.json"),
            stop_marker_file: paths.run_dir.join("xvfb.stop"),
        };
        ServiceRuntimeState::new(
            ServiceName::Xvfb,
            999_999,
            spec.command.clone(),
            Vec::new(),
            None,
            spec.log_file.clone(),
        )
        .save_to(&spec.state_file)
        .expect("runtime state should save");

        let items = collect_diagnostics(&AppConfig::default(), &paths, &[spec]);
        assert!(
            items
                .iter()
                .any(|item| { item.label == "Command xvfb" && item.status == "missing" })
        );
        assert!(
            items.iter().any(|item| {
                item.label == "Pid xvfb" && item.status.starts_with("stale (999999)")
            })
        );

        fs::remove_dir_all(temp_dir).expect("temp directory should be removable");
    }

    #[test]
    fn diagnostics_report_closed_port() {
        let port = 65500;
        let temp_dir = unique_temp_dir("diagnostics-port");
        let paths = RuntimePaths {
            config_dir: temp_dir.join("config"),
            config_file: temp_dir.join("config").join("config.toml"),
            state_root: temp_dir.join("state"),
            log_dir: temp_dir.join("logs"),
            run_dir: temp_dir.join("run"),
        };
        paths.ensure().expect("paths should be created");

        let spec = ServiceSpec {
            name: ServiceName::NoVnc,
            command: "/bin/sh".to_string(),
            args: Vec::new(),
            env: BTreeMap::new(),
            deps: Vec::new(),
            cwd: None,
            health_check: HealthCheck::TcpPort(port),
            startup_timeout_secs: 1,
            log_file: paths.log_dir.join("novnc.log"),
            state_file: paths.run_dir.join("novnc.json"),
            exit_file: paths.run_dir.join("novnc.exit.json"),
            stop_marker_file: paths.run_dir.join("novnc.stop"),
        };

        let items = collect_diagnostics(&AppConfig::default(), &paths, &[spec]);
        assert!(
            items
                .iter()
                .any(|item| item.label == format!("Port {}", port) && item.status == "closed")
        );

        fs::remove_dir_all(temp_dir).expect("temp directory should be removable");
    }
}
