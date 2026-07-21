use std::{collections::BTreeMap, path::PathBuf};

use anyhow::Result;

use crate::config::{AppConfig, RuntimePaths, expand_tilde};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ServiceName {
    Xvfb,
    Fluxbox,
    X11Vnc,
    NoVnc,
    Qq,
    Snowluma,
}

#[derive(Debug, Clone)]
pub struct ServiceSpec {
    pub name: ServiceName,
    pub command: String,
    pub args: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub deps: Vec<ServiceName>,
    pub cwd: Option<PathBuf>,
    pub health_check: HealthCheck,
    pub startup_timeout_secs: u64,
    pub log_file: PathBuf,
    pub state_file: PathBuf,
}

#[derive(Debug, Clone)]
pub enum HealthCheck {
    None,
    TcpPort(u16),
    XSocket(PathBuf),
}

impl ServiceName {
    pub const ALL: [ServiceName; 6] = [
        ServiceName::Xvfb,
        ServiceName::Fluxbox,
        ServiceName::X11Vnc,
        ServiceName::NoVnc,
        ServiceName::Qq,
        ServiceName::Snowluma,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            ServiceName::Xvfb => "xvfb",
            ServiceName::Fluxbox => "fluxbox",
            ServiceName::X11Vnc => "x11vnc",
            ServiceName::NoVnc => "novnc",
            ServiceName::Qq => "qq",
            ServiceName::Snowluma => "snowluma",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            ServiceName::Xvfb => "Xvfb",
            ServiceName::Fluxbox => "Fluxbox",
            ServiceName::X11Vnc => "x11vnc",
            ServiceName::NoVnc => "noVNC",
            ServiceName::Qq => "QQ",
            ServiceName::Snowluma => "Snowluma",
        }
    }
}

