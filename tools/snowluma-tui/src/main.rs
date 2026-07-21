mod app;
mod config;
mod diagnostics;
mod service;
mod state;
mod supervisor;

use std::io;

use anyhow::Result;
use crossterm::{
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::{Terminal, backend::CrosstermBackend};

use crate::{
    app::{App, config_path_from_args},
    config::AppConfig,
};

fn main() -> Result<()> {
    let config_dir = config_path_from_args();
    let (config, paths) = AppConfig::load_or_create(config_dir)?;
    let mut app = App::new(config, paths)?;

    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let result = app.run(&mut terminal);

    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;

    result
}
