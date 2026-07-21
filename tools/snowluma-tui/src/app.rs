use std::{
    collections::BTreeMap,
    path::PathBuf,
    sync::mpsc::{self, Receiver, Sender},
    thread,
    time::Duration,
};

use anyhow::Result;
use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyModifiers};
use ratatui::{
    Frame, Terminal,
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Text},
    widgets::{
        Block, Borders, Cell, Clear, List, ListItem, ListState, Paragraph, Row, Table, Tabs, Wrap,
    },
};

use crate::{
    config::{AppConfig, RuntimePaths},
    diagnostics::{DiagnosticItem, collect_diagnostics, tail_log},
    service::{ServiceName, build_service_specs},
    state::{ServiceStatus, ServiceStatusSnapshot},
    supervisor::Supervisor,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tab {
    Overview,
    Logs,
    Config,
    Diagnostics,
}

impl Tab {
    const ALL: [Tab; 4] = [Tab::Overview, Tab::Logs, Tab::Config, Tab::Diagnostics];

    fn title(self) -> &'static str {
        match self {
            Tab::Overview => "Overview",
            Tab::Logs => "Logs",
            Tab::Config => "Config",
            Tab::Diagnostics => "Diagnostics",
        }
    }
}

#[derive(Debug, Clone)]
pub struct ConfigField {
    pub key: String,
    pub label: String,
    pub value: String,
    pub help: String,
}

pub struct App {
    config: AppConfig,
    paths: RuntimePaths,
    supervisor: Supervisor,
    statuses: Vec<ServiceStatusSnapshot>,
    diagnostics: Vec<DiagnosticItem>,
    logs: Vec<String>,
    last_message: String,
    selected_service: usize,
    selected_config: usize,
    active_tab: Tab,
    editing: bool,
    input_buffer: String,
    should_quit: bool,
    action_rx: Receiver<ActionOutcome>,
    action_tx: Sender<ActionOutcome>,
    pending_statuses: BTreeMap<ServiceName, ServiceStatus>,
}

#[derive(Debug, Clone, Copy)]
enum ActionRequest {
    Start(ServiceName),
    Stop(ServiceName),
    Restart(ServiceName),
    StartAll,
    StopAll,
    RestartAll,
}

#[derive(Debug)]
struct ActionOutcome {
    affected_services: Vec<ServiceName>,
    message: Result<String, String>,
}

impl App {
    pub fn new(config: AppConfig, paths: RuntimePaths) -> Result<Self> {
        let supervisor = Supervisor::new(build_service_specs(&config, &paths)?);
        let (action_tx, action_rx) = mpsc::channel();
        let mut app = Self {
            config,
            paths,
            statuses: Vec::new(),
            diagnostics: Vec::new(),
            logs: Vec::new(),
            last_message: "Ready".to_string(),
            selected_service: 0,
            selected_config: 0,
            active_tab: Tab::Overview,
            editing: false,
            input_buffer: String::new(),
            should_quit: false,
            action_rx,
            action_tx,
            pending_statuses: BTreeMap::new(),
            supervisor,
        };
        app.refresh()?;
        Ok(app)
    }

    pub fn run(
        &mut self,
        terminal: &mut Terminal<CrosstermBackend<std::io::Stdout>>,
    ) -> Result<()> {
        while !self.should_quit {
            self.poll_action_completion()?;
            terminal.draw(|frame| self.draw(frame))?;
            if event::poll(Duration::from_millis(self.config.tick_ms))? {
                let event = event::read()?;
                if let Event::Key(key) = event {
                    self.handle_key(key)?;
                }
            } else {
                self.refresh()?;
            }
        }
        Ok(())
    }

    fn refresh(&mut self) -> Result<()> {
        self.statuses = self.supervisor.statuses();
        self.diagnostics = collect_diagnostics(&self.config, &self.paths, &self.collect_specs());
        self.refresh_logs()?;
        Ok(())
    }

    fn refresh_logs(&mut self) -> Result<()> {
        let Some(service) = self.selected_service_name() else {
            self.logs = vec!["<no services>".to_string()];
            return Ok(());
        };
        let status = self
            .statuses
            .iter()
            .find(|item| item.name == service)
            .map(|item| item.log_file.clone())
            .unwrap_or_else(|| self.paths.log_dir.join(format!("{}.log", service.as_str())));
        self.logs = tail_log(&status, 18)?;
        Ok(())
    }

