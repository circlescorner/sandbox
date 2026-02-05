# Secure Sandbox Network on DigitalOcean

A hardened, web-accessible sandbox environment with isolated Docker containers for running untrusted code and LLM systems.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│  YOUR BROWSER @ https://circlescorner.xyz                               │
│    → TOTP 2FA login                                                     │
│    → Control Panel (spawn/kill sandbox, configure network)              │
│    → Web Terminal (to orchestrator or sandbox containers)               │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │ HTTPS (port 443 only)
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR DROPLET ($6/mo, always-on)                                │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ • Caddy (reverse proxy, auto-HTTPS via Let's Encrypt)            │  │
│  │ • Control Panel (FastAPI + TOTP 2FA)                             │  │
│  │ • ttyd (web terminal, proxied through Caddy)                     │  │
│  │ • DO API client (manages sandbox lifecycle)                      │  │
│  │ • Cloud Firewall: ingress 443 only                               │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │ VPC Private Network (10.116.0.0/20)
                              │ No public IP on sandbox
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  SANDBOX DROPLET (on-demand, spawned from snapshot)                     │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ isolated_net (bridge, no external access)                        │  │
│  │   ├── container-1: Empty Alpine                                  │  │
│  │   ├── container-2: Empty Alpine                                  │  │
│  │   └── container-3: Empty Alpine                                  │  │
│  │                                                                   │  │
│  │ llm_net (bridge, RunPod egress only via iptables)                │  │
│  │   └── container-4: Docker Model Runner                           │  │
│  │                                                                   │  │
│  │ Network Policies: Configurable per-container via control panel   │  │
│  │ Host Firewall: No public ingress, VPC only                       │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

1. DigitalOcean account
2. Domain: `circlescorner.xyz` (you'll point DNS here)
3. A TOTP authenticator app (Google Authenticator, Authy, etc.)

## Setup Steps

### Step 1: Create DigitalOcean API Token

1. Go to https://cloud.digitalocean.com/account/api/tokens
2. Click "Generate New Token"
3. Name: `sandbox-orchestrator`
4. Select: Read + Write
5. **Save the token securely** - you'll only see it once

### Step 2: Configure DNS

Point your domain to DigitalOcean's nameservers:

1. In your domain registrar, set nameservers to:
   - ns1.digitalocean.com
   - ns2.digitalocean.com
   - ns3.digitalocean.com

2. In DigitalOcean (Networking → Domains):
   - Add domain: `circlescorner.xyz`
   - You'll add an A record after creating the orchestrator

### Step 3: Create VPC Network

```bash
# Using doctl (DigitalOcean CLI) or via web console
doctl vpcs create \
  --name sandbox-vpc \
  --region nyc1 \
  --ip-range 10.116.0.0/20
```

Or via web console: Networking → VPC → Create VPC

### Step 4: Create Cloud Firewall

Create two firewalls:

**orchestrator-firewall:**
- Inbound: TCP 443 (HTTPS) from anywhere
- Outbound: All (needs to talk to DO API, GitHub, etc.)

**sandbox-firewall:**
- Inbound: All TCP/UDP from VPC only (10.116.0.0/20)
- Outbound: None (controlled at container level)

### Step 5: Deploy Orchestrator

```bash
# Clone this repo on your local machine
git clone https://github.com/YOUR_USERNAME/sandbox-network.git
cd sandbox-network

# Create the orchestrator droplet
doctl compute droplet create orchestrator \
  --image ubuntu-24-04-x64 \
  --size s-1vcpu-1gb \
  --region nyc1 \
  --vpc-uuid YOUR_VPC_UUID \
  --user-data-file orchestrator/cloud-init.yaml \
  --tag-names orchestrator \
  --wait

# Get the public IP
doctl compute droplet list --format ID,Name,PublicIPv4
```

### Step 6: Configure DNS A Record

Add an A record pointing `circlescorner.xyz` to the orchestrator's public IP.

### Step 7: Initial Setup

1. Wait 3-5 minutes for cloud-init to complete
2. Access: `https://circlescorner.xyz/setup`
3. Scan the QR code with your TOTP app
4. Complete setup with your TOTP code

### Step 8: Build Sandbox Snapshot

From the control panel:
1. Click "Build Sandbox Image"
2. Wait for snapshot creation (~5 min)
3. Snapshot ID will be saved automatically

## Usage

### Control Panel

- **Spawn Sandbox**: Creates new sandbox droplet from snapshot
- **Kill Sandbox**: Destroys the sandbox droplet
- **Network Config**: Modify container egress rules
- **Terminal**: Access orchestrator or sandbox shell

### Network Configuration

Default policies:
- Containers 1-3: No external network access
- Container 4 (LLM): RunPod API access only

Configurable options:
- Allow specific domains/IPs per container
- Enable inter-container communication
- Open specific ports between containers

## Security Model

### Threat Model: Bad Actors Inside Containers

This system assumes containers may be compromised. Defense layers:

1. **Network Isolation**: Containers have no internet by default
2. **No Public IP on Sandbox**: Only reachable via VPC
3. **Orchestrator Separation**: Control plane on different droplet
4. **Ephemeral Sandboxes**: Killed after use, fresh on each spawn
5. **Container Hardening**: No privileged mode, dropped capabilities
6. **Egress Control**: iptables rules block unauthorized outbound

### What's Protected

- Orchestrator credentials and API tokens
- Other users' data (if multi-tenant in future)
- Your DigitalOcean account
- Network infrastructure

### What's NOT Protected (by design)

- Data inside the sandbox containers (ephemeral)
- Container-to-container attacks within same network
- DoS within the sandbox itself

## File Structure

```
sandbox-network/
├── README.md
├── orchestrator/
│   ├── cloud-init.yaml       # Initial droplet setup
│   ├── Dockerfile            # Control panel container
│   ├── docker-compose.yml    # Orchestrator services
│   ├── app/
│   │   ├── main.py          # FastAPI control panel
│   │   ├── auth.py          # TOTP authentication
│   │   ├── droplet.py       # DO API wrapper
│   │   └── templates/       # Web UI
│   └── caddy/
│       └── Caddyfile        # Reverse proxy config
├── sandbox/
│   ├── cloud-init.yaml      # Sandbox droplet setup
│   ├── docker-compose.yml   # 4 containers
│   ├── network-policy.sh    # iptables rules
│   └── Dockerfiles/
│       ├── empty.Dockerfile
│       └── llm.Dockerfile
└── scripts/
    └── build-snapshot.sh    # Creates sandbox snapshot
```

## Maintenance

### Updating Orchestrator

```bash
# SSH via web terminal, then:
cd /opt/orchestrator
git pull
docker-compose up -d --build
```

### Updating Sandbox Image

1. Spawn a sandbox
2. Make changes via terminal
3. Kill all containers
4. Create new snapshot from control panel
5. Delete old snapshot

## Troubleshooting

### Can't access https://circlescorner.xyz

1. Check DNS propagation: `dig circlescorner.xyz`
2. Verify firewall allows 443
3. Check Caddy logs: `docker logs caddy`

### TOTP not working

1. Ensure device time is synced
2. Check `/opt/orchestrator/.totp-secret` exists
3. Re-run setup if needed

### Sandbox won't spawn

1. Check DO API token validity
2. Verify snapshot exists
3. Check orchestrator logs: `docker logs control-panel`
