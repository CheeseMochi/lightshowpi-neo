"""
Config editor router: view and edit the show's config files (admin only).

Changes apply from the next song (each song is a new show process). Settings
the API itself reads ([api], [buttons], [devices]) need an API restart.
"""

import configparser
import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.core.auth import require_admin
from api.services import config_files as cf

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["Config"])

# Set by main.py at startup; tests override get_config_dir instead
_config_dir: Optional[str] = None


def set_config_dir(path: Optional[str]) -> None:
    """Point the editor at a config directory (main.py: <home>/config)."""
    global _config_dir
    _config_dir = path


def get_config_dir() -> str:
    """Dependency: the config directory (defaults to $SYNCHRONIZED_LIGHTS_HOME/config)."""
    if _config_dir:
        return _config_dir
    home = os.getenv("SYNCHRONIZED_LIGHTS_HOME")
    if not home:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="SYNCHRONIZED_LIGHTS_HOME is not set")
    return os.path.join(home, "config")


class ConfigFileInfo(BaseModel):
    name: str
    exists: bool
    read_only: bool
    kind: str
    size: int
    modified: Optional[str] = None


class ConfigFileContent(BaseModel):
    name: str
    content: str
    exists: bool
    sha256: str
    read_only: bool


class ConfigFileWrite(BaseModel):
    content: str = Field(..., max_length=1_000_000)
    # sha256 of the text the client loaded (GET returns it); 409 if the file changed
    base_sha256: str = Field(..., min_length=64, max_length=64)


class ConfigWriteResult(BaseModel):
    name: str
    sha256: str
    backup: Optional[str] = None
    warnings: List[str] = []
    note: str = ("Saved. Show settings apply from the next song; "
                 "API settings ([api], [buttons], [devices]) need an API restart.")


class ConfigBackup(BaseModel):
    id: str
    created: str
    size: int


class ValidateResult(BaseModel):
    ok: bool
    error: Optional[str] = None
    warnings: List[str] = []


def _http(fn):
    """Run a service call, mapping its errors to HTTP errors."""
    try:
        return fn()
    except cf.InvalidNameError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except cf.ReadOnlyError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except cf.ParseError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Config does not parse: {e}")
    except cf.StaleFileError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except configparser.Error as e:
        # e.g. the effective view of a hand-edited overrides.cfg that doesn't parse
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"A config file does not parse: {str(e).strip()}")


@router.get("/files", response_model=List[ConfigFileInfo])
def list_config_files(config_dir: str = Depends(get_config_dir),
                      current_user: dict = Depends(require_admin)):
    """List the config files the editor can open (defaults are read-only)."""
    return cf.list_files(config_dir)


@router.get("/effective")
def get_effective_config(config_dir: str = Depends(get_config_dir),
                         current_user: dict = Depends(require_admin)):
    """
    The merged config (defaults.cfg + overrides.cfg) the next song will use.

    Values of keys containing token, key, password or secret are masked.
    """
    return _http(lambda: cf.effective_config(config_dir))


@router.get("/files/{name}", response_model=ConfigFileContent)
def get_config_file(name: str, config_dir: str = Depends(get_config_dir),
                    current_user: dict = Depends(require_admin)):
    """Raw text of one config file plus the sha256 to send back when saving."""
    return _http(lambda: cf.read_file(config_dir, name))


@router.post("/files/{name}/validate", response_model=ValidateResult)
def validate_config_file(name: str, body: ConfigFileWrite,
                         config_dir: str = Depends(get_config_dir),
                         current_user: dict = Depends(require_admin)):
    """Check text without saving: parse errors and unknown sections/keys."""
    _http(lambda: cf.check_name(config_dir, name))
    try:
        return {"ok": True, "warnings": cf.validate(config_dir, name, body.content)}
    except cf.ParseError as e:
        return {"ok": False, "error": str(e), "warnings": []}


@router.put("/files/{name}", response_model=ConfigWriteResult)
def put_config_file(name: str, body: ConfigFileWrite,
                    config_dir: str = Depends(get_config_dir),
                    current_user: dict = Depends(require_admin)):
    """
    Save a config file.

    The text must parse; unknown sections/keys come back as warnings. The old
    file is backed up to config/backups/ and the write is atomic. Returns 409
    if the file changed since `base_sha256` was read.
    """
    result = _http(lambda: cf.write_file(config_dir, name, body.content, body.base_sha256,
                                         user=current_user.get("username")))
    log.info("Config file %s saved by %s", name, current_user.get("username"))
    return result


@router.get("/files/{name}/backups", response_model=List[ConfigBackup])
def list_config_backups(name: str, config_dir: str = Depends(get_config_dir),
                        current_user: dict = Depends(require_admin)):
    """Backups of one file, newest first (the last 20 are kept)."""
    return _http(lambda: cf.list_backups(config_dir, name))


@router.get("/files/{name}/backups/{backup_id}", response_model=ConfigFileContent)
def get_config_backup(name: str, backup_id: str, config_dir: str = Depends(get_config_dir),
                      current_user: dict = Depends(require_admin)):
    """Text of one backup (for a diff before restoring)."""
    text = _http(lambda: cf.read_backup(config_dir, name, backup_id))
    return {"name": backup_id, "content": text, "exists": True,
            "sha256": cf.sha256_text(text), "read_only": True}


@router.post("/files/{name}/backups/{backup_id}/restore", response_model=ConfigWriteResult)
def restore_config_backup(name: str, backup_id: str, config_dir: str = Depends(get_config_dir),
                          current_user: dict = Depends(require_admin)):
    """Put a backup back. The current file is backed up first, so this can be undone."""
    result = _http(lambda: cf.restore_backup(config_dir, name, backup_id,
                                             user=current_user.get("username")))
    log.info("Config file %s restored from %s by %s", name, backup_id,
             current_user.get("username"))
    return result