    fn collect_specs(&self) -> Vec<crate::service::ServiceSpec> {
        build_service_specs(&self.config, &self.paths).unwrap_or_default()
    }

    fn draw(&self, frame: &mut Frame<'_>) {
        let chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(3),
                Constraint::Min(10),
                Constraint::Length(3),
            ])
            .split(frame.area());

        self.draw_tabs(frame, chunks[0]);
        match self.active_tab {
            Tab::Overview => self.draw_overview(frame, chunks[1]),
            Tab::Logs => self.draw_logs(frame, chunks[1]),
            Tab::Config => self.draw_config(frame, chunks[1]),
            Tab::Diagnostics => self.draw_diagnostics(frame, chunks[1]),
        }
        self.draw_footer(frame, chunks[2]);
        if self.editing {
            self.draw_input_modal(frame, centered_rect(70, 20, frame.area()));
        }
    }

    fn draw_tabs(&self, frame: &mut Frame<'_>, area: Rect) {
        let titles = Tab::ALL.into_iter().map(|tab| Line::from(tab.title()));
        let tabs = Tabs::new(titles.collect::<Vec<_>>())
            .select(
                Tab::ALL
                    .iter()
                    .position(|tab| *tab == self.active_tab)
                    .unwrap_or(0),
            )
            .block(Block::default().title("Snowluma TUI").borders(Borders::ALL))
            .highlight_style(
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            );
        frame.render_widget(tabs, area);
    }

    fn draw_overview(&self, frame: &mut Frame<'_>, area: Rect) {
        let columns = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(62), Constraint::Percentage(38)])
            .split(area);

        let header = Row::new(vec!["Service", "Status", "PID", "Started"]).style(
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        );

        let rows = self.statuses.iter().map(|item| {
            let status_text = match self
                .pending_statuses
                .get(&item.name)
                .unwrap_or(&item.status)
            {
                ServiceStatus::Stopped => "stopped".to_string(),
                ServiceStatus::Starting => "starting".to_string(),
                ServiceStatus::Running => "running".to_string(),
                ServiceStatus::Stopping => "stopping".to_string(),
                ServiceStatus::Failed(reason) => format!("failed: {reason}"),
            };
            Row::new(vec![
                item.label.clone(),
                status_text,
                item.pid
                    .map(|pid| pid.to_string())
                    .unwrap_or_else(|| "-".to_string()),
                item.started_at.clone().unwrap_or_else(|| "-".to_string()),
            ])
        });
        let table = Table::new(
            rows,
            [
                Constraint::Length(10),
                Constraint::Length(24),
                Constraint::Length(8),
                Constraint::Min(24),
            ],
        )
        .header(header)
        .block(Block::default().title("Services").borders(Borders::ALL))
        .row_highlight_style(Style::default().bg(Color::Blue))
        .highlight_symbol("> ");
        let mut state = TableStateAdapter::new(self.selected_service);
        frame.render_stateful_widget(table, columns[0], &mut state.0);

        let detail = self
            .selected_service_name()
            .and_then(|name| self.statuses.iter().find(|item| item.name == name))
            .map(|item| {
                let mut lines = vec![
                    Line::from(format!("Service: {}", item.label)),
                    Line::from(format!(
                        "PID: {}",
                        item.pid
                            .map(|value| value.to_string())
                            .unwrap_or_else(|| "-".to_string())
                    )),
                    Line::from(format!("Log: {}", item.log_file.display())),
                    Line::from(format!(
                        "Started: {}",
                        item.started_at.clone().unwrap_or_else(|| "-".to_string())
                    )),
                ];
                if let Some(exit) = &item.exit_state {
                    lines.push(Line::from(format!(
                        "Last exit: code={:?} signal={:?} expected_stop={}",
                        exit.exit_code, exit.signal, exit.expected_stop
                    )));
                    lines.push(Line::from(format!("Finished: {}", exit.finished_at)));
                    if !exit.output_tail.is_empty() {
                        lines.push(Line::from("Output:"));
                        for line in exit.output_tail.iter().take(4) {
                            lines.push(Line::from(format!("  {line}")));
                        }
                    }
                }
                lines
            })
            .unwrap_or_else(|| vec![Line::from("No service selected")]);
        let paragraph = Paragraph::new(Text::from(detail))
            .block(Block::default().title("Details").borders(Borders::ALL))
            .wrap(Wrap { trim: false });
        frame.render_widget(paragraph, columns[1]);
    }

    fn draw_logs(&self, frame: &mut Frame<'_>, area: Rect) {
        let title = self
            .selected_service_name()
            .map(|name| format!("Logs: {}", name.label()))
            .unwrap_or_else(|| "Logs".to_string());
        let text = self.logs.join("\n");
        let paragraph = Paragraph::new(text)
            .block(Block::default().title(title).borders(Borders::ALL))
            .wrap(Wrap { trim: false });
        frame.render_widget(paragraph, area);
    }

    fn draw_config(&self, frame: &mut Frame<'_>, area: Rect) {
        let fields = self.config_fields();
        let items = fields
            .iter()
            .map(|field| ListItem::new(format!("{} = {}", field.label, field.value)))
            .collect::<Vec<_>>();
        let help_text = fields
            .get(self.selected_config)
            .map(|field| field.help.clone())
            .unwrap_or_else(|| "Select a config field".to_string());
        let list = List::new(items)
            .block(
                Block::default()
                    .title(format!("Config (Enter/e edit, w save) | {}", help_text))
                    .borders(Borders::ALL),
            )
            .highlight_style(Style::default().bg(Color::Blue))
            .highlight_symbol("> ");
        let mut state = ListState::default();
        state.select(Some(self.selected_config));
        frame.render_stateful_widget(list, area, &mut state);
    }

    fn draw_diagnostics(&self, frame: &mut Frame<'_>, area: Rect) {
        let rows = self.diagnostics.iter().map(|item| {
            Row::new(vec![
                Cell::from(item.label.clone()),
                Cell::from(item.status.clone()),
            ])
        });
        let table = Table::new(
            rows,
            [Constraint::Percentage(45), Constraint::Percentage(55)],
        )
        .header(
            Row::new(vec!["Item", "Status"]).style(
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ),
        )
        .block(Block::default().title("Diagnostics").borders(Borders::ALL));
        frame.render_widget(table, area);
    }

    fn draw_footer(&self, frame: &mut Frame<'_>, area: Rect) {
        let text = format!(
            "q quit | tab next page | j/k move | s start | x stop | r restart | S start all | X stop all | R restart all | g refresh | {}",
            self.last_message
        );
        frame.render_widget(
            Paragraph::new(text).block(Block::default().borders(Borders::ALL)),
            area,
        );
    }

    fn draw_input_modal(&self, frame: &mut Frame<'_>, area: Rect) {
        frame.render_widget(Clear, area);
        let paragraph = Paragraph::new(self.input_buffer.as_str())
            .block(
                Block::default()
                    .title("Edit value (Enter save, Esc cancel)")
                    .borders(Borders::ALL),
            )
            .wrap(Wrap { trim: false });
        frame.render_widget(paragraph, area);
    }

    fn handle_key(&mut self, key: KeyEvent) -> Result<()> {
        if self.editing {
            return self.handle_edit_key(key);
        }

        match key.code {
            KeyCode::Char('q') => self.should_quit = true,
            KeyCode::Tab => {
                self.active_tab = match self.active_tab {
                    Tab::Overview => Tab::Logs,
                    Tab::Logs => Tab::Config,
                    Tab::Config => Tab::Diagnostics,
                    Tab::Diagnostics => Tab::Overview,
                };
            }
            KeyCode::Char('j') | KeyCode::Down => self.move_selection(1),
            KeyCode::Char('k') | KeyCode::Up => self.move_selection(-1),
            KeyCode::Char('g') => {
                let result = self.refresh();
                self.set_message(result);
            }
            KeyCode::Char('s') => self.launch_selected_action("start", ActionKind::Start)?,
            KeyCode::Char('x') => self.launch_selected_action("stop", ActionKind::Stop)?,
            KeyCode::Char('r') => self.launch_selected_action("restart", ActionKind::Restart)?,
            KeyCode::Char('S') => self.launch_action(ActionRequest::StartAll)?,
            KeyCode::Char('X') => self.launch_action(ActionRequest::StopAll)?,
            KeyCode::Char('R') => self.launch_action(ActionRequest::RestartAll)?,
            KeyCode::Char('w') if self.active_tab == Tab::Config => self.save_config(),
            KeyCode::Enter | KeyCode::Char('e') if self.active_tab == Tab::Config => {
                self.begin_edit_current_field()
            }
            _ => {}
        }
        self.refresh()?;
        Ok(())
    }

    fn handle_edit_key(&mut self, key: KeyEvent) -> Result<()> {
        match key.code {
            KeyCode::Esc => {
                self.editing = false;
                self.input_buffer.clear();
            }
            KeyCode::Enter => {
                self.apply_current_field();
                self.editing = false;
                self.input_buffer.clear();
            }
            KeyCode::Backspace => {
                self.input_buffer.pop();
            }
            KeyCode::Char(c) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.input_buffer.push(c);
            }
            _ => {}
        }
        self.refresh()?;
        Ok(())
    }

    fn move_selection(&mut self, delta: i32) {
        match self.active_tab {
            Tab::Config => {
                let total = self.config_fields().len();
                if total == 0 {
                    return;
                }
                self.selected_config = wrap_index(self.selected_config, total, delta);
            }
            _ => {
                let total = self.statuses.len();
                if total == 0 {
                    return;
                }
                self.selected_service = wrap_index(self.selected_service, total, delta);
            }
        }
    }

    fn selected_service_name(&self) -> Option<ServiceName> {
        self.statuses
            .get(self.selected_service)
            .map(|item| item.name)
    }

    fn set_message<T>(&mut self, result: Result<T>) {
        self.last_message = match result {
            Ok(_) => "ok".to_string(),
            Err(error) => error.to_string(),
        };
    }

    fn config_fields(&self) -> Vec<ConfigField> {
        let mut fields = vec![
            string_field(
                "display",
                self.config.display.clone(),
                "X display, for example :1",
            ),
            string_field(
                "screen",
                self.config.screen.clone(),
                "Xvfb screen spec, for example 0 1920x1080x16",
            ),
            string_field(
                "vnc_password_file",
                self.config.vnc_password_file.clone(),
                "Path to the x11vnc password file",
            ),
            string_field(
                "novnc_listen_port",
                self.config.novnc_listen_port.to_string(),
                "TCP port exposed by noVNC",
            ),
            string_field(
                "qq_path",
                self.config.qq_path.clone(),
                "Path to the QQ binary",
            ),
            string_field(
                "node_path",
                self.config.node_path.clone(),
                "Node.js executable used for snowluma",
            ),
            string_field(
                "snowluma_entry",
                self.config.snowluma_entry.clone(),
                "Path to snowluma index.mjs",
            ),
            string_field(
                "snowluma_cwd",
                self.config.snowluma_cwd.clone(),
                "Working directory for the snowluma node process",
            ),
            string_field(
                "state_root",
                self.config.state_root.clone(),
                "Base state directory for status and logs",
            ),
            string_field(
                "log_dir",
                self.config.log_dir.clone(),
                "Directory for service log files",
            ),
            string_field(
                "run_dir",
                self.config.run_dir.clone(),
                "Directory for pid/state files",
            ),
            string_field(
                "tick_ms",
                self.config.tick_ms.to_string(),
                "UI polling interval in milliseconds",
            ),
        ];

        for service in ServiceName::ALL {
            let name = service.as_str();
            fields.push(string_field(
                &format!("extra_args.{name}"),
                format_args_for_input(&self.config.args_for(name)),
                "JSON array string, for example [\"--foo\",\"bar\"]",
            ));
            fields.push(string_field(
                &format!("extra_env.{name}"),
                format_env_for_input(&self.config.env_for(name)),
                "JSON object string, for example {\"KEY\":\"VALUE\"}",
            ));
        }

        fields
    }

    fn begin_edit_current_field(&mut self) {
        if let Some(field) = self.config_fields().get(self.selected_config) {
            self.editing = true;
            self.input_buffer = field.value.clone();
        }
    }

    fn apply_current_field(&mut self) {
        let value = self.input_buffer.trim().to_string();
        let key = self
            .config_fields()
            .get(self.selected_config)
            .map(|field| field.key.clone())
            .unwrap_or_default();

        let result = match key.as_str() {
            "display" => {
                self.config.display = value;
                Ok(())
            }
            "screen" => {
                self.config.screen = value;
                Ok(())
            }
            "vnc_password_file" => {
                self.config.vnc_password_file = value;
                Ok(())
            }
            "novnc_listen_port" => value
                .parse::<u16>()
                .map(|port| self.config.novnc_listen_port = port)
                .map_err(|error| anyhow::anyhow!(error)),
            "qq_path" => {
                self.config.qq_path = value;
                Ok(())
            }
            "node_path" => {
                self.config.node_path = value;
                Ok(())
            }
            "snowluma_entry" => {
                self.config.snowluma_entry = value;
                Ok(())
            }
            "snowluma_cwd" => {
                self.config.snowluma_cwd = value;
                Ok(())
            }
            "state_root" => {
                self.config.state_root = value;
                Ok(())
            }
            "log_dir" => {
                self.config.log_dir = value;
                Ok(())
            }
            "run_dir" => {
                self.config.run_dir = value;
                Ok(())
            }
            "tick_ms" => value
                .parse::<u64>()
                .map(|tick| self.config.tick_ms = tick)
                .map_err(|error| anyhow::anyhow!(error)),
            key if key.starts_with("extra_args.") => {
                let service = key.trim_start_matches("extra_args.");
                parse_args_input(&value).map(|parsed| {
                    self.config.extra_args.insert(service.to_string(), parsed);
                })
            }
            key if key.starts_with("extra_env.") => {
                let service = key.trim_start_matches("extra_env.");
                parse_env_input(&value).map(|parsed| {
                    self.config.extra_env.insert(service.to_string(), parsed);
                })
            }
            _ => Ok(()),
        };

        self.set_message(result);
    }

    fn save_config(&mut self) {
        if !self.pending_statuses.is_empty() {
            self.last_message = "wait for running service action before saving".to_string();
            return;
        }
        let result = self
            .config
            .save_to(&self.paths.config_file)
            .and_then(|_| {
                let paths = RuntimePaths::from_config(self.paths.config_dir.clone(), &self.config)?;
                paths.ensure()?;
                self.paths = paths;
                self.supervisor = Supervisor::new(build_service_specs(&self.config, &self.paths)?);
                Ok(())
            })
            .map(|_| "config saved".to_string());
        self.set_message(result);
    }

    fn launch_selected_action(&mut self, verb: &str, kind: ActionKind) -> Result<()> {
        let name = self
            .selected_service_name()
            .ok_or_else(|| anyhow::anyhow!("no service selected"))?;
        let request = match kind {
            ActionKind::Start => ActionRequest::Start(name),
            ActionKind::Stop => ActionRequest::Stop(name),
            ActionKind::Restart => ActionRequest::Restart(name),
        };
        self.launch_action_with_message(
            request,
            format!("{verb} {}...", name.label()),
            match kind {
                ActionKind::Start => ServiceStatus::Starting,
                ActionKind::Stop | ActionKind::Restart => ServiceStatus::Stopping,
            },
        )?;
        Ok(())
    }

    fn launch_action(&mut self, request: ActionRequest) -> Result<()> {
        let (message, status) = action_context(&request);
        self.launch_action_with_message(request, message, status)
    }

    fn launch_action_with_message(
        &mut self,
        request: ActionRequest,
        in_progress_message: String,
        pending_status: ServiceStatus,
    ) -> Result<()> {
        let scope = action_scope(&self.supervisor, &request);
        if self.action_conflicts(&scope) {
            self.last_message = "another action already touches one of those services".to_string();
            return Ok(());
        }
        let pending = scope
            .iter()
            .map(|service| (*service, pending_status.clone()))
            .collect::<BTreeMap<_, _>>();
        self.pending_statuses.extend(pending);

        let supervisor = self.supervisor.clone();
        let tx = self.action_tx.clone();
        thread::spawn(move || {
            let message =
                run_action_request(&supervisor, request).map_err(|error| error.to_string());
            let _ = tx.send(ActionOutcome {
                affected_services: scope,
                message,
            });
        });
        self.last_message = in_progress_message;
        Ok(())
    }

    fn poll_action_completion(&mut self) -> Result<()> {
        loop {
            match self.action_rx.try_recv() {
                Ok(outcome) => {
                    for service in outcome.affected_services {
                        self.pending_statuses.remove(&service);
                    }
                    self.last_message = match outcome.message {
                        Ok(message) => message,
                        Err(message) => message,
                    };
                    self.refresh()?;
                }
                Err(std::sync::mpsc::TryRecvError::Empty) => break,
                Err(std::sync::mpsc::TryRecvError::Disconnected) => break,
            }
        }
        Ok(())
    }

    fn action_conflicts(&self, scope: &[ServiceName]) -> bool {
        scope
            .iter()
            .any(|service| self.pending_statuses.contains_key(service))
    }
}

