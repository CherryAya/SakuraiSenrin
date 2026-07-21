use std::{
    collections::BTreeMap,
    env, fs,
    path::{Path, PathBuf},
};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

pub const CONFIG_FILE_NAME: &str = "config.toml";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default)]
pub struct AppConfig {
    pub display: String,
    pub screen: String,
    pub vnc_password_file: String,
    pub novnc_listen_port: u16,
    pub qq_path: String,
    pub node_path: String,
    pub snowluma_entry: String,
    pub snowluma_cwd: String,
    pub state_root: String,
    pub log_dir: String,
    pub run_dir: String,
    pub tick_ms: u64,
    pub extra_args: BTreeMap<String, Vec<String>>,
    pub extra_env: BTreeMap<String, BTreeMap<String, String>>,
}

#[derive(Debug, Clone)]
pub struct RuntimePaths {
    pub config_dir: PathBuf,
    pub config_file: PathBuf,
    pub state_root: PathBuf,
    pub log_dir: PathBuf,
    pub run_dir: PathBuf,
}

impl Default for AppConfig {
    fn default() -> Self {
        let extra_args = [
            ("xvfb", Vec::new()),
            ("fluxbox", Vec::new()),
            ("x11vnc", Vec::new()),
            ("novnc", Vec::new()),
            ("qq", Vec::new()),
            ("snowluma", Vec::new()),
        ]
        .into_iter()
        .map(|(name, value)| (name.to_string(), value))
        .collect();

        let extra_env = [
            ("xvfb", BTreeMap::new()),
            ("fluxbox", BTreeMap::new()),
            ("x11vnc", BTreeMap::new()),
            ("novnc", BTreeMap::new()),
            ("qq", BTreeMap::new()),
            ("snowluma", BTreeMap::new()),
        ]
        .into_iter()
        .map(|(name, value)| (name.to_string(), value))
        .collect();

        Self {
            display: ":1".to_string(),
            screen: "0 1920x1080x16".to_string(),
            vnc_password_file: "~/.vnc/passwd".to_string(),
            novnc_listen_port: 6081,
            qq_path: "/opt/QQ/qq".to_string(),
            node_path: "node".to_string(),
            snowluma_entry: "/home/sc/download/snowluma/index.mjs".to_string(),
            snowluma_cwd: "/home/sc/download/snowluma".to_string(),
            state_root: "~/.local/state/snowluma-tui".to_string(),
            log_dir: "~/.local/state/snowluma-tui/logs".to_string(),
            run_dir: "~/.local/state/snowluma-tui/run".to_string(),
            tick_ms: 1000,
            extra_args,
            extra_env,
        }
    }
}

impl AppConfig {
    pub fn load_or_create(config_dir: Option<PathBuf>) -> Result<(Self, RuntimePaths)> {
        let config_dir = config_dir.unwrap_or_else(default_config_dir);
        fs::create_dir_all(&config_dir)
            .with_context(|| format!("failed to create config dir {}", config_dir.display()))?;
        let config_file = config_dir.join(CONFIG_FILE_NAME);

        let config = if config_file.exists() {
            let raw = fs::read_to_string(&config_file)
                .with_context(|| format!("failed to read {}", config_file.display()))?;
            toml::from_str(&raw)
                .with_context(|| format!("failed to parse {}", config_file.display()))?
        } else {
            let config = Self::default();
            config.save_to(&config_file)?;
            config
        };

        let paths = RuntimePaths::from_config(config_dir, &config)?;
        paths.ensure()?;
        Ok((config, paths))
    }

    pub fn save_to(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("failed to create {}", parent.display()))?;
        }
        let content = toml::to_string_pretty(self).context("failed to serialize config")?;
        fs::write(path, content).with_context(|| format!("failed to write {}", path.display()))
    }

    pub fn display_number(&self) -> Result<u16> {
        let raw = self.display.trim().trim_start_matches(':');
        raw.parse::<u16>()
            .with_context(|| format!("invalid display value {}", self.display))
    }

    pub fn args_for(&self, service_name: &str) -> Vec<String> {
        self.extra_args
            .get(service_name)
            .cloned()
            .unwrap_or_default()
    }

    pub fn env_for(&self, service_name: &str) -> BTreeMap<String, String> {
        self.extra_env
            .get(service_name)
            .cloned()
            .unwrap_or_default()
    }
}

