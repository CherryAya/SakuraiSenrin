use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, OpenOptions},
    io,
    os::unix::process::{CommandExt, ExitStatusExt},
    path::Path,
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use anyhow::{Context, Result, anyhow, bail};
use chrono::Local;
use nix::{sys::signal::Signal, unistd::setsid};
use serde_json::json;

use crate::{
    service::{HealthCheck, ServiceName, ServiceSpec},
    state::{
        ServiceExitState, ServiceRuntimeState, ServiceStatus, ServiceStatusSnapshot,
        clean_state_file, load_json, read_tail_lines, remove_file_if_exists, save_json,
        service_is_alive, terminate_process_group,
    },
};

#[derive(Clone)]
pub struct Supervisor {
    spec_map: BTreeMap<ServiceName, ServiceSpec>,
}

impl Supervisor {
    pub fn new(specs: Vec<ServiceSpec>) -> Self {
        let spec_map = specs.into_iter().map(|spec| (spec.name, spec)).collect();
        Self { spec_map }
    }

    pub fn specs(&self) -> Vec<&ServiceSpec> {
        ServiceName::ALL
            .into_iter()
            .filter_map(|name| self.spec_map.get(&name))
            .collect()
    }

    pub fn statuses(&self) -> Vec<ServiceStatusSnapshot> {
        self.specs()
            .into_iter()
            .map(|spec| self.status_for(spec.name))
            .collect()
    }

    pub fn start_sequence(&self, name: ServiceName) -> Vec<ServiceName> {
        let mut ordered = Vec::new();
        let mut seen = BTreeSet::new();
        self.collect_dependencies(name, &mut seen, &mut ordered);
        ordered
    }

    pub fn stop_sequence(&self, name: ServiceName) -> Vec<ServiceName> {
        vec![name]
    }

    pub fn restart_sequence(&self, name: ServiceName) -> Vec<ServiceName> {
        let mut set = BTreeSet::new();
        self.collect_dependents(name, &mut set);
        ServiceName::ALL
            .into_iter()
            .filter(|item| set.contains(item))
            .collect()
    }

    pub fn status_for(&self, name: ServiceName) -> ServiceStatusSnapshot {
        let spec = self
            .spec_map
            .get(&name)
            .expect("service spec should exist for all known services");
        let exit_state = load_json::<ServiceExitState>(&spec.exit_file).ok();

        match ServiceRuntimeState::load_from(&spec.state_file) {
            Ok(state) if service_is_alive(state.pid) => ServiceStatusSnapshot {
                name,
                label: name.label().to_string(),
                status: ServiceStatus::Running,
                pid: Some(state.pid),
                started_at: Some(state.started_at),
                log_file: spec.log_file.clone(),
                exit_state,
            },
            Ok(_) => {
                let _ = clean_state_file(&spec.state_file);
                ServiceStatusSnapshot {
                    name,
                    label: name.label().to_string(),
                    status: status_from_exit_state(exit_state.as_ref()),
                    pid: None,
                    started_at: None,
                    log_file: spec.log_file.clone(),
                    exit_state,
                }
            }
            Err(_) => ServiceStatusSnapshot {
                name,
                label: name.label().to_string(),
                status: status_from_exit_state(exit_state.as_ref()),
                pid: None,
                started_at: None,
                log_file: spec.log_file.clone(),
                exit_state,
            },
        }
    }

    pub fn start_all(&self) -> Result<()> {
        for name in ServiceName::ALL {
            self.start_service(name)?;
        }
        Ok(())
    }

    pub fn stop_all(&self) -> Result<()> {
        for name in ServiceName::ALL.into_iter().rev() {
            self.stop_service(name)?;
        }
        Ok(())
    }

    pub fn restart_all(&self) -> Result<()> {
        self.stop_all()?;
        self.start_all()
    }

    pub fn start_service(&self, name: ServiceName) -> Result<()> {
        for item in self.start_sequence(name) {
            self.spawn_if_needed(item)?;
        }
        Ok(())
    }

    pub fn stop_service(&self, name: ServiceName) -> Result<()> {
        let Some(spec) = self.spec_map.get(&name) else {
            bail!("unknown service");
        };
        let state = match ServiceRuntimeState::load_from(&spec.state_file) {
            Ok(state) => state,
            Err(_) => return Ok(()),
        };
        self.terminate_runtime_state(spec, &state, true)
    }

