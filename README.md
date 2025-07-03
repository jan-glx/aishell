# aishell
A Root shell backend for your AI Chatbot 
## Prerequisite:
A root server and a domain pointing to it.

## 🚀 Installation

### **Clone the Repository**

```sh
git clone https://github.com/jan-glx/aishell.git
```

### Configuration

Create a `.env` file in the project root to specify your domain and come up with an API token:

```bash
cat <<EOF > aishell/.env
YOUR_DOMAIN=example.com
API_TOKEN=supersecret
EOF
```

### Setup and Deploy
Configure ngix and setup a service (systemd)
```sh
sudo make deploy
```

## 🤖 Setting Up a Custom GPT with OpenAI

1. Visit [platform.openai.com/gpts](https://platform.openai.com/gpts)
2. Click "Create a GPT"
3. In the "Configure" tab, scroll to the **API** section
4. Enter your OpenAPI schema URL:
   ```
   https://${YOUR_DOMAIN}/openapi.yaml
   ```
5. OpenAI will introspect and expose available endpoints for you to wire into your GPT