#[derive(Debug, Clone, Copy)]
enum ActionKind {
    Start,
    Stop,
    Restart,
}

fn run_action_request(supervisor: &Supervisor, request: ActionRequest) -> Result<String> {
    match request {
        ActionRequest::Start(name) => {
            supervisor.start_service(name)?;
            Ok(format!("start {} ok", name.label()))
        }
        ActionRequest::Stop(name) => {
            supervisor.stop_service(name)?;
            Ok(format!("stop {} ok", name.label()))
        }
        ActionRequest::Restart(name) => {
            supervisor.restart_service(name)?;
            Ok(format!("restart {} ok", name.label()))
        }
        ActionRequest::StartAll => {
            supervisor.start_all()?;
            Ok("start all ok".to_string())
        }
        ActionRequest::StopAll => {
            supervisor.stop_all()?;
            Ok("stop all ok".to_string())
        }
        ActionRequest::RestartAll => {
            supervisor.restart_all()?;
            Ok("restart all ok".to_string())
        }
    }
}

fn action_scope(supervisor: &Supervisor, request: &ActionRequest) -> Vec<ServiceName> {
    match request {
        ActionRequest::Start(name) => supervisor.start_sequence(*name),
        ActionRequest::Stop(name) => supervisor.stop_sequence(*name),
        ActionRequest::Restart(name) => supervisor.restart_sequence(*name),
        ActionRequest::StartAll | ActionRequest::StopAll | ActionRequest::RestartAll => {
            ServiceName::ALL.to_vec()
        }
    }
}

