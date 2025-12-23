
# aishell: a shell for your AI   $~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~$   ![Aishell Logo](https://aishell.gleixner.xyz/static/favicon.svg) 
Aishell does not add AI to your shell. Aishell provides your AI root shell access to a server you control.

Run a persistent Linux server under AI’s control, with full root access, programmable APIs, and a transparent, inspectable environment. Perfect for developers, tinkerers, and AI agents that need more than a browser and a sandbox.

## Prerequisite:
A root-access Linux server and a domain pointing to it.

You have two main setup options:

-  [jan-glx/aishell-setup](https://github.com/jan-glx/aishell-setup):  
  A Terraform module to deploy **aishell** on Amazon EC2 — includes support for **hibernation** to reduce idle-time costs.

-  Alternatively, a simple shared VM (e.g. Hetzner CX or CPX) often provides better price/performance and sufficient persistence for most use cases.

## Installation

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


## Features
- [x] **Persistent working directory and environment**  
- [x] **GPT-native API interface**: Exposes your shell as tools inside custom GPTs  
- [x] **Shell history & command logs**: Everything is logged (`~/output/api_log.htm`)  
- [x] **File uploads & downloads**  
- [x] **Shared `tmux` session over SSH**: See and edit what your AI agent is doing in real time  
- [x] **Fully programmable**: Root access, unrestricted Internet access, virtualenvs, systemd services, etc.


### Planned / Missing

- [ ] **Unprivileged shell execution**: Currently runs as root — sandboxing not yet implemented  
- [ ] **Web-based sudo command authorization**: No prompt/approval interface yet  
- [ ] **Multi-user support**: Single user/session model for now  