impl RuntimePaths {
    pub fn from_config(config_dir: PathBuf, config: &AppConfig) -> Result<Self> {
        let config_file = config_dir.join(CONFIG_FILE_NAME);
        let state_root = expand_tilde(&config.state_root)?;
        let log_dir = expand_tilde(&config.log_dir)?;
        let run_dir = expand_tilde(&config.run_dir)?;
        Ok(Self {
            config_dir,
            config_file,
            state_root,
            log_dir,
            run_dir,
        })
    }

    pub fn ensure(&self) -> Result<()> {
        fs::create_dir_all(&self.config_dir)
            .with_context(|| format!("failed to create {}", self.config_dir.display()))?;
        fs::create_dir_all(&self.state_root)
            .with_context(|| format!("failed to create {}", self.state_root.display()))?;
        fs::create_dir_all(&self.log_dir)
            .with_context(|| format!("failed to create {}", self.log_dir.display()))?;
        fs::create_dir_all(&self.run_dir)
            .with_context(|| format!("failed to create {}", self.run_dir.display()))?;
        Ok(())
    }
}

pub fn default_config_dir() -> PathBuf {
    if let Some(value) = env::var_os("XDG_CONFIG_HOME") {
        return PathBuf::from(value).join("snowluma-tui");
    }
    if let Some(value) = env::var_os("HOME") {
        return PathBuf::from(value).join(".config").join("snowluma-tui");
    }
    PathBuf::from(".snowluma-tui")
}

pub fn expand_tilde(input: &str) -> Result<PathBuf> {
    if input == "~" {
        return home_dir();
    }
    if let Some(stripped) = input.strip_prefix("~/") {
        return Ok(home_dir()?.join(stripped));
    }
    Ok(PathBuf::from(input))
}

fn home_dir() -> Result<PathBuf> {
    env::var_os("HOME")
        .map(PathBuf::from)
        .context("HOME is not set")
}

#[cfg(test)]
mod tests {
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::*;

    fn unique_temp_dir(name: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time went backwards")
            .as_nanos();
        env::temp_dir().join(format!("snowluma-tui-{name}-{stamp}"))
    }

    #[test]
    fn default_config_has_known_services() {
        let config = AppConfig::default();
        assert!(config.extra_args.contains_key("xvfb"));
        assert!(config.extra_args.contains_key("snowluma"));
        assert!(config.extra_env.contains_key("qq"));
        assert!(config.log_dir.ends_with("/logs"));
        assert!(config.run_dir.ends_with("/run"));
    }

    #[test]
    fn expand_tilde_works() {
        let home = home_dir().expect("home dir");
        assert_eq!(expand_tilde("~/demo").expect("expanded"), home.join("demo"));
    }

    #[test]
    fn load_or_create_round_trips_config_file() {
        let config_dir = unique_temp_dir("config");
        fs::create_dir_all(&config_dir).expect("config dir should exist");
        let config_file = config_dir.join(CONFIG_FILE_NAME);
        let mut config = AppConfig::default();
        config.state_root = config_dir.join("state").display().to_string();
        config.log_dir = config_dir.join("state/logs").display().to_string();
        config.run_dir = config_dir.join("state/run").display().to_string();
        config
            .save_to(&config_file)
            .expect("config fixture should save");

        let (mut config, paths) =
            AppConfig::load_or_create(Some(config_dir.clone())).expect("config should load");
        assert!(paths.config_file.exists());

        config.display = ":9".to_string();
        config.log_dir = config_dir.join("custom-logs").display().to_string();
        config
            .save_to(&paths.config_file)
            .expect("config should save back to disk");

        let (reloaded, reloaded_paths) =
            AppConfig::load_or_create(Some(config_dir.clone())).expect("config should reload");
        assert_eq!(reloaded.display, ":9");
        assert_eq!(
            reloaded.log_dir,
            config_dir.join("custom-logs").display().to_string()
        );
        assert_eq!(reloaded_paths.log_dir, config_dir.join("custom-logs"));

        fs::remove_dir_all(config_dir).expect("temp config dir should be removable");
    }
}
