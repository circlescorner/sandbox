"""
Orchestrator Control Panel
FastAPI application for managing sandbox droplets with TOTP authentication
"""

import os
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pydantic_settings import BaseSettings
import pyotp
import qrcode
import qrcode.image.svg
from io import BytesIO
import base64

from app.auth import (
    verify_totp,
    create_session,
    verify_session,
    get_session_user,
    SESSION_COOKIE_NAME,
)
from app.droplet import DropletManager

# Configuration
class Settings(BaseSettings):
    domain: str = "circlescorner.xyz"
    do_api_token: str = ""
    vpc_uuid: str = ""
    sandbox_snapshot_id: str = ""
    sandbox_region: str = "nyc1"
    sandbox_size: str = "s-2vcpu-2gb"
    data_dir: Path = Path("/app/data")
    
    class Config:
        env_file = ".env"

settings = Settings()

# Ensure data directory exists
settings.data_dir.mkdir(parents=True, exist_ok=True)

# App initialization
app = FastAPI(title="Sandbox Orchestrator")
templates = Jinja2Templates(directory="templates")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Droplet manager
droplet_manager = DropletManager(
    api_token=settings.do_api_token,
    vpc_uuid=settings.vpc_uuid,
    region=settings.sandbox_region,
)

# =====================
# TOTP Setup & Storage
# =====================

TOTP_SECRET_FILE = settings.data_dir / "totp_secret.json"

def get_totp_secret() -> Optional[str]:
    """Load TOTP secret from file"""
    if TOTP_SECRET_FILE.exists():
        data = json.loads(TOTP_SECRET_FILE.read_text())
        return data.get("secret")
    return None

def save_totp_secret(secret: str):
    """Save TOTP secret to file"""
    TOTP_SECRET_FILE.write_text(json.dumps({"secret": secret}))
    TOTP_SECRET_FILE.chmod(0o600)

def is_setup_complete() -> bool:
    """Check if initial setup is complete"""
    return get_totp_secret() is not None

# =====================
# Network Config Storage
# =====================

NETWORK_CONFIG_FILE = settings.data_dir / "network_config.json"

def get_network_config() -> dict:
    """Load network configuration"""
    if NETWORK_CONFIG_FILE.exists():
        return json.loads(NETWORK_CONFIG_FILE.read_text())
    # Default config
    return {
        "containers": {
            "container-1": {"egress": "none", "allowed_domains": []},
            "container-2": {"egress": "none", "allowed_domains": []},
            "container-3": {"egress": "none", "allowed_domains": []},
            "container-4": {"egress": "allowlist", "allowed_domains": ["api.runpod.ai", "*.runpod.net"]},
        },
        "inter_container": {
            "enabled": False,
            "rules": []  # e.g., [{"from": "container-1", "to": "container-2", "ports": [8080]}]
        }
    }

def save_network_config(config: dict):
    """Save network configuration"""
    NETWORK_CONFIG_FILE.write_text(json.dumps(config, indent=2))

# =====================
# Health & Auth Endpoints
# =====================

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/auth/verify")
async def verify_auth(request: Request):
    """Verify session for forward auth"""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id or not verify_session(session_id, settings.data_dir):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    user = get_session_user(session_id, settings.data_dir)
    return Response(
        status_code=200,
        headers={"X-User": user or "admin"}
    )

# =====================
# Setup Flow
# =====================

@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    """Initial TOTP setup page"""
    if is_setup_complete():
        return RedirectResponse(url="/login", status_code=303)
    
    # Generate new TOTP secret
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(
        name="admin",
        issuer_name=f"Sandbox@{settings.domain}"
    )
    
    # Generate QR code as base64 SVG
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(provisioning_uri)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    qr_base64 = base64.b64encode(buffer.getvalue()).decode()
    
    return templates.TemplateResponse("setup.html", {
        "request": request,
        "qr_code": qr_base64,
        "secret": secret,
        "domain": settings.domain,
    })

