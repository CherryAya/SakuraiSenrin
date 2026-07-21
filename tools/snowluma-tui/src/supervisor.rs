use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, OpenOptions},
    io,
    os::unix::process::CommandExt,
    path::Path,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use anyhow::{Context, Result, anyhow, bail};
use nix::{
    sys::signal::Signal,
    unistd::{Pid, getsid, setsid},
};

use crate::{
    service::{HealthCheck, ServiceName, ServiceSpec},
    state::{
        ServiceRuntimeState, ServiceStatus, ServiceStatusSnapshot, clean_state_file,
        service_is_alive, terminate_process_group,
    },
};

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

    pub fn status_for(&self, name: ServiceName) -> ServiceStatusSnapshot {
        let spec = self
            .spec_map
            .get(&name)
            .expect("service spec should exist for all known services");
        match ServiceRuntimeState::load_from(&spec.state_file) {
            Ok(state) if service_is_alive(state.pid) => ServiceStatusSnapshot {
                name,
                label: name.label().to_string(),
                status: ServiceStatus::Running,
                pid: Some(state.pid),
                started_at: Some(state.started_at),
                log_file: spec.log_file.clone(),
            },
            Ok(_) => {
                let _ = clean_state_file(&spec.state_file);
                ServiceStatusSnapshot {
                    name,
                    label: name.label().to_string(),
                    status: ServiceStatus::Stopped,
                    pid: None,
                    started_at: None,
                    log_file: spec.log_file.clone(),
                }
            }
            Err(_) => ServiceStatusSnapshot {
                name,
                label: name.label().to_string(),
                status: ServiceStatus::Stopped,
                pid: None,
                started_at: None,
                log_file: spec.log_file.clone(),
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
        let mut ordered = Vec::new();
        let mut seen = BTreeSet::new();
        self.collect_dependencies(name, &mut seen, &mut ordered);
        for item in ordered {
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

        terminate_process_group(state.pgid, Signal::SIGTERM)?;
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if !service_is_alive(state.pid) {
                clean_state_file(&spec.state_file)?;
                return Ok(());
            }
            thread::sleep(Duration::from_millis(150));
        }

        terminate_process_group(state.pgid, Signal::SIGKILL)?;
        clean_state_file(&spec.state_file)?;
        Ok(())
    }

    pub fn restart_service(&self, name: ServiceName) -> Result<()> {
        let affected = self.restart_closure(name);
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
        let _ = getsid(Some(Pid::from_raw(pid)));
        let runtime_state = ServiceRuntimeState::new(
            spec.name,
            pid,
            spec.command.clone(),
            spec.args.clone(),
            spec.cwd.clone(),
            spec.log_file.clone(),
        );
        runtime_state.save_to(&spec.state_file)?;

        self.wait_for_health(spec, pid)
            .with_context(|| format!("{} failed health check", spec.name.as_str()))?;
        Ok(())
    }

    fn wait_for_health(&self, spec: &ServiceSpec, pid: i32) -> Result<()> {
        let deadline = Instant::now() + Duration::from_secs(spec.startup_timeout_secs);
        loop {
            if !service_is_alive(pid) {
                clean_state_file(&spec.state_file)?;
                bail!("process exited early");
            }
            if health_check_passes(&spec.health_check) {
                return Ok(());
            }
            if Instant::now() >= deadline {
                self.stop_service(spec.name)?;
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

    fn restart_closure(&self, name: ServiceName) -> Vec<ServiceName> {
        let mut set = BTreeSet::new();
        self.collect_dependents(name, &mut set);
        ServiceName::ALL
            .into_iter()
            .filter(|item| set.contains(item))
            .collect()
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
}

fn health_check_passes(check: &HealthCheck) -> bool {
    match check {
        HealthCheck::None => true,
        HealthCheck::TcpPort(port) => std::net::TcpStream::connect(("127.0.0.1", *port)).is_ok(),
        HealthCheck::XSocket(path) => Path::new(path).exists(),
    }
}
