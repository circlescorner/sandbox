"""
DigitalOcean Droplet Manager
Handles sandbox droplet lifecycle: spawn, kill, status, snapshot
"""

import asyncio
import json
from typing import Optional
import httpx

SANDBOX_TAG = "sandbox-instance"
SANDBOX_NAME = "sandbox"


class DropletManager:
    """Manages sandbox droplets via DigitalOcean API"""
    
    def __init__(self, api_token: str, vpc_uuid: str, region: str = "nyc1"):
        self.api_token = api_token
        self.vpc_uuid = vpc_uuid
        self.region = region
        self.base_url = "https://api.digitalocean.com/v2"
        
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make an API request"""
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=self._headers(),
                **kwargs
            )
            response.raise_for_status()
            if response.content:
                return response.json()
            return {}
    
    async def _find_sandbox_droplet(self) -> Optional[dict]:
        """Find the sandbox droplet by tag"""
        try:
            data = await self._request("GET", f"/droplets?tag_name={SANDBOX_TAG}")
            droplets = data.get("droplets", [])
            if droplets:
                return droplets[0]
        except Exception:
            pass
        return None
    
    async def get_sandbox_status(self) -> dict:
        """Get current sandbox status"""
        droplet = await self._find_sandbox_droplet()
        
        if not droplet:
            return {
                "running": False,
                "status": "not_created",
                "droplet_id": None,
                "ip_address": None,
                "private_ip": None,
            }
        
        # Extract IPs
        public_ip = None
        private_ip = None
        for network in droplet.get("networks", {}).get("v4", []):
            if network.get("type") == "public":
                public_ip = network.get("ip_address")
            elif network.get("type") == "private":
                private_ip = network.get("ip_address")
        
        return {
            "running": droplet.get("status") == "active",
            "status": droplet.get("status"),
            "droplet_id": droplet.get("id"),
            "ip_address": public_ip,
            "private_ip": private_ip,
            "created_at": droplet.get("created_at"),
            "size": droplet.get("size", {}).get("slug"),
            "region": droplet.get("region", {}).get("slug"),
        }
    
    async def spawn_sandbox(self, snapshot_id: str, size: str = "s-2vcpu-2gb") -> dict:
        """Spawn a new sandbox droplet from snapshot"""
        # Check if already running
        existing = await self._find_sandbox_droplet()
        if existing:
            return {
                "status": "error",
                "message": "Sandbox already running",
                "droplet_id": existing.get("id"),
            }
        
        # Create droplet from snapshot
        payload = {
            "name": SANDBOX_NAME,
            "region": self.region,
            "size": size,
            "image": snapshot_id,  # Snapshot ID
            "vpc_uuid": self.vpc_uuid,
            "tags": [SANDBOX_TAG],
            "monitoring": True,
            # No SSH keys - access via VPC only
        }
        
        try:
            data = await self._request("POST", "/droplets", json=payload)
            droplet = data.get("droplet", {})
            
            return {
                "status": "creating",
                "message": "Sandbox droplet is being created",
                "droplet_id": droplet.get("id"),
            }
        except httpx.HTTPStatusError as e:
            return {
                "status": "error",
                "message": f"Failed to create droplet: {e.response.text}",
            }
    
    async def kill_sandbox(self) -> dict:
        """Destroy the sandbox droplet"""
        droplet = await self._find_sandbox_droplet()
        
        if not droplet:
            return {
                "status": "ok",
                "message": "No sandbox running",
            }
        
        droplet_id = droplet.get("id")
        
        try:
            await self._request("DELETE", f"/droplets/{droplet_id}")
            return {
                "status": "ok",
                "message": "Sandbox droplet destroyed",
                "droplet_id": droplet_id,
            }
        except httpx.HTTPStatusError as e:
            return {
                "status": "error",
                "message": f"Failed to destroy droplet: {e.response.text}",
            }
    
    async def apply_network_config(self, config: dict) -> dict:
        """Apply network configuration to running sandbox
        
        This sends the config to the sandbox droplet to update iptables rules.
        The sandbox runs a small agent that receives these updates via VPC.
        """
        status = await self.get_sandbox_status()
        if not status.get("running"):
            return {"status": "error", "message": "Sandbox not running"}
        
        private_ip = status.get("private_ip")
        if not private_ip:
            return {"status": "error", "message": "No private IP for sandbox"}
        
        # Send config to sandbox agent (runs on port 9999)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"http://{private_ip}:9999/network/apply",
                    json=config,
                    timeout=30,
                )
                return response.json()
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    async def build_sandbox_snapshot(self) -> dict:
        """Build a new sandbox snapshot
        
        1. Create a temporary droplet with sandbox config
        2. Wait for it to be ready
        3. Power off and snapshot
        4. Return snapshot ID
        """
        # This is a simplified version - in practice you'd want more control
        
        # Create droplet from base image
        payload = {
            "name": "sandbox-builder",
            "region": self.region,
            "size": "s-2vcpu-2gb",
            "image": "ubuntu-24-04-x64",
            "vpc_uuid": self.vpc_uuid,
            "tags": ["sandbox-builder"],
            "user_data": self._get_sandbox_user_data(),
        }
        
        try:
            # Create the builder droplet
            data = await self._request("POST", "/droplets", json=payload)
            droplet_id = data.get("droplet", {}).get("id")
            
            if not droplet_id:
                return {"status": "error", "message": "Failed to create builder droplet"}
            
            # Wait for droplet to be active (simplified - should poll)
            await asyncio.sleep(60)
            
            # Power off the droplet
            await self._request(
                "POST",
                f"/droplets/{droplet_id}/actions",
                json={"type": "shutdown"}
            )
            await asyncio.sleep(30)
            
            # Create snapshot
            snapshot_data = await self._request(
                "POST",
                f"/droplets/{droplet_id}/actions",
                json={"type": "snapshot", "name": f"sandbox-{int(asyncio.get_event_loop().time())}"}
            )
            
            # Wait for snapshot (simplified)
            await asyncio.sleep(120)
            
            # Get snapshot ID
            snapshots = await self._request("GET", f"/droplets/{droplet_id}/snapshots")
            snapshot_list = snapshots.get("snapshots", [])
            
            # Destroy builder droplet
            await self._request("DELETE", f"/droplets/{droplet_id}")
            
            if snapshot_list:
                return {
                    "status": "ok",
                    "snapshot_id": snapshot_list[-1].get("id"),
                    "message": "Snapshot created successfully"
                }
            
            return {"status": "error", "message": "Snapshot not found"}
            
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def _get_sandbox_user_data(self) -> str:
        """Get cloud-init user data for sandbox droplet"""
        # This would be loaded from sandbox/cloud-init.yaml
        return """#cloud-config
package_update: true
packages:
  - docker.io
  - docker-compose
  - iptables-persistent

runcmd:
  - systemctl enable docker
  - systemctl start docker
  - mkdir -p /opt/sandbox
  - echo "Sandbox ready" > /opt/sandbox/ready
"""