@app.post("/setup")
async def complete_setup(
    request: Request,
    secret: str = Form(...),
    totp_code: str = Form(...),
):
    """Complete TOTP setup"""
    if is_setup_complete():
        return RedirectResponse(url="/login", status_code=303)
    
    # Verify the TOTP code
    totp = pyotp.TOTP(secret)
    if not totp.verify(totp_code, valid_window=1):
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "error": "Invalid TOTP code. Please try again.",
            "secret": secret,
            "qr_code": "",  # Would need to regenerate
        })
    
    # Save the secret
    save_totp_secret(secret)
    
    # Create session and redirect
    session_id = create_session("admin", settings.data_dir)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=86400,  # 24 hours
    )
    return response

# =====================
# Login Flow
# =====================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    if not is_setup_complete():
        return RedirectResponse(url="/setup", status_code=303)
    
    # Check if already logged in
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id and verify_session(session_id, settings.data_dir):
        return RedirectResponse(url="/", status_code=303)
    
    return templates.TemplateResponse("login.html", {
        "request": request,
        "domain": settings.domain,
    })

@app.post("/login")
async def login(
    request: Request,
    totp_code: str = Form(...),
):
    """Process login"""
    secret = get_totp_secret()
    if not secret:
        return RedirectResponse(url="/setup", status_code=303)
    
    if not verify_totp(secret, totp_code):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid TOTP code",
            "domain": settings.domain,
        })
    
    session_id = create_session("admin", settings.data_dir)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=86400,
    )
    return response

@app.get("/logout")
async def logout(request: Request):
    """Logout and clear session"""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response

# =====================
# Dashboard
# =====================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard"""
    # Auth is handled by Caddy forward_auth
    
    sandbox_status = await droplet_manager.get_sandbox_status()
    network_config = get_network_config()
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "domain": settings.domain,
        "sandbox_status": sandbox_status,
        "network_config": network_config,
        "snapshot_id": settings.sandbox_snapshot_id,
    })

# =====================
# API Endpoints
# =====================

class NetworkConfigUpdate(BaseModel):
    containers: dict
    inter_container: dict

@app.post("/api/sandbox/spawn")
async def spawn_sandbox():
    """Spawn a new sandbox droplet"""
    if not settings.sandbox_snapshot_id:
        raise HTTPException(status_code=400, detail="No sandbox snapshot configured")
    
    result = await droplet_manager.spawn_sandbox(
        snapshot_id=settings.sandbox_snapshot_id,
        size=settings.sandbox_size,
    )
    return result

@app.post("/api/sandbox/kill")
async def kill_sandbox():
    """Destroy the sandbox droplet"""
    result = await droplet_manager.kill_sandbox()
    return result

@app.get("/api/sandbox/status")
async def sandbox_status():
    """Get sandbox status"""
    return await droplet_manager.get_sandbox_status()

@app.post("/api/network/config")
async def update_network_config(config: NetworkConfigUpdate):
    """Update network configuration"""
    save_network_config(config.model_dump())
    
    # If sandbox is running, apply changes
    status = await droplet_manager.get_sandbox_status()
    if status.get("running"):
        await droplet_manager.apply_network_config(config.model_dump())
    
    return {"status": "ok", "config": config.model_dump()}

@app.get("/api/network/config")
async def get_current_network_config():
    """Get current network configuration"""
    return get_network_config()

@app.post("/api/snapshot/build")
async def build_snapshot():
    """Build a new sandbox snapshot"""
    result = await droplet_manager.build_sandbox_snapshot()
    if result.get("snapshot_id"):
        # Save the new snapshot ID
        settings.sandbox_snapshot_id = result["snapshot_id"]
        # Would need to persist this - for now just return it
    return result

@app.get("/api/terminal/sandbox")
async def get_sandbox_terminal_url():
    """Get URL for sandbox terminal access"""
    status = await droplet_manager.get_sandbox_status()
    if not status.get("running"):
        raise HTTPException(status_code=400, detail="Sandbox not running")
    
    # Return info for connecting to sandbox via orchestrator
    return {
        "type": "sandbox",
        "containers": ["container-1", "container-2", "container-3", "container-4"],
        "connect_via": "/terminal"  # Uses ttyd on orchestrator to exec into sandbox
    }

# =====================
# Error Handlers
# =====================

@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc: HTTPException):
    return RedirectResponse(url="/login", status_code=303)

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse("error.html", {
        "request": request,
        "error": "Page not found",
        "code": 404,
    }, status_code=404)
