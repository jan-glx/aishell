#!/bin/bash
set -e

cp -f ~/api.py ./
cp -fr ~/app ./
cp -a /etc/systemd/system/fastapi.service ./fastapi.service
cp -a /etc/nginx/sites-available/shell.gleixner.xyz ./nginx-shell.gleixner.xyz
cp -a /var/www/shell.gleixner.xyz/openapi.yaml ./openapi.yaml