pub fn build_service_specs(config: &AppConfig, paths: &RuntimePaths) -> Result<Vec<ServiceSpec>> {
    let display_num = config.display_number()?;
    let x_socket = PathBuf::from(format!("/tmp/.X11-unix/X{display_num}"));
    let vnc_password = expand_tilde(&config.vnc_password_file)?;
    let snowluma_cwd = expand_tilde(&config.snowluma_cwd)?;

    Ok(vec![
        ServiceSpec {
            name: ServiceName::Xvfb,
            command: "Xvfb".to_string(),
            args: extend_args(
                vec![
                    config.display.clone(),
                    "-screen".to_string(),
                    config.screen.clone(),
                ],
                config.args_for("xvfb"),
            ),
            env: config.env_for("xvfb"),
            deps: vec![],
            cwd: None,
            health_check: HealthCheck::XSocket(x_socket),
            startup_timeout_secs: 8,
            log_file: paths.log_dir.join("xvfb.log"),
            state_file: paths.run_dir.join("xvfb.json"),
        },
        ServiceSpec {
            name: ServiceName::Fluxbox,
            command: "fluxbox".to_string(),
            args: extend_args(
                vec!["-display".to_string(), config.display.clone()],
                config.args_for("fluxbox"),
            ),
            env: with_display(config.display.clone(), config.env_for("fluxbox")),
            deps: vec![ServiceName::Xvfb],
            cwd: None,
            health_check: HealthCheck::None,
            startup_timeout_secs: 5,
            log_file: paths.log_dir.join("fluxbox.log"),
            state_file: paths.run_dir.join("fluxbox.json"),
        },
        ServiceSpec {
            name: ServiceName::X11Vnc,
            command: "x11vnc".to_string(),
            args: extend_args(
                vec![
                    "-display".to_string(),
                    config.display.clone(),
                    "-rfbauth".to_string(),
                    vnc_password.display().to_string(),
                    "-forever".to_string(),
                    "-shared".to_string(),
                ],
                config.args_for("x11vnc"),
            ),
            env: with_display(config.display.clone(), config.env_for("x11vnc")),
            deps: vec![ServiceName::Xvfb],
            cwd: None,
            health_check: HealthCheck::TcpPort(5900),
            startup_timeout_secs: 10,
            log_file: paths.log_dir.join("x11vnc.log"),
            state_file: paths.run_dir.join("x11vnc.json"),
        },
        ServiceSpec {
            name: ServiceName::NoVnc,
            command: "/usr/share/novnc/utils/novnc_proxy".to_string(),
            args: extend_args(
                vec![
                    "--vnc".to_string(),
                    "localhost:5900".to_string(),
                    "--listen".to_string(),
                    config.novnc_listen_port.to_string(),
                ],
                config.args_for("novnc"),
            ),
            env: config.env_for("novnc"),
            deps: vec![ServiceName::X11Vnc],
            cwd: None,
            health_check: HealthCheck::TcpPort(config.novnc_listen_port),
            startup_timeout_secs: 10,
            log_file: paths.log_dir.join("novnc.log"),
            state_file: paths.run_dir.join("novnc.json"),
        },
        ServiceSpec {
            name: ServiceName::Qq,
            command: config.qq_path.clone(),
            args: extend_args(
                vec![
                    "--no-sandbox".to_string(),
                    "--disable-gpu".to_string(),
                    "--disable-software-rasterizer".to_string(),
                    "--disable-gpu-compositing".to_string(),
                ],
                config.args_for("qq"),
            ),
            env: with_display(config.display.clone(), config.env_for("qq")),
            deps: vec![ServiceName::Xvfb, ServiceName::Fluxbox],
            cwd: None,
            health_check: HealthCheck::None,
            startup_timeout_secs: 5,
            log_file: paths.log_dir.join("qq.log"),
            state_file: paths.run_dir.join("qq.json"),
        },
        ServiceSpec {
            name: ServiceName::Snowluma,
            command: config.node_path.clone(),
            args: extend_args(
                vec![config.snowluma_entry.clone()],
                config.args_for("snowluma"),
            ),
            env: with_display(config.display.clone(), config.env_for("snowluma")),
            deps: vec![ServiceName::Xvfb, ServiceName::Fluxbox, ServiceName::Qq],
            cwd: Some(snowluma_cwd),
            health_check: HealthCheck::None,
            startup_timeout_secs: 5,
            log_file: paths.log_dir.join("snowluma.log"),
            state_file: paths.run_dir.join("snowluma.json"),
        },
    ])
}

fn extend_args(mut base: Vec<String>, extra: Vec<String>) -> Vec<String> {
    base.extend(extra);
    base
}

fn with_display(display: String, mut env: BTreeMap<String, String>) -> BTreeMap<String, String> {
    env.insert("DISPLAY".to_string(), display);
    env
}

#[cfg(test)]
pub fn dependents_for(name: ServiceName) -> Vec<ServiceName> {
    ServiceName::ALL
        .into_iter()
        .filter(|candidate| dependencies_of(*candidate).contains(&name))
        .collect()
}

#[cfg(test)]
pub fn dependencies_of(name: ServiceName) -> Vec<ServiceName> {
    match name {
        ServiceName::Xvfb => vec![],
        ServiceName::Fluxbox => vec![ServiceName::Xvfb],
        ServiceName::X11Vnc => vec![ServiceName::Xvfb],
        ServiceName::NoVnc => vec![ServiceName::X11Vnc],
        ServiceName::Qq => vec![ServiceName::Xvfb, ServiceName::Fluxbox],
        ServiceName::Snowluma => vec![ServiceName::Xvfb, ServiceName::Fluxbox, ServiceName::Qq],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dependency_graph_matches_plan() {
        assert_eq!(
            dependencies_of(ServiceName::NoVnc),
            vec![ServiceName::X11Vnc]
        );
        assert!(dependents_for(ServiceName::Qq).contains(&ServiceName::Snowluma));
        assert!(dependents_for(ServiceName::NoVnc).is_empty());
    }
}
