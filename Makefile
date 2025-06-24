setup:
python -m venv venv-aishell
source venv-aishell/bin/activate && pip install -e .

deploy-service:
sudo cp deploy/aishell.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable aishell
sudo systemctl restart aishell

deploy-openapi:
sudo mkdir -p /var/www/$$YOUR_DOMAIN
sudo ln -sf $$(realpath deploy/openapi.yaml) /var/www/$$YOUR_DOMAIN/openapi.yaml

deploy-nginx:
envsubst < deploy/nginx-template.conf > /etc/nginx/sites-available/$$YOUR_DOMAIN.conf
sudo ln -sf /etc/nginx/sites-available/$$YOUR_DOMAIN.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

.PHONY: setup deploy-service deploy-openapi deploy-nginx

test:
	source venv-aishell/bin/activate && uvicorn aishell.aishell:app --host 0.0.0.0 --port 8002
