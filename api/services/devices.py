"""
Device registry: Pis, outputs and relay channels, stored in the ``devices`` table.

Three kinds:
- ``pi``: this host (``config_key = self``), clients from ``[network]``
  (``ip_clients`` for the legacy server, ``sacn_address`` for an sACN server),
  and Pis added by hand or by heartbeat. With the planned API-only show
  manager (every relay box a client), each relay Pi is just a ``pi`` row with
  a heartbeat, so this model already fits.
- ``output``: ``[output:<name>]`` sections (Nanoleaf...) and LED configs
  (an sACN target such as an ESPixelStick).
- ``relay``: one row per ``[hardware] gpio_pins`` channel, for friendly names
  (letter, snowflake...) and test buttons.

``sync_from_config`` is idempotent: config rows are keyed by ``config_key`` and
a sync never touches a row's name or notes. Rows whose config entry disappeared
are kept (with their notes) and flagged ``in_config = 0``; they can then be
deleted. Tokens and passwords are never copied out of the config.
"""

import json
import logging
import os
import socket
from datetime import datetime, timezone
from typing import Dict, List, Optional

from api.services import config_files as cf

log = logging.getLogger(__name__)

KINDS = ("pi", "output", "relay")
DEFAULT_SSH_PORT = 22
NANOLEAF_PORT = 16021

# Columns a caller may set (status columns are written by the monitor only)
EDITABLE = ("name", "kind", "address", "port", "role", "channel", "notes", "enabled")
# Columns that come from config on config rows (editing them would be undone by sync)
CONFIG_OWNED = ("kind", "address", "port", "role", "channel")


class DeviceError(Exception):
    """Base error for device operations."""


class DeviceNotFound(DeviceError):
    pass


class DeviceConflict(DeviceError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r) -> dict:
    d = dict(r)
    d["enabled"] = bool(d.get("enabled"))
    d["in_config"] = bool(d.get("in_config"))
    if d.get("last_ok") is not None:
        d["last_ok"] = bool(d["last_ok"])
    try:
        d["info"] = json.loads(d["info"]) if d.get("info") else None
    except ValueError:
        d["info"] = None
    return d


def _truthy(value: Optional[str], default: bool = True) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).split("#", 1)[0].strip().lower() not in ("0", "false", "no", "off")


def _clean(value: Optional[str]) -> str:
    """Strip an inline ``# comment`` (the config loader keeps it in the value)."""
    return (value or "").split(" #", 1)[0].split("\t#", 1)[0].strip()


def config_devices(config_dir: str, hostname: Optional[str] = None) -> List[dict]:
    """The devices the config describes, each with a stable ``config_key``."""
    merged = cf.load_merged(config_dir)
    host = hostname or socket.gethostname().split(".")[0]
    networking = _clean(merged.get("network", "networking", fallback="off")) or "off"
    items = [{
        "config_key": "self", "kind": "pi", "name": host, "address": host,
        "port": None, "role": "standalone" if networking == "off" else networking,
        "channel": None,
    }]

    clients = []
    if networking in ("server", "serverjson"):
        clients = cf.split_csv(_clean(merged.get("network", "ip_clients", fallback="")))
        role = "client"
    elif networking == "sacn_server":
        clients = cf.split_csv(_clean(merged.get("network", "sacn_address", fallback="")))
        role = "sacn_client"
    for addr in clients:
        items.append({"config_key": f"pi:{addr}", "kind": "pi", "name": f"Client {addr}",
                      "address": addr, "port": DEFAULT_SSH_PORT, "role": role,
                      "channel": None})

    for section in merged.sections():
        if not section.lower().startswith("output:"):
            continue
        name = section.split(":", 1)[1].strip()
        otype = _clean(merged.get(section, "type", fallback="")).lower() or "output"
        port = _clean(merged.get(section, "port", fallback=""))
        items.append({
            "config_key": f"output:{name}", "kind": "output", "name": name,
            "address": _clean(merged.get(section, "host", fallback="")) or None,
            "port": int(port) if port.isdigit() else
            (NANOLEAF_PORT if otype == "nanoleaf" else None),
            "role": otype, "channel": None,
            "enabled": _truthy(merged.get(section, "enabled", fallback="true")),
        })

    led_raw = _clean(merged.get("configs", "led_config", fallback=""))
    for led_name in cf.split_csv(led_raw):
        if not cf.valid_name(led_name):
            continue
        led = cf.new_parser()
        led.read([os.path.join(config_dir, cf.LED_TEMPLATE),
                  os.path.join(config_dir, led_name)], encoding="utf-8")
        conn = _clean(led.get("led", "led_connection", fallback="")).upper()
        address = None
        role = conn.lower() or "led"
        if conn == "SACN":
            role = "sacn_led"
            multicast = _truthy(led.get("led", "enable_multicast", fallback="false"), False)
            target = _clean(led.get("led", "sacn_address", fallback=""))
            address = None if multicast else (target or None)
        items.append({"config_key": f"led:{led_name}", "kind": "output",
                      "name": os.path.splitext(led_name)[0], "address": address,
                      "port": None, "role": role, "channel": None})

    pins = cf.split_csv(merged.get("hardware", "gpio_pins", fallback=""))
    for i, pin in enumerate(pins):
        items.append({"config_key": f"relay:{i}", "kind": "relay", "name": f"Relay {i}",
                      "address": f"pin {pin}", "port": None, "role": "gpio", "channel": i})
    return items