    pub fn restart_service(&self, name: ServiceName) -> Result<()> {
        let affected = self.restart_sequence(name);
        for item in affected.iter().rev().copied() {
            self.stop_service(item)?;
        }
        for item in affected {
            self.start_service(item)?;
        }
        Ok(())
    }

    fn spawn_if_needed(&self, name: ServiceName) -> Result<()> {
        let spec = self
            .spec_map
            .get(&name)
            .ok_or_else(|| anyhow!("missing spec for {}", name.as_str()))?;

        if let Ok(state) = ServiceRuntimeState::load_from(&spec.state_file) {
            if service_is_alive(state.pid) {
                return Ok(());
            }
            clean_state_file(&spec.state_file)?;
        }

        let _ = remove_file_if_exists(&spec.exit_file);
        let _ = remove_file_if_exists(&spec.stop_marker_file);

        if let Some(parent) = spec.log_file.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("failed to create {}", parent.display()))?;
        }
        if let Some(parent) = spec.state_file.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("failed to create {}", parent.display()))?;
        }

        let log_file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&spec.log_file)
            .with_context(|| format!("failed to open {}", spec.log_file.display()))?;
        let stderr_file = log_file
            .try_clone()
            .with_context(|| format!("failed to clone {}", spec.log_file.display()))?;

        let mut command = Command::new(&spec.command);
        command
            .args(&spec.args)
            .stdin(Stdio::null())
            .stdout(Stdio::from(log_file))
            .stderr(Stdio::from(stderr_file));

        if let Some(cwd) = &spec.cwd {
            command.current_dir(cwd);
        }
        for (key, value) in &spec.env {
            command.env(key, value);
        }

        unsafe {
            command.pre_exec(|| {
                setsid().map_err(io::Error::other)?;
                Ok(())
            });
        }

        let child = command.spawn().with_context(|| {
            format!(
                "failed to start {} using {}",
                spec.name.as_str(),
                spec.command
            )
        })?;

        let pid = child.id() as i32;
        let runtime_state = ServiceRuntimeState::new(
            spec.name,
            pid,
            spec.command.clone(),
            spec.args.clone(),
            spec.cwd.clone(),
            spec.log_file.clone(),
        );
        runtime_state.save_to(&spec.state_file)?;

        self.spawn_exit_monitor(spec.clone(), child);
        self.wait_for_health(spec, pid)
            .with_context(|| format!("{} failed health check", spec.name.as_str()))?;
        Ok(())
    }

    fn wait_for_health(&self, spec: &ServiceSpec, pid: i32) -> Result<()> {
        let deadline = Instant::now() + Duration::from_secs(spec.startup_timeout_secs);
        loop {
            if !service_is_alive(pid) {
                let _ = clean_state_file(&spec.state_file);
                bail!("process exited early");
            }
            if health_check_passes(&spec.health_check) {
                return Ok(());
            }
            if Instant::now() >= deadline {
                if let Ok(state) = ServiceRuntimeState::load_from(&spec.state_file) {
                    self.terminate_runtime_state(spec, &state, false)?;
                }
                bail!("health check timed out");
            }
            thread::sleep(Duration::from_millis(200));
        }
    }

    fn collect_dependencies(
        &self,
        name: ServiceName,
        seen: &mut BTreeSet<ServiceName>,
        ordered: &mut Vec<ServiceName>,
    ) {
        if !seen.insert(name) {
            return;
        }
        let spec = self
            .spec_map
            .get(&name)
            .expect("service spec should exist for all known services");
        for dep in &spec.deps {
            self.collect_dependencies(*dep, seen, ordered);
        }
        ordered.push(name);
    }

    fn collect_dependents(&self, name: ServiceName, set: &mut BTreeSet<ServiceName>) {
        if !set.insert(name) {
            return;
        }
        for spec in self.spec_map.values() {
            if spec.deps.contains(&name) {
                self.collect_dependents(spec.name, set);
            }
        }
    }

    fn terminate_runtime_state(
        &self,
        spec: &ServiceSpec,
        state: &ServiceRuntimeState,
        expected_stop: bool,
    ) -> Result<()> {
        if expected_stop {
            save_json(
                &json!({
                    "service": spec.name.as_str(),
                    "pid": state.pid,
                    "requested_at": Local::now().to_rfc3339(),
                }),
                &spec.stop_marker_file,
            )?;
        } else {
            let _ = remove_file_if_exists(&spec.stop_marker_file);
        }

        terminate_process_group(state.pgid, Signal::SIGTERM)?;
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if !service_is_alive(state.pid) {
                let _ = clean_state_file(&spec.state_file);
                return Ok(());
            }
            thread::sleep(Duration::from_millis(150));
        }

        terminate_process_group(state.pgid, Signal::SIGKILL)?;
        let _ = clean_state_file(&spec.state_file);
        Ok(())
    }

    fn spawn_exit_monitor(&self, spec: ServiceSpec, mut child: Child) {
        thread::spawn(move || {
            let status = match child.wait() {
                Ok(status) => status,
                Err(_) => return,
            };
            let exit_state = ServiceExitState {
                service: spec.name.as_str().to_string(),
                exit_code: status.code(),
                signal: status.signal(),
                expected_stop: spec.stop_marker_file.exists(),
                finished_at: Local::now().to_rfc3339(),
                output_tail: read_tail_lines(&spec.log_file, 20).unwrap_or_default(),
            };
            let _ = save_json(&exit_state, &spec.exit_file);
            let _ = clean_state_file(&spec.state_file);
            let _ = remove_file_if_exists(&spec.stop_marker_file);
        });
    }
}

