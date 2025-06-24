#!/bin/bash
set -e

VENV=~/ec2-shell-env
APP_DIR=~/aishell
SERVICE_NAME=fastapi.service

# Install the package into the virtual environment
$VENV/bin/pip install --upgrade pip
$VENV/bin/pip install $APP_DIR

# Copy and enable the systemd service
sudo cp "$APP_DIR/$SERVICE_NAME" /etc/systemd/system/
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE_NAME
