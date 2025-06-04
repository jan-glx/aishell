#!/bin/bash
set -e

# 1. System packages
sudo apt update
sudo apt install -y python3-venv python3-pip nginx certbot python3-certbot-nginx

# 2. Virtual environment
python3 -m venv ~/ec2-shell-env
source ~/ec2-shell-env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. Systemd service
sudo cp fastapi.service /etc/systemd/system/fastapi.service
sudo systemctl daemon-reload
sudo systemctl enable fastapi.service

# 4. Nginx config
sudo ln -sf $(pwd)/nginx-shell.gleixner.xyz /etc/nginx/sites-available/shell.gleixner.xyz
sudo ln -sf /etc/nginx/sites-available/shell.gleixner.xyz /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 5. Certbot (interactive step)
sudo certbot --nginx -d shell.gleixner.xyz

# 6. Start service
sudo systemctl start fastapi.service