fn action_context(request: &ActionRequest) -> (String, ServiceStatus) {
    match request {
        ActionRequest::Start(name) => (
            format!("starting {}", name.label()),
            ServiceStatus::Starting,
        ),
        ActionRequest::Stop(name) => (
            format!("stopping {}", name.label()),
            ServiceStatus::Stopping,
        ),
        ActionRequest::Restart(name) => (
            format!("restarting {}", name.label()),
            ServiceStatus::Stopping,
        ),
        ActionRequest::StartAll => ("starting all services".to_string(), ServiceStatus::Starting),
        ActionRequest::StopAll => ("stopping all services".to_string(), ServiceStatus::Stopping),
        ActionRequest::RestartAll => (
            "restarting all services".to_string(),
            ServiceStatus::Stopping,
        ),
    }
}

fn string_field(key: &str, value: String, help: &str) -> ConfigField {
    ConfigField {
        key: key.to_string(),
        label: key.to_string(),
        value,
        help: help.to_string(),
    }
}

fn format_args_for_input(args: &[String]) -> String {
    serde_json::to_string(args).unwrap_or_else(|_| "[]".to_string())
}

fn format_env_for_input(env: &BTreeMap<String, String>) -> String {
    serde_json::to_string(env).unwrap_or_else(|_| "{}".to_string())
}

