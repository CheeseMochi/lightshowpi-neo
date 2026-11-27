"""
Layout router: the show's channel layout (GPIO, LED EXTEND and virtual
channels) and which channels each output reads. Read-only; any logged-in user.
"""

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.core.auth import get_current_user
from api.routers import devices as devices_router
from api.routers.config_files import get_config_dir
from api.services.layout import channel_layout

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/layout", tags=["Layout"])


class LayoutOutput(BaseModel):
    name: str
    type: str
    channels: List[int]


class Layout(BaseModel):
    gpio_len: int
    physical: List[int]
    led_extend: Optional[List[int]] = None
    virtual: Optional[List[int]] = None
    virtual_sources: List[int]
    outputs: List[LayoutOutput]
    channel_labels: Dict[str, str]
    warnings: List[str]


def _relay_labels() -> Dict[int, str]:
    """Relay names from the device registry, if it is running."""
    store = devices_router._store
    if store is None:
        return {}
    try:
        return {d["channel"]: d["name"] for d in store.list("relay")
                if d.get("channel") is not None}
    except Exception as e:  # labels are a nicety; never fail the layout over them
        log.warning("Could not read relay names: %s", e)
        return {}


@router.get("", response_model=Layout)
def get_layout(config_dir: str = Depends(get_config_dir),
               current_user: dict = Depends(get_current_user)):
    """
    Show channel ranges as ``[start, end)``: ``physical`` (GPIO), ``led_extend``
    (LED EXTEND/LEDONLY channels) and ``virtual`` (``[hardware]
    virtual_channels``), each null when empty, plus every output's channels.
    CSV sequences have ``gpio_len`` columns.
    """
    try:
        return channel_layout(config_dir, _relay_labels())
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Could not read the config: {e}")
