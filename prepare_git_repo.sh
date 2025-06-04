#!/bin/bash
set -e
set -e

mkdir -p .
cp -al ~/api.py ./
cp -al ~/app ./
cp -a /etc/systemd/system/fastapi.service ./fastapi.service
cp -a /etc/nginx/sites-available/shell.gleixner.xyz ./nginx-shell.gleixner.xyz
cp -a /var/www/shell.gleixner.xyz/openapi.yaml ./openapi.yaml

echo -e "output/
ec2-shell-env/
__pycache__/" > .gitignore
