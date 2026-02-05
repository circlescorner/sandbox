#!/bin/bash
# Quick Start Script
# This helps you get everything set up step by step

set -e

echo "🚀 Sandbox Network Quick Start"
echo "=============================="
echo ""

# Check for required tools
check_tool() {
    if ! command -v "$1" &> /dev/null; then
        echo "❌ $1 is required but not installed."
        return 1
    fi
    echo "✅ $1 found"
}

echo "Checking required tools..."
check_tool doctl || { echo "Install doctl: https://docs.digitalocean.com/reference/doctl/how-to/install/"; exit 1; }
check_tool git || { echo "Install git"; exit 1; }

echo ""
echo "Step 1: DigitalOcean Authentication"
echo "------------------------------------"

if ! doctl account get &> /dev/null; then
    echo "Please authenticate with DigitalOcean:"
    echo "  doctl auth init"
    echo ""
    echo "You'll need your API token from:"
    echo "  https://cloud.digitalocean.com/account/api/tokens"
    exit 1
fi

ACCOUNT=$(doctl account get --format Email --no-header)
echo "✅ Authenticated as: $ACCOUNT"

echo ""
echo "Step 2: Create VPC"
echo "------------------"

EXISTING_VPC=$(doctl vpcs list --format Name --no-header | grep -w "sandbox-vpc" || true)

if [ -z "$EXISTING_VPC" ]; then
    echo "Creating VPC 'sandbox-vpc' in nyc1..."
    doctl vpcs create --name sandbox-vpc --region nyc1 --ip-range 10.116.0.0/20
    echo "✅ VPC created"
else
    echo "✅ VPC 'sandbox-vpc' already exists"
fi

VPC_UUID=$(doctl vpcs list --format ID,Name --no-header | grep "sandbox-vpc" | awk '{print $1}')
echo "   VPC UUID: $VPC_UUID"

echo ""
echo "Step 3: Create Cloud Firewalls"
echo "------------------------------"

# Check if firewalls exist
ORCH_FW=$(doctl compute firewall list --format Name --no-header | grep -w "orchestrator-firewall" || true)
SAND_FW=$(doctl compute firewall list --format Name --no-header | grep -w "sandbox-firewall" || true)

if [ -z "$ORCH_FW" ]; then
    echo "Creating orchestrator firewall..."
    doctl compute firewall create \
        --name orchestrator-firewall \
        --inbound-rules "protocol:tcp,ports:443,address:0.0.0.0/0,address:::/0" \
        --outbound-rules "protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 protocol:udp,ports:all,address:0.0.0.0/0,address:::/0" \
        --tag-names orchestrator
    echo "✅ Orchestrator firewall created"
else
    echo "✅ Orchestrator firewall exists"
fi

if [ -z "$SAND_FW" ]; then
    echo "Creating sandbox firewall..."
    doctl compute firewall create \
        --name sandbox-firewall \
        --inbound-rules "protocol:tcp,ports:all,address:10.116.0.0/20 protocol:udp,ports:all,address:10.116.0.0/20" \
        --outbound-rules "protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 protocol:udp,ports:all,address:0.0.0.0/0,address:::/0" \
        --tag-names sandbox-instance
    echo "✅ Sandbox firewall created"
else
    echo "✅ Sandbox firewall exists"
fi

echo ""
echo "Step 4: Configuration"
echo "---------------------"

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env file from template"
fi

# Get API token
echo ""
echo "Enter your DigitalOcean API token (or press Enter to skip if already set):"
read -s DO_TOKEN
if [ -n "$DO_TOKEN" ]; then
    sed -i "s/DO_API_TOKEN=.*/DO_API_TOKEN=$DO_TOKEN/" .env
fi

# Set VPC UUID
sed -i "s/VPC_UUID=.*/VPC_UUID=$VPC_UUID/" .env

echo "✅ Configuration updated"

echo ""
echo "Step 5: Deploy Orchestrator"
echo "---------------------------"
echo ""
echo "To deploy the orchestrator droplet, run:"
echo ""
echo "  doctl compute droplet create orchestrator \\"
echo "    --image ubuntu-24-04-x64 \\"
echo "    --size s-1vcpu-1gb \\"
echo "    --region nyc1 \\"
echo "    --vpc-uuid $VPC_UUID \\"
echo "    --user-data-file orchestrator/cloud-init.yaml \\"
echo "    --tag-names orchestrator \\"
echo "    --wait"
echo ""
echo "Then:"
echo "1. Get the public IP: doctl compute droplet list"
echo "2. Point your domain to that IP"
echo "3. Wait 5 minutes for setup"
echo "4. Access https://your-domain/setup"
echo ""
echo "=============================="
echo "Quick start complete!"
