use std::{path::PathBuf, time::Duration};

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
    pub label: &'static str,
    pub value: String,
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
}

impl App {
    pub fn new(config: AppConfig, paths: RuntimePaths) -> Result<Self> {
        let supervisor = Supervisor::new(build_service_specs(&config, &paths)?);
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
            let status_text = match &item.status {
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
                vec![
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
                ]
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
        let list = List::new(items)
            .block(
                Block::default()
                    .title("Config (Enter/e to edit, w to save)")
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
            KeyCode::Char('s') => {
                self.run_service_action("start", |app, name| app.supervisor.start_service(name))
            }
            KeyCode::Char('x') => {
                self.run_service_action("stop", |app, name| app.supervisor.stop_service(name))
            }
            KeyCode::Char('r') => {
                self.run_service_action("restart", |app, name| app.supervisor.restart_service(name))
            }
            KeyCode::Char('S') => self.set_message(self.supervisor.start_all()),
            KeyCode::Char('X') => self.set_message(self.supervisor.stop_all()),
            KeyCode::Char('R') => self.set_message(self.supervisor.restart_all()),
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

    fn run_service_action(
        &mut self,
        verb: &str,
        action: impl FnOnce(&mut Self, ServiceName) -> Result<()>,
    ) {
        let result = self
            .selected_service_name()
            .ok_or_else(|| anyhow::anyhow!("no service selected"))
            .and_then(|name| action(self, name));
        self.set_message(result.map(|_| format!("{verb} ok")));
    }

    fn set_message<T>(&mut self, result: Result<T>) {
        self.last_message = match result {
            Ok(_) => "ok".to_string(),
            Err(error) => error.to_string(),
        };
    }

    fn config_fields(&self) -> Vec<ConfigField> {
        vec![
            ConfigField {
                label: "display",
                value: self.config.display.clone(),
            },
            ConfigField {
                label: "screen",
                value: self.config.screen.clone(),
            },
            ConfigField {
                label: "vnc_password_file",
                value: self.config.vnc_password_file.clone(),
            },
            ConfigField {
                label: "novnc_listen_port",
                value: self.config.novnc_listen_port.to_string(),
            },
            ConfigField {
                label: "qq_path",
                value: self.config.qq_path.clone(),
            },
            ConfigField {
                label: "node_path",
                value: self.config.node_path.clone(),
            },
            ConfigField {
                label: "snowluma_entry",
                value: self.config.snowluma_entry.clone(),
            },
            ConfigField {
                label: "snowluma_cwd",
                value: self.config.snowluma_cwd.clone(),
            },
            ConfigField {
                label: "state_root",
                value: self.config.state_root.clone(),
            },
            ConfigField {
                label: "tick_ms",
                value: self.config.tick_ms.to_string(),
            },
        ]
    }

    fn begin_edit_current_field(&mut self) {
        if let Some(field) = self.config_fields().get(self.selected_config) {
            self.editing = true;
            self.input_buffer = field.value.clone();
        }
    }

    fn apply_current_field(&mut self) {
        let value = self.input_buffer.trim().to_string();
        let label = self
            .config_fields()
            .get(self.selected_config)
            .map(|field| field.label)
            .unwrap_or_default();

        let result = match label {
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
            "tick_ms" => value
                .parse::<u64>()
                .map(|tick| self.config.tick_ms = tick)
                .map_err(|error| anyhow::anyhow!(error)),
            _ => Ok(()),
        };

        self.set_message(result);
    }

    fn save_config(&mut self) {
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
