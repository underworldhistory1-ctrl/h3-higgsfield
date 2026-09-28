// PM2 process for the local H3 Studio ↔ Comfy Cloud bridge.
const envFile = process.env.H3_CLOUD_ENV_FILE;
const args = envFile ? ["--env-file", envFile] : [];
if (process.env.H3_ACCEPT_CLOUD_VAE === "1") args.push("--accept-cloud-vae");

module.exports = {
  apps: [
    {
      name: "h3-cloud-bridge",
      cwd: __dirname,
      script: "bridge.py",
      interpreter: `${__dirname}/.venv/bin/python`,
      args,
      autorestart: true,
    },
  ],
};
