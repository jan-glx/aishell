
# aishell: a shell for your AI   $~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~$   ![Aishell Logo](https://aishell.gleixner.xyz/static/favicon.svg) 
_**aishell** does not add AI to your shell. **aishell** provides your AI root shell access to a dedicated server._

**Run a persistent Linux server under AI’s control, with full root access, programmable APIs, and a transparent, inspectable environment. Perfect for developers, tinkerers, and AI agents that need more than a browser and a sandbox.**



This project provides a simple FastAPI server that exposes a few endpoints to be called from OpenAI's ChatGPT, essentially turning the chatbot into a powerfull agent. 
In particular there are endpoints to execute commands within a temporary shell or arbitrary keystrokes within a persistent tmux session and to up- and download files.
The user can follow the agent's actions by joing the tmux session over ssh or by observing a log of api calls at /static/api_log.html .
This enables endless use cases and should be the only plugin/MPC you ever need.
## Examples

## Installation

### Prerequisite:
A root-access Linux server and a domain pointing to it.

You have two main setup options:

-  [jan-glx/aishell-setup](https://github.com/jan-glx/aishell-setup):  
  A Terraform module to deploy **aishell** on Amazon EC2 — includes support for **hibernation** to reduce idle-time costs.

-  Alternatively, a simple shared VM (e.g. Hetzner CX or CPX) often provides better price/performance and sufficient persistence for most use cases.

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

###  Setting Up a Custom GPT with OpenAI

1. Visit [platform.openai.com/gpts](https://platform.openai.com/gpts)
2. Click "Create a GPT"
3. In the "Configure" tab, scroll to the **API** section
4. Enter your OpenAPI schema URL:
   ```
   https://${YOUR_DOMAIN}/openapi.yaml
   ```
5. OpenAI will introspect and expose available endpoints for you to wire into your GPT

