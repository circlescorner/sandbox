"""
Authentication module
TOTP verification and session management
"""

import secrets
import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pyotp

SESSION_COOKIE_NAME = "sandbox_session"
SESSION_DURATION_HOURS = 24
SESSIONS_FILE = "sessions.json"


def verify_totp(secret: str, code: str) -> bool:
    """Verify a TOTP code against the secret"""
    totp = pyotp.TOTP(secret)
    # Allow 1 window tolerance for clock skew
    return totp.verify(code, valid_window=1)


def _get_sessions_file(data_dir: Path) -> Path:
    return data_dir / SESSIONS_FILE


def _load_sessions(data_dir: Path) -> dict:
    """Load sessions from file"""
    sessions_file = _get_sessions_file(data_dir)
    if sessions_file.exists():
        try:
            return json.loads(sessions_file.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_sessions(data_dir: Path, sessions: dict):
    """Save sessions to file"""
    sessions_file = _get_sessions_file(data_dir)
    sessions_file.write_text(json.dumps(sessions, indent=2))
    sessions_file.chmod(0o600)


def _clean_expired_sessions(sessions: dict) -> dict:
    """Remove expired sessions"""
    now = datetime.utcnow().isoformat()
    return {
        sid: data for sid, data in sessions.items()
        if data.get("expires_at", "") > now
    }


def create_session(user: str, data_dir: Path) -> str:
    """Create a new session and return the session ID"""
    session_id = secrets.token_urlsafe(32)
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()
    
    sessions = _load_sessions(data_dir)
    sessions = _clean_expired_sessions(sessions)
    
    expires_at = datetime.utcnow() + timedelta(hours=SESSION_DURATION_HOURS)
    
    sessions[session_hash] = {
        "user": user,
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    
    _save_sessions(data_dir, sessions)
    
    return session_id


def verify_session(session_id: str, data_dir: Path) -> bool:
    """Verify a session is valid and not expired"""
    if not session_id:
        return False
    
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()
    sessions = _load_sessions(data_dir)
    
    if session_hash not in sessions:
        return False
    
    session = sessions[session_hash]
    expires_at = session.get("expires_at", "")
    
    if expires_at < datetime.utcnow().isoformat():
        # Session expired, clean it up
        del sessions[session_hash]
        _save_sessions(data_dir, sessions)
        return False
    
    return True


def get_session_user(session_id: str, data_dir: Path) -> Optional[str]:
    """Get the user associated with a session"""
    if not session_id:
        return None
    
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()
    sessions = _load_sessions(data_dir)
    
    session = sessions.get(session_hash)
    if session:
        return session.get("user")
    
    return None


def revoke_session(session_id: str, data_dir: Path) -> bool:
    """Revoke a session"""
    if not session_id:
        return False
    
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()
    sessions = _load_sessions(data_dir)
    
    if session_hash in sessions:
        del sessions[session_hash]
        _save_sessions(data_dir, sessions)
        return True
    
    return False


def revoke_all_sessions(data_dir: Path):
    """Revoke all sessions"""
    sessions_file = _get_sessions_file(data_dir)
    if sessions_file.exists():
        sessions_file.write_text("{}")
