// PM2 ecosystem config — mexc-trade-bot
// Запуск: pm2 start ecosystem.config.js
// Только paper: pm2 start ecosystem.config.js --only researcher-paper
// Только live:  pm2 start ecosystem.config.js --only researcher-live

module.exports = {
  apps: [
    // ── Paper Trading (всегда запущен) ────────────────────────────────────────
    {
      name: 'researcher-paper',
      script: '/home/bot/venv/bin/python',
      args: '-m uvicorn researcher.app.main:app --host 0.0.0.0 --port 8100',
      cwd: '/home/bot/mexc-trade-bot',
      interpreter: 'none',
      env_file: '/home/bot/mexc-trade-bot/researcher/.env.paper',

      // Авто-рестарт
      autorestart: true,
      watch: false,
      max_memory_restart: '900M',
      restart_delay: 5000,       // 5 сек пауза перед рестартом
      max_restarts: 10,           // после 10 падений — не рестартовать
      min_uptime: '30s',         // если упал раньше 30с — считается краш

      // Логи
      output: '/home/bot/logs/paper-out.log',
      error: '/home/bot/logs/paper-err.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      merge_logs: true,
    },

    // ── Live Trading (запускать вручную когда готов) ───────────────────────────
    {
      name: 'researcher-live',
      script: '/home/bot/venv/bin/python',
      args: '-m uvicorn researcher.app.main:app --host 0.0.0.0 --port 8101',
      cwd: '/home/bot/mexc-trade-bot',
      interpreter: 'none',
      env_file: '/home/bot/mexc-trade-bot/researcher/.env.live',

      // НЕ запускать автоматически — только вручную
      autorestart: true,
      watch: false,
      max_memory_restart: '900M',
      restart_delay: 10000,      // 10 сек — live требует осторожности
      max_restarts: 5,            // меньше рестартов для live
      min_uptime: '60s',

      // Логи отдельные
      output: '/home/bot/logs/live-out.log',
      error: '/home/bot/logs/live-err.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      merge_logs: true,
    },
  ],
};