fn health_check_passes(check: &HealthCheck) -> bool {
    match check {
        HealthCheck::None => true,
        HealthCheck::TcpPort(port) => std::net::TcpStream::connect(("127.0.0.1", *port)).is_ok(),
        HealthCheck::XSocket(path) => Path::new(path).exists(),
    }
}

fn status_from_exit_state(exit_state: Option<&ServiceExitState>) -> ServiceStatus {
    match exit_state {
        Some(exit) if !exit.expected_stop => ServiceStatus::Failed(exit_summary(exit)),
        _ => ServiceStatus::Stopped,
    }
}

fn exit_summary(exit: &ServiceExitState) -> String {
    let code = exit
        .exit_code
        .map(|value| format!("code {value}"))
        .unwrap_or_else(|| "no code".to_string());
    let signal = exit
        .signal
        .map(|value| format!("signal {value}"))
        .unwrap_or_else(|| "no signal".to_string());
    format!("{code}, {signal}")
}

#[cfg(test)]
mod tests {
    use std::{
        collections::BTreeMap,
        fs,
        path::PathBuf,
        thread,
        time::{Duration, SystemTime, UNIX_EPOCH},
    };

    use super::*;
    use crate::{
        service::{HealthCheck, ServiceName, ServiceSpec},
        state::ServiceStatus,
    };

    fn unique_temp_dir(name: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time went backwards")
            .as_nanos();
        std::env::temp_dir().join(format!("snowluma-tui-{name}-{stamp}"))
    }

    fn fake_specs(root: &Path) -> Vec<ServiceSpec> {
        let log_dir = root.join("logs");
        let run_dir = root.join("run");
        fs::create_dir_all(&log_dir).expect("log dir should exist");
        fs::create_dir_all(&run_dir).expect("run dir should exist");

        ServiceName::ALL
            .into_iter()
            .map(|name| ServiceSpec {
                name,
                command: "/bin/sh".to_string(),
                args: vec!["-c".to_string(), "exec sleep 30".to_string()],
                env: BTreeMap::new(),
                deps: match name {
                    ServiceName::Xvfb => vec![],
                    ServiceName::Fluxbox => vec![ServiceName::Xvfb],
                    ServiceName::X11Vnc => vec![ServiceName::Xvfb],
                    ServiceName::NoVnc => vec![ServiceName::X11Vnc],
                    ServiceName::Qq => vec![ServiceName::Xvfb, ServiceName::Fluxbox],
                    ServiceName::Snowluma => {
                        vec![ServiceName::Xvfb, ServiceName::Fluxbox, ServiceName::Qq]
                    }
                },
                cwd: None,
                health_check: HealthCheck::None,
                startup_timeout_secs: 1,
                log_file: log_dir.join(format!("{}.log", name.as_str())),
                state_file: run_dir.join(format!("{}.json", name.as_str())),
                exit_file: run_dir.join(format!("{}.exit.json", name.as_str())),
                stop_marker_file: run_dir.join(format!("{}.stop", name.as_str())),
            })
            .collect()
    }

