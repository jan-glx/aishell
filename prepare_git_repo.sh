#!/bin/bash
set -e

mkdir -p .
cp -al ~/api.py ./
cp -al ~/app ./
cp -a /etc/systemd/system/fastapi.service ./fastapi.service
cp -a /etc/nginx/sites-available/shell.gleixner.xyz ./nginx-shell.gleixner.xyz

echo -e "output/\nec2-shell-env/\n__pycache__/" > .gitignore
