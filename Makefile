
-include .env
export

.PHONY: setup venv deploy-service deploy-openapi deploy-nginx deploy test-service test build

deploy: setup build deploy-service deploy-openapi deploy-static deploy-nginx

setup: venv

venv: ../venv-aishell/touchfile

PYTHON=../venv-aishell/bin/python

$(PYTHON):
	python3 -m venv ../venv-aishell

../venv-aishell/touchfile: $(PYTHON) aishell/*.py
	$(PYTHON) -m pip install  --no-warn-script-location --upgrade pip wheel
	touch ../venv-aishell/touchfile
	
	
build: ../venv-aishell/touchfile
	$(PYTHON) -m pip install .

deploy-service: /etc/systemd/system/aishell.service
	sudo systemctl daemon-reload
	sudo systemctl enable aishell
	sudo systemctl stop aishell
	sudo systemctl start aishell
	sudo systemctl status aishell


/etc/systemd/system/aishell.service: deploy/aishell.service
	envsubst < deploy/aishell.service | sudo tee /etc/systemd/system/aishell.service > /dev/null


deploy-static:
	mkdir -p /var/www/$(YOUR_DOMAIN)/static
	cp aishell/*.html /var/www/$(YOUR_DOMAIN)/static/
	cp aishell/favicon.svg /var/www/${YOUR_DOMAIN}/static/
deploy-openapi: /var/www/$(YOUR_DOMAIN)/openapi.yaml

/var/www/$(YOUR_DOMAIN)/openapi.yaml: deploy/openapi.yaml 
	sudo mkdir -p /var/www/$(YOUR_DOMAIN)
	envsubst < deploy/openapi.yaml | sudo tee /var/www/$(YOUR_DOMAIN)/openapi.yaml > /dev/null

deploy-nginx: /etc/nginx/sites-available/$(YOUR_DOMAIN) /etc/nginx/sites-available/$(YOUR_DOMAIN)
	sudo nginx -t && sudo systemctl reload nginx

/etc/nginx/sites-enabled/$(YOUR_DOMAIN) /etc/nginx/sites-available/$(YOUR_DOMAIN): deploy/nginx-template.conf
	envsubst < deploy/nginx-template.conf | sudo tee /etc/nginx/sites-available/$(YOUR_DOMAIN) > /dev/null
	sudo ln -sf /etc/nginx/sites-available/$(YOUR_DOMAIN) /etc/nginx/sites-enabled/


test-syntax: ../venv-aishell/touchfile
	$(PYTHON) -c "import aishell"

test-uvicornapp: ../venv-aishell/touchfile
	timeout 6 $(PYTHON) -m uvicorn aishell:app --host 0.0.0.0 --port 8002 & \
	server_pid=$$! ; \
	( \
		for i in 1 2 3 4 5 ; do \
			curl -s http://localhost:8002/docs > /dev/null && break ; \
			sleep 1 ; \
		done ; \
		resp=$$(curl -sS --fail -X POST \
			-H "Authorization: Bearer ${API_TOKEN}" \
			-H "Content-Type: application/json" \
			-d '{"command":"echo Hello Jan"}' \
			http://localhost:8002/execute) ; \
		echo "$$resp" | grep -q "Hello Jan" || (echo "Test failed, response:" ; echo "$$resp" ; kill $$server_pid ; false) ; \
		kill $$server_pid ;\
		wait $$server_pid ;\
		sleep 1 \
	)

test-deployed-nginx: /etc/systemd/system/aishell.service /etc/nginx/sites-enabled/$(YOUR_DOMAIN)
		resp=$$(curl -sS --fail -X POST \
			-H "Authorization: Bearer ${API_TOKEN}" \
			-H "Content-Type: application/json" \
			-d '{"command":"echo Hello Jan"}' \
			http://$(YOUR_DOMAIN)/execute) ; \
		echo "$$resp" | grep -q "Hello Jan" || (echo "Test failed, response:" ; echo "$$resp" ; false) ; 

test-deployed: /etc/systemd/system/aishell.service
		resp=$$(curl -sS --fail -X POST \
			-H "Authorization: Bearer ${API_TOKEN}" \
			-H "Content-Type: application/json" \
			-d '{"command":"echo Hello Jan"}' \
			127.0.0.1:8000/execute) ; \
		echo "$$resp" | grep -q "Hello Jan" || (echo "Test failed, response:" ; echo "$$resp" ; false) ; 

test: test-service test-uvicornapp

.PHONY: status
status:
	@echo "\n[NGINX STATUS]" && systemctl status nginx --no-pager
	@echo "\n[NGINX LOGS]" && journalctl -u nginx --no-pager -n 30
	@echo "\n[FASTAPI STATUS - aishell]" && systemctl status aishell --no-pager
	@echo "\n[FASTAPI LOGS - aishell]" && journalctl -u aishell --no-pager -n 30

.PHONEY: redeploy
redeploy:
	echo "cd $PWD && sleep 3 && /usr/bin/make deploy > ~/output/deploy.log 2>&1" | at now
