// PM2 supervises only a rootless, read-only monitor; libvirt owns VM persistence.
module.exports = {
  apps: [{
    name: 'magbot-watch',
    script: './pm2/magbot-watch.py',
    cwd: __dirname,
    interpreter: '/usr/bin/python3',
    watch: false,
    autorestart: true,
    min_uptime: '10s',
    restart_delay: 5000,
    max_memory_restart: '96M',
    kill_timeout: 5000,
    env: { PYTHONUNBUFFERED: '1' }
  }]
};
