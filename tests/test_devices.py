"""Tests for the device registry, config sync, the device monitor (network
mocked) and the /api/devices router."""

import os
import socket
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.core.auth import get_current_user, require_admin
from api.core.database import Database
from api.routers import devices as router_mod
from api.routers.config_files import get_config_dir
from api.services import config_files as cf
from api.services import device_monitor as dm
from api.services.devices import DeviceConflict, DeviceError, DeviceStore, config_devices

DEFAULTS = """\
[hardware]
gpio_pins = 0,1,2

[configs]
led_config =

[network]
networking = off
ip_clients =
sacn_address =
"""

LED_TEMPLATE = """\
[led]
led_connection = SACN
enable_multicast = False
sacn_address =
"""

OVERRIDES = """\
[configs]
led_config = led1.cfg

[network]
networking = sacn_server
sacn_address = 192.168.1.21, 192.168.1.22

# The roofline strip
[output:nanoleaf]
type = nanoleaf
enabled = true
host = 192.168.1.50
token = s3cret-token
"""


@pytest.fixture
def config_dir(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    (d / "defaults.cfg").write_text(DEFAULTS)
    (d / "led1.defaults.cfg").write_text(LED_TEMPLATE)
    (d / "overrides.cfg").write_text(OVERRIDES)
    (d / "led1.cfg").write_text("[led]\nsacn_address = 192.168.1.60\n")
    return str(d)


@pytest.fixture
def store(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize_schema()
    return DeviceStore(db)


def by_key(store):
    return {d["config_key"]: d for d in store.list() if d["config_key"]}


# ---------------------------------------------------------------------------
# Config sync
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSync:

    def test_config_devices(self, config_dir):
        items = {i["config_key"]: i for i in config_devices(config_dir, hostname="piserver")}
        assert items["self"]["name"] == "piserver"
        assert items["self"]["role"] == "sacn_server"
        assert items["pi:192.168.1.21"]["role"] == "sacn_client"
        assert items["output:nanoleaf"]["address"] == "192.168.1.50"
        assert items["output:nanoleaf"]["port"] == 16021
        assert items["led:led1.cfg"]["address"] == "192.168.1.60"
        assert items["led:led1.cfg"]["role"] == "sacn_led"
        assert [items[f"relay:{i}"]["channel"] for i in range(3)] == [0, 1, 2]
        assert "s3cret" not in repr(items)

    def test_sync_is_idempotent_and_keeps_names(self, store, config_dir):
        first = store.sync_from_config(config_dir, hostname="piserver")
        assert first["added"] == 1 + 2 + 1 + 1 + 3
        relay0 = by_key(store)["relay:0"]
        store.update(relay0["id"], {"name": "letter", "notes": "the big L"})

        again = store.sync_from_config(config_dir, hostname="piserver")
        assert again == {"added": 0, "updated": 0, "missing": 0}
        relay0 = by_key(store)["relay:0"]
        assert relay0["name"] == "letter" and relay0["notes"] == "the big L"
        assert len(store.list()) == first["added"]

    def test_sync_updates_address_and_flags_missing(self, store, config_dir):
        store.sync_from_config(config_dir, hostname="piserver")
        text = OVERRIDES.replace("192.168.1.50", "192.168.1.51").replace(
            ", 192.168.1.22", "")
        with open(os.path.join(config_dir, "overrides.cfg"), "w") as f:
            f.write(text)
        result = store.sync_from_config(config_dir, hostname="piserver")
        assert result["missing"] == 1 and result["updated"] == 1
        keys = by_key(store)
        assert keys["output:nanoleaf"]["address"] == "192.168.1.51"
        assert keys["pi:192.168.1.22"]["in_config"] is False
        # A device gone from config can be deleted; one in config can't
        store.delete(keys["pi:192.168.1.22"]["id"])
        with pytest.raises(DeviceConflict):
            store.delete(keys["relay:0"]["id"])

    def test_name_clash_gets_suffix(self, store, config_dir):
        store.create({"name": "nanoleaf", "kind": "pi"})
        store.sync_from_config(config_dir, hostname="piserver")
        assert by_key(store)["output:nanoleaf"]["name"] == "nanoleaf (2)"

    def test_disabled_output(self, store, config_dir):
        with open(os.path.join(config_dir, "overrides.cfg"), "w") as f:
            f.write(OVERRIDES.replace("enabled = true", "enabled = false"))
        store.sync_from_config(config_dir, hostname="piserver")
        assert by_key(store)["output:nanoleaf"]["enabled"] is False


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestStore:

    def test_crud(self, store):
        d = store.create({"name": "piclient", "kind": "pi", "address": "10.0.0.5"})
        assert d["source"] == "manual" and d["enabled"] is True
        d = store.update(d["id"], {"notes": "neighbor", "port": 2222})
        assert d["notes"] == "neighbor" and d["port"] == 2222
        with pytest.raises(DeviceConflict):
            store.create({"name": "piclient", "kind": "pi"})
        with pytest.raises(DeviceError):
            store.create({"name": "x", "kind": "toaster"})
        store.delete(d["id"])
        assert store.list() == []

    def test_config_owned_fields_locked(self, store, config_dir):
        store.sync_from_config(config_dir, hostname="piserver")
        relay = by_key(store)["relay:1"]
        with pytest.raises(DeviceError):
            store.update(relay["id"], {"channel": 5})
        assert store.update(relay["id"], {"name": "snowflake"})["name"] == "snowflake"

    def test_heartbeat(self, store):
        d = store.heartbeat("piclient", address="10.0.0.7", info={"version": "0.10"})
        assert d["kind"] == "pi" and d["last_ok"] is True and d["last_seen"]
        assert d["info"] == {"version": "0.10"}
        store.create({"name": "strip", "kind": "output"})
        with pytest.raises(DeviceConflict):
            store.heartbeat("strip")


# ---------------------------------------------------------------------------
# Monitor (network mocked)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMonitor:

    def test_probe_dispatch(self, store, config_dir):
        store.sync_from_config(config_dir, hostname="piserver")
        tcp = MagicMock(return_value=(True, None))
        http = MagicMock(return_value=(False, "http: timeout"))
        mon = dm.DeviceMonitor(store, timeout_s=5, version="9.9", tcp=tcp, http=http)
        assert mon.timeout_s == dm.MAX_TIMEOUT_S
        results = {r["name"]: r for r in mon.probe_once()}
        tcp.assert_any_call("192.168.1.50", 16021, 2.0)
        tcp.assert_any_call("192.168.1.21", 22, 2.0)
        http.assert_called_once_with("http://192.168.1.60/", 2.0)
        assert "Relay 0" not in results  # relays aren't probed
        keys = by_key(store)
        assert keys["self"]["last_ok"] is True
        assert keys["self"]["info"]["version"] == "9.9"
        assert keys["led:led1.cfg"]["last_ok"] is False
        assert keys["led:led1.cfg"]["last_error"] == "http: timeout"
        assert keys["led:led1.cfg"]["last_seen"] is None
        assert keys["output:nanoleaf"]["last_seen"]

    def test_disabled_devices_not_probed(self, store):
        d = store.create({"name": "pi2", "kind": "pi", "address": "10.0.0.9",
                          "enabled": False})
        tcp = MagicMock(return_value=(True, None))
        dm.DeviceMonitor(store, tcp=tcp).probe_once()
        tcp.assert_not_called()
        assert store.get(d["id"])["last_checked"] is None

    def test_disabled_monitor_does_not_start(self, store):
        mon = dm.DeviceMonitor(store, enabled=False)
        assert mon.start() is False
        assert mon.is_running() is False

    def test_monitor_thread_starts_and_stops(self, store):
        tcp = MagicMock(return_value=(True, None))
        mon = dm.DeviceMonitor(store, interval_s=60, tcp=tcp)
        assert mon.start() is True
        mon.stop()
        assert not mon.is_running()

    def test_probe_tcp(self):
        with patch.object(dm.socket, "create_connection") as cc:
            assert dm.probe_tcp("h", 22, 1.0) == (True, None)
            cc.assert_called_once_with(("h", 22), timeout=1.0)
            cc.side_effect = socket.timeout()
            assert dm.probe_tcp("h", 22, 1.0)[0] is False
            cc.side_effect = ConnectionRefusedError(61, "Connection refused")
            assert dm.probe_tcp("h", 22, 1.0) == (False, "port 22: Connection refused")

    def test_probe_http(self):
        with patch.object(dm.urllib.request, "urlopen") as uo:
            uo.return_value.__enter__.return_value.read.return_value = b"ok"
            assert dm.probe_http("http://h/", 1.0) == (True, None)
            uo.side_effect = urllib.error.HTTPError("http://h/", 401, "no", {}, None)
            assert dm.probe_http("http://h/", 1.0) == (True, None)
            uo.side_effect = urllib.error.URLError(OSError(65, "No route to host"))
            ok, err = dm.probe_http("http://h/", 1.0)
            assert ok is False and "No route" in err


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

@pytest.fixture
def client(store, config_dir):
    app = FastAPI()
    app.include_router(router_mod.router)
    mon = dm.DeviceMonitor(store, enabled=False, tcp=MagicMock(return_value=(True, None)),
                           http=MagicMock(return_value=(True, None)))
    app.dependency_overrides[router_mod.get_store] = lambda: store
    app.dependency_overrides[router_mod.get_optional_monitor] = lambda: mon
    app.dependency_overrides[get_config_dir] = lambda: config_dir
    app.dependency_overrides[require_admin] = lambda: {"username": "tester", "id": 1}
    app.dependency_overrides[get_current_user] = lambda: {"username": "tester", "id": 1}
    return TestClient(app)


@pytest.mark.unit
class TestRouter:

    def test_sync_and_status(self, client):
        res = client.post("/api/devices/sync")
        assert res.status_code == 200 and res.json()["added"] > 0
        assert client.post("/api/devices/sync").json()["added"] == 0
        status = client.get("/api/devices/status").json()
        assert status["monitor"]["enabled"] is False
        assert {d["kind"] for d in status["devices"]} == {"pi", "output", "relay"}
        assert "s3cret" not in client.get("/api/devices").text

    def test_crud(self, client):
        res = client.post("/api/devices", json={"name": "bench", "kind": "pi",
                                                "address": "10.0.0.3"})
        assert res.status_code == 201
        dev = res.json()
        assert client.get(f"/api/devices/{dev['id']}").json()["name"] == "bench"
        res = client.patch(f"/api/devices/{dev['id']}", json={"notes": "Pi 5"})
        assert res.json()["notes"] == "Pi 5"
        assert client.post("/api/devices", json={"name": "bench", "kind": "pi"}
                           ).status_code == 409
        assert client.post("/api/devices", json={"name": "x", "kind": "toaster"}
                           ).status_code == 422
        assert client.delete(f"/api/devices/{dev['id']}").status_code == 204
        assert client.get(f"/api/devices/{dev['id']}").status_code == 404

    def test_list_by_kind(self, client):
        client.post("/api/devices/sync")
        relays = client.get("/api/devices?kind=relay").json()
        assert [r["channel"] for r in relays] == [0, 1, 2]

    def test_disable_output_edits_overrides(self, client, config_dir):
        client.post("/api/devices/sync")
        nl = next(d for d in client.get("/api/devices?kind=output").json()
                  if d["config_key"] == "output:nanoleaf")
        res = client.patch(f"/api/devices/{nl['id']}", json={"enabled": False})
        assert res.status_code == 200 and res.json()["enabled"] is False
        text = open(os.path.join(config_dir, "overrides.cfg")).read()
        assert "enabled = false" in text
        assert "# The roofline strip" in text and "token = s3cret-token" in text
        assert len(cf.list_backups(config_dir, "overrides.cfg")) == 1
        # Sync reads it back as disabled
        client.post("/api/devices/sync")
        assert client.get(f"/api/devices/{nl['id']}").json()["enabled"] is False

    def test_relay_enable_does_not_touch_config(self, client, config_dir):
        client.post("/api/devices/sync")
        relay = client.get("/api/devices?kind=relay").json()[0]
        client.patch(f"/api/devices/{relay['id']}", json={"enabled": False})
        assert cf.list_backups(config_dir, "overrides.cfg") == []

    def test_probe_now(self, client):
        client.post("/api/devices/sync")
        res = client.post("/api/devices/probe")
        assert res.status_code == 200
        selfdev = next(d for d in res.json()["devices"] if d["config_key"] == "self")
        assert selfdev["last_ok"] is True

    def test_heartbeat(self, client):
        res = client.post("/api/devices/heartbeat",
                          json={"name": "piclient", "address": "10.0.0.8", "version": "0.10"})
        assert res.status_code == 200
        assert res.json()["last_ok"] is True