fn parse_args_input(raw: &str) -> Result<Vec<String>> {
    if raw.is_empty() {
        return Ok(Vec::new());
    }
    serde_json::from_str::<Vec<String>>(raw).map_err(anyhow::Error::from)
}

fn parse_env_input(raw: &str) -> Result<BTreeMap<String, String>> {
    if raw.is_empty() {
        return Ok(BTreeMap::new());
    }
    serde_json::from_str::<BTreeMap<String, String>>(raw).map_err(anyhow::Error::from)
}

struct TableStateAdapter(ratatui::widgets::TableState);

impl TableStateAdapter {
    fn new(selected: usize) -> Self {
        let mut state = ratatui::widgets::TableState::default();
        state.select(Some(selected));
        Self(state)
    }
}

fn wrap_index(current: usize, total: usize, delta: i32) -> usize {
    if total == 0 {
        return 0;
    }
    let total = total as i32;
    let next = (current as i32 + delta).rem_euclid(total);
    next as usize
}

fn centered_rect(percent_x: u16, percent_y: u16, r: Rect) -> Rect {
    let popup_layout = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(r);

    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(popup_layout[1])[1]
}

pub fn config_path_from_args() -> Option<PathBuf> {
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        if arg == "--config-dir" {
            return args.next().map(PathBuf::from);
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_config_field_helpers_round_trip() {
        let args = vec!["--foo".to_string(), "bar".to_string()];
        let env = BTreeMap::from([("DISPLAY".to_string(), ":1".to_string())]);

        assert_eq!(
            parse_args_input(&format_args_for_input(&args)).expect("args should parse"),
            args
        );
        assert_eq!(
            parse_env_input(&format_env_for_input(&env)).expect("env should parse"),
            env
        );
    }
}