    #[test]
    fn start_service_brings_up_dependencies_and_stop_cleans_state() {
        let temp_dir = unique_temp_dir("supervisor-start-stop");
        let supervisor = Supervisor::new(fake_specs(&temp_dir));

        supervisor
            .start_service(ServiceName::NoVnc)
            .expect("service with dependencies should start");

        let xvfb = supervisor.status_for(ServiceName::Xvfb);
        let x11vnc = supervisor.status_for(ServiceName::X11Vnc);
        let novnc = supervisor.status_for(ServiceName::NoVnc);
        let qq = supervisor.status_for(ServiceName::Qq);

        assert_eq!(xvfb.status, ServiceStatus::Running);
        assert_eq!(x11vnc.status, ServiceStatus::Running);
        assert_eq!(novnc.status, ServiceStatus::Running);
        assert_eq!(qq.status, ServiceStatus::Stopped);

        supervisor
            .stop_service(ServiceName::NoVnc)
            .expect("target service should stop");
        supervisor
            .stop_service(ServiceName::X11Vnc)
            .expect("dependency service should stop");
        supervisor
            .stop_service(ServiceName::Xvfb)
            .expect("root dependency should stop");

        thread::sleep(Duration::from_millis(300));
        assert_eq!(
            supervisor.status_for(ServiceName::NoVnc).status,
            ServiceStatus::Stopped
        );
        assert!(!temp_dir.join("run").join("novnc.json").exists());

        fs::remove_dir_all(temp_dir).expect("temp directory should be removable");
    }

    #[test]
    fn restart_service_restarts_dependents() {
        let temp_dir = unique_temp_dir("supervisor-restart");
        let supervisor = Supervisor::new(fake_specs(&temp_dir));

        supervisor.start_all().expect("all services should start");
        let first_xvfb_pid = supervisor
            .status_for(ServiceName::Xvfb)
            .pid
            .expect("xvfb pid should exist");
        let first_snowluma_pid = supervisor
            .status_for(ServiceName::Snowluma)
            .pid
            .expect("snowluma pid should exist");

        supervisor
            .restart_service(ServiceName::Xvfb)
            .expect("restarting root dependency should restart dependents");

        let second_xvfb_pid = supervisor
            .status_for(ServiceName::Xvfb)
            .pid
            .expect("xvfb pid should still exist");
        let second_snowluma_pid = supervisor
            .status_for(ServiceName::Snowluma)
            .pid
            .expect("snowluma pid should still exist");

        assert_ne!(first_xvfb_pid, second_xvfb_pid);
        assert_ne!(first_snowluma_pid, second_snowluma_pid);

        supervisor.stop_all().expect("services should stop");
        fs::remove_dir_all(temp_dir).expect("temp directory should be removable");
    }

    #[test]
    fn failed_health_check_cleans_state_file() {
        let temp_dir = unique_temp_dir("supervisor-fail");
        let mut specs = fake_specs(&temp_dir);
        let xvfb_spec = specs
            .iter_mut()
            .find(|spec| spec.name == ServiceName::Xvfb)
            .expect("xvfb spec should exist");
        xvfb_spec.health_check = HealthCheck::TcpPort(65500);
        xvfb_spec.startup_timeout_secs = 1;

        let supervisor = Supervisor::new(specs);
        let result = supervisor.start_service(ServiceName::Xvfb);
        assert!(result.is_err());
        assert!(!temp_dir.join("run").join("xvfb.json").exists());

        fs::remove_dir_all(temp_dir).expect("temp directory should be removable");
    }

    #[test]
    fn unexpected_exit_records_failure_output() {
        let temp_dir = unique_temp_dir("supervisor-exit");
        let mut specs = fake_specs(&temp_dir);
        let spec = specs
            .iter_mut()
            .find(|item| item.name == ServiceName::Xvfb)
            .expect("xvfb spec should exist");
        spec.args = vec![
            "-c".to_string(),
            "echo boom >&2; sleep 0.1; exit 3".to_string(),
        ];

        let supervisor = Supervisor::new(specs);
        supervisor
            .start_service(ServiceName::Xvfb)
            .expect("short-lived process should start");

        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            let snapshot = supervisor.status_for(ServiceName::Xvfb);
            if let ServiceStatus::Failed(reason) = &snapshot.status {
                assert!(reason.contains("code 3"));
                let exit_state = snapshot.exit_state.expect("exit state should exist");
                assert_eq!(exit_state.exit_code, Some(3));
                assert!(
                    exit_state
                        .output_tail
                        .iter()
                        .any(|line| line.contains("boom"))
                );
                break;
            }
            assert!(
                Instant::now() < deadline,
                "service should have failed by now"
            );
            thread::sleep(Duration::from_millis(50));
        }

        fs::remove_dir_all(temp_dir).expect("temp directory should be removable");
    }
}
