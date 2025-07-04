
-include .env
export

.PHONY: setup venv deploy-service deploy-openapi deploy-nginx deploy test-service test

deploy: setup deploy-service deploy-openapi deploy-nginx

setup: venv

venv: ../venv-aishell/touchfile

PYTHON=../venv-aishell/bin/python

$(PYTHON):
	python3 -m venv ../venv-aishell

../venv-aishell/touchfile: $(PYTHON)
	$(PYTHON) -m pip install  --no-warn-script-location --upgrade pip wheel
	$(PYTHON) -m pip install .
	touch ../venv-aishell/touchfile
	
deploy-service:
	sudo cp deploy/aishell.service /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable aishell
	sudo systemctl restart aishell

deploy-openapi:
	sudo mkdir -p /var/www/$(YOUR_DOMAIN)
	envsubst < deploy/openapi.yaml | sudo tee /var/www/$(YOUR_DOMAIN)/openapi.yaml > /dev/null

deploy-nginx:
	envsubst < deploy/nginx-template.conf | sudo tee /etc/nginx/sites-available/$(YOUR_DOMAIN) > /dev/null
	sudo ln -sf /etc/nginx/sites-available/$(YOUR_DOMAIN) /etc/nginx/sites-enabled/
	sudo nginx -t && sudo systemctl reload nginx


test-syntax: ../venv-aishell/touchfile
	$(PYTHON) -c "import aishell"

test-uvicornapp: ../venv-aishell/touchfile
	(timeout 6 $(PYTHON) -m uvicorn aishell:app --host 0.0.0.0 --port 8002) & 
	sleep 3 && curl --fail http://localhost:8002/execute || (sleep 5 ; fail)


test: test-service test-uvicornapp
