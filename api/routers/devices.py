"""
Devices router: Pis, outputs and relay channels, with reachability status.

Admin only. Relay test buttons in the web UI use the existing /api/test/*
endpoints with each relay's ``channel``.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.core.auth import require_admin
from api.routers.config_files import get_config_dir
from api.services import config_files as cf
from api.services.device_monitor import DeviceMonitor
from api.services.devices import (
    DeviceConflict, DeviceError, DeviceNotFound, DeviceStore,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["Devices"])

_store: Optional[DeviceStore] = None
_monitor: Optional[DeviceMonitor] = None


def set_store(store: Optional[DeviceStore]) -> None:
    global _store
    _store = store


def set_monitor(monitor: Optional[DeviceMonitor]) -> None:
    global _monitor
    _monitor = monitor


def get_store() -> DeviceStore:
    """Dependency: the device store (503 until the API has started)."""
    if _store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Devices not initialized")
    return _store


def get_optional_monitor() -> Optional[DeviceMonitor]:
    return _monitor


class Device(BaseModel):
    id: int
    name: str
    kind: str
    address: Optional[str] = None
    port: Optional[int] = None
    role: Optional[str] = None
    channel: Optional[int] = None
    notes: Optional[str] = None
    enabled: bool = True
    source: str = "manual"
    config_key: Optional[str] = None
    in_config: bool = True
    last_seen: Optional[str] = None
    last_ok: Optional[bool] = None
    last_error: Optional[str] = None
    last_checked: Optional[str] = None
    info: Optional[dict] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DeviceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    kind: str = Field(..., pattern="^(pi|output|relay)$")
    address: Optional[str] = Field(None, max_length=255)
    port: Optional[int] = Field(None, ge=1, le=65535)
    role: Optional[str] = Field(None, max_length=40)
    channel: Optional[int] = Field(None, ge=0)
    notes: Optional[str] = Field(None, max_length=2000)
    enabled: bool = True


class DeviceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    kind: Optional[str] = Field(None, pattern="^(pi|output|relay)$")
    address: Optional[str] = Field(None, max_length=255)
    port: Optional[int] = Field(None, ge=1, le=65535)
    role: Optional[str] = Field(None, max_length=40)
    channel: Optional[int] = Field(None, ge=0)
    notes: Optional[str] = Field(None, max_length=2000)
    enabled: Optional[bool] = None


class Heartbeat(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    address: Optional[str] = Field(None, max_length=255)
    role: Optional[str] = Field(None, max_length=40)
    version: Optional[str] = Field(None, max_length=40)


class SyncResult(BaseModel):
    added: int
    updated: int
    missing: int


class DevicesStatus(BaseModel):
    monitor: dict
    devices: List[Device]


def _http(fn):
    try:
        return fn()
    except DeviceNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except DeviceConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except DeviceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("", response_model=List[Device])
def list_devices(kind: Optional[str] = None, store: DeviceStore = Depends(get_store),
                 current_user: dict = Depends(require_admin)):
    """All devices, optionally only one kind (pi, output, relay)."""
    return store.list(kind)


@router.post("", response_model=Device, status_code=status.HTTP_201_CREATED)
def create_device(body: DeviceCreate, store: DeviceStore = Depends(get_store),
                  current_user: dict = Depends(require_admin)):
    """Add a device by hand (e.g. another Pi)."""
    return _http(lambda: store.create(body.model_dump()))


@router.get("/status", response_model=DevicesStatus)
def devices_status(store: DeviceStore = Depends(get_store),
                   monitor: Optional[DeviceMonitor] = Depends(get_optional_monitor),
                   current_user: dict = Depends(require_admin)):
    """Every device with its last probe result, plus the monitor's own state."""
    mon = monitor.status() if monitor else {"enabled": False, "running": False}
    return {"monitor": mon, "devices": store.list()}


@router.post("/sync", response_model=SyncResult)
def sync_devices(store: DeviceStore = Depends(get_store),
                 config_dir: str = Depends(get_config_dir),
                 current_user: dict = Depends(require_admin)):
    """
    Add or refresh devices from the config (outputs, LED configs, network
    clients, relay channels). Safe to repeat; names and notes are kept.
    """
    try:
        return store.sync_from_config(config_dir)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Could not read the config: {e}")


@router.post("/probe", response_model=DevicesStatus)
async def probe_devices(store: DeviceStore = Depends(get_store),
                        monitor: Optional[DeviceMonitor] = Depends(get_optional_monitor),
                        current_user: dict = Depends(require_admin)):
    """Probe every enabled device now (takes up to ~2 s)."""
    if monitor is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Device monitor not initialized")
    await run_in_threadpool(monitor.probe_once)
    return {"monitor": monitor.status(), "devices": store.list()}


@router.post("/heartbeat", response_model=Device)
def heartbeat(body: Heartbeat, store: DeviceStore = Depends(get_store),
              current_user: dict = Depends(require_admin)):
    """
    A Pi reporting in (for client Pis that call the API). Creates the Pi if it
    is new and marks it seen. Uses the normal admin login for now.
    """
    info = {"version": body.version} if body.version else None
    return _http(lambda: store.heartbeat(body.name, body.address, body.role, info))


@router.get("/{device_id}", response_model=Device)
def get_device(device_id: int, store: DeviceStore = Depends(get_store),
               current_user: dict = Depends(require_admin)):
    return _http(lambda: store.get(device_id))


@router.patch("/{device_id}", response_model=Device)
def update_device(device_id: int, body: DeviceUpdate, store: DeviceStore = Depends(get_store),
                  config_dir: str = Depends(get_config_dir),
                  current_user: dict = Depends(require_admin)):
    """
    Rename, add notes, or enable/disable a device.

    For an ``[output:<name>]`` device, ``enabled`` is written to overrides.cfg
    (backed up, atomic; applies from the next song). For other devices it only
    turns status checks on or off. Address, port, role and channel of devices
    from the config are changed in the config editor.
    """
    changes = body.model_dump(exclude_unset=True)
    current = _http(lambda: store.get(device_id))
    key = current.get("config_key") or ""
    if ("enabled" in changes and changes["enabled"] != current["enabled"]
            and key.startswith("output:")):
        section = key  # config_key is the section name, e.g. output:nanoleaf
        try:
            cf.set_option(config_dir, cf.OVERRIDES, section, "enabled",
                          "true" if changes["enabled"] else "false",
                          user=current_user.get("username"))
        except cf.ConfigFileError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail=f"Could not update {cf.OVERRIDES}: {e}")
        log.info("Output %s %s by %s", section,
                 "enabled" if changes["enabled"] else "disabled",
                 current_user.get("username"))
    return _http(lambda: store.update(device_id, changes))


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: int, store: DeviceStore = Depends(get_store),
                  current_user: dict = Depends(require_admin)):
    """Delete a manual device, or a config device that is no longer in the config."""
    _http(lambda: store.delete(device_id))