class DeviceStore:
    """CRUD and status updates for the ``devices`` table."""

    def __init__(self, db):
        self.db = db

    # -- reads ---------------------------------------------------------------

    def list(self, kind: Optional[str] = None) -> List[dict]:
        with self.db.get_connection() as conn:
            if kind:
                rows = conn.execute(
                    "SELECT * FROM devices WHERE kind = ? ORDER BY channel, name",
                    (kind,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM devices ORDER BY kind, channel, name").fetchall()
        return [_row(r) for r in rows]

    def get(self, device_id: int) -> dict:
        with self.db.get_connection() as conn:
            r = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        if r is None:
            raise DeviceNotFound(f"Device {device_id} not found")
        return _row(r)

    def get_by_name(self, name: str) -> Optional[dict]:
        with self.db.get_connection() as conn:
            r = conn.execute("SELECT * FROM devices WHERE name = ?", (name,)).fetchone()
        return _row(r) if r else None

    # -- writes --------------------------------------------------------------

    def create(self, data: dict, source: str = "manual", config_key: Optional[str] = None,
               conn=None) -> dict:
        fields = {k: data.get(k) for k in EDITABLE if k in data}
        if fields.get("kind") not in KINDS:
            raise DeviceError(f"kind must be one of {', '.join(KINDS)}")
        if not (fields.get("name") or "").strip():
            raise DeviceError("name is required")
        fields["name"] = fields["name"].strip()
        fields["enabled"] = 1 if fields.get("enabled", True) else 0
        fields.update(source=source, config_key=config_key, in_config=1,
                      created_at=now_iso(), updated_at=now_iso())
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)

        def run(c):
            try:
                cur = c.execute(f"INSERT INTO devices ({cols}) VALUES ({marks})",
                                tuple(fields.values()))
            except Exception as e:
                if "UNIQUE" in str(e):
                    raise DeviceConflict(f"A device named {fields['name']!r} already exists")
                raise
            return cur.lastrowid

        if conn is not None:
            return {"id": run(conn)}
        with self.db.get_connection() as c:
            new_id = run(c)
        return self.get(new_id)

    def update(self, device_id: int, changes: dict) -> dict:
        current = self.get(device_id)
        fields = {k: v for k, v in changes.items() if k in EDITABLE}
        if not fields:
            return current
        if current["config_key"]:
            owned = [k for k in CONFIG_OWNED if k in fields and fields[k] != current[k]]
            if owned:
                raise DeviceError(
                    f"{', '.join(owned)} of {current['name']!r} come from the config; "
                    "change them in the config editor")
        if "kind" in fields and fields["kind"] not in KINDS:
            raise DeviceError(f"kind must be one of {', '.join(KINDS)}")
        if "name" in fields:
            if not (fields["name"] or "").strip():
                raise DeviceError("name is required")
            fields["name"] = fields["name"].strip()
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        try:
            with self.db.get_connection() as conn:
                conn.execute(f"UPDATE devices SET {sets} WHERE id = ?",
                             (*fields.values(), device_id))
        except Exception as e:
            if "UNIQUE" in str(e):
                raise DeviceConflict(f"A device named {fields.get('name')!r} already exists")
            raise
        return self.get(device_id)

    def delete(self, device_id: int) -> None:
        current = self.get(device_id)
        if current["config_key"] and current["in_config"]:
            raise DeviceConflict(
                f"{current['name']!r} comes from the config and would come back on the "
                "next sync; remove it from the config first")
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))

    def record_status(self, device_id: int, ok: bool, error: Optional[str] = None,
                      info: Optional[dict] = None, seen: bool = None) -> None:
        """Store a probe result. ``last_seen`` moves only when the device answered."""
        stamp = now_iso()
        seen = ok if seen is None else seen
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE devices SET last_ok = ?, last_error = ?, last_checked = ?, "
                "info = COALESCE(?, info), last_seen = CASE WHEN ? THEN ? ELSE last_seen END "
                "WHERE id = ?",
                (1 if ok else 0, None if ok else (error or "unreachable")[:200], stamp,
                 json.dumps(info) if info is not None else None, 1 if seen else 0, stamp,
                 device_id))

    def heartbeat(self, name: str, address: Optional[str] = None,
                  role: Optional[str] = None, info: Optional[dict] = None) -> dict:
        """A Pi reporting in: create it if new, then mark it seen."""
        device = self.get_by_name(name)
        if device is None:
            device = self.create({"name": name, "kind": "pi", "address": address,
                                  "role": role or "client", "port": DEFAULT_SSH_PORT})
        elif device["kind"] != "pi":
            raise DeviceConflict(f"{name!r} is a {device['kind']}, not a pi")
        elif address and not device["config_key"] and address != device["address"]:
            self.update(device["id"], {"address": address})
        self.record_status(device["id"], True, info=info)
        return self.get(device["id"])

    # -- sync ----------------------------------------------------------------

    def sync_from_config(self, config_dir: str, hostname: Optional[str] = None) -> Dict[str, int]:
        """
        Add or refresh config-derived devices. Idempotent.

        Never changes name or notes of an existing row; flags rows whose config
        entry is gone with ``in_config = 0`` instead of deleting them.

        Returns:
            dict: counts of added, updated and missing rows
        """
        items = config_devices(config_dir, hostname)
        added = updated = 0
        with self.db.get_connection() as conn:
            existing = {r["config_key"]: dict(r) for r in conn.execute(
                "SELECT * FROM devices WHERE config_key IS NOT NULL").fetchall()}
            names = {r[0] for r in conn.execute("SELECT name FROM devices").fetchall()}
            for item in items:
                key = item["config_key"]
                values = {k: item.get(k) for k in CONFIG_OWNED}
                if "enabled" in item:
                    values["enabled"] = 1 if item["enabled"] else 0
                row = existing.get(key)
                if row is None:
                    name = _unique(item["name"], names)
                    names.add(name)
                    data = dict(values, name=name)
                    data.setdefault("enabled", 1)
                    self.create(data, source="config", config_key=key, conn=conn)
                    added += 1
                    continue
                changed = {k: v for k, v in values.items() if row.get(k) != v}
                if not row["in_config"]:
                    changed["in_config"] = 1
                if changed:
                    changed["updated_at"] = now_iso()
                    sets = ", ".join(f"{k} = ?" for k in changed)
                    conn.execute(f"UPDATE devices SET {sets} WHERE id = ?",
                                 (*changed.values(), row["id"]))
                    updated += 1
            keys = {i["config_key"] for i in items}
            gone = [r["id"] for k, r in existing.items() if k not in keys and r["in_config"]]
            for device_id in gone:
                conn.execute("UPDATE devices SET in_config = 0, updated_at = ? WHERE id = ?",
                             (now_iso(), device_id))
        result = {"added": added, "updated": updated, "missing": len(gone)}
        log.info("Device sync from config: %s", result)
        return result


def _unique(name: str, taken: set) -> str:
    if name not in taken:
        return name
    n = 2
    while f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"
