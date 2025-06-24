## 🚀 Installation

### 1. **Clone the Repository**

```bash
git clone https://github.com/jan-glx/aishell.git
cd aishell
```

### 2. **Install the Python Package**

It is recommended to use a virtual environment:

```bash
python -m venv venv
source venv/bin/activate
pip install -e .
```

This installs the package in editable mode, so changes in the source code are reflected without reinstalling.

---

## ⚙️ Deployment

### 1. Environment Configuration

\
Edit the `.env` file in the project root to set your domain and API token:

```
# .env
YOUR_DOMAIN=example.com
API_TOKEN=supersecret
```

Adjust the values as needed for your deployment. This file is used by both the application and t

### 2. **Systemd Service**

Edit `deploy/fastapi.service` to match your system paths and environment variables, then:

```bash
sudo cp deploy/fastapi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fastapi
sudo systemctl start fastapi
```

### 3. **NGINX Config**

Template `deploy/nginx-template.conf` by replacing `${YOUR_DOMAIN}` with your actual domain:

```bash
export YOUR_DOMAIN=example.com
envsubst < deploy/nginx-template.conf > /etc/nginx/sites-available/${YOUR_DOMAIN}.conf
sudo ln -s /etc/nginx/sites-available/${YOUR_DOMAIN}.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 4. **Host the OpenAPI Spec**

Ensure `deploy/openapi.yaml` is available publicly at:

```
https://${YOUR_DOMAIN}/openapi.yaml
```

To serve this directly from your repo (editable):

```bash
sudo mkdir -p /var/www/${YOUR_DOMAIN}
sudo ln -sf $(realpath deploy/openapi.yaml) /var/www/${YOUR_DOMAIN}/openapi.yaml
```

---

## 🤖 Setting Up a Custom GPT with OpenAI

1. Visit [platform.openai.com/gpts](https://platform.openai.com/gpts)
2. Click "Create a GPT"
3. In the "Configure" tab, scroll to the **API** section
4. Enter your OpenAPI schema URL:
   ```
   https://${YOUR_DOMAIN}/openapi.yaml
   ```
5. OpenAI will introspect and expose available endpoints for you to wire into your GPT
