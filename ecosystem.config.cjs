module.exports = {
  apps: [
    {
      name: "finage-bot",
      cwd: "/home/alphacode/finage",
      script: "/home/alphacode/.local/bin/uv",
      args: "run finage bot",
      interpreter: "none",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      env_file: "/home/alphacode/finage/.env",
      time: true,
    },
    {
      name: "finage-digest",
      cwd: "/home/alphacode/finage",
      script: "/home/alphacode/.local/bin/uv",
      args: "run finage digest send",
      interpreter: "none",
      autorestart: false,
      cron_restart: "0 7 * * 1-5",
      env_file: "/home/alphacode/finage/.env",
      env: {
        TZ: "America/Los_Angeles",
      },
      time: true,
    },
  ],
};
