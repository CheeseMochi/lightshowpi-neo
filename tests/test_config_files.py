"""Tests for the config editor: name allowlist, safe writes, backups, masking,
warnings, and the /api/config router."""

import os
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.core.auth import get_current_user, require_admin
from api.routers import config_files as router_mod
from api.services import config_files as cf

DEFAULTS = """\
[hardware]
# Relays
gpio_pins = 0,1,2,3
pin_modes = onoff

[configs]
led_config =

[network]
networking = off
ip_clients =

[api]
enabled = False
cloud_key =
"""

LED_TEMPLATE = """\
[led]
led_connection = SACN
sacn_address =
"""


@pytest.fixture
def config_dir(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    (d / "defaults.cfg").write_text(DEFAULTS)
    (d / "led1.defaults.cfg").write_text(LED_TEMPLATE)
    (d / "state.cfg").write_text("[do_not_modify]\nplay_now = 0\n")
    (d / "testbed.cfg").write_text("[hardware]\n")
    return str(d)


def write(config_dir, name, text):
    with open(os.path.join(config_dir, name), "w") as f:
        f.write(text)


def read(config_dir, name):
    with open(os.path.join(config_dir, name)) as f:
        return f.read()


@pytest.fixture
def client(config_dir):
    app = FastAPI()
    app.include_router(router_mod.router)
    app.dependency_overrides[router_mod.get_config_dir] = lambda: config_dir
    app.dependency_overrides[require_admin] = lambda: {"username": "tester", "id": 1}
    app.dependency_overrides[get_current_user] = lambda: {"username": "tester", "id": 1}
    return TestClient(app)


# ---------------------------------------------------------------------------
# Names and allowlist
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestNames:

    @pytest.mark.parametrize("name", [
        "../overrides.cfg", "..", "../../etc/passwd", "a/b.cfg", "a\\b.cfg",
        "%2e%2e%2fx.cfg", "over\x00rides.cfg", ".hidden.cfg", "overrides.txt",
        "", "x..cfg", "/etc/passwd", "overrides.cfg/",
    ])
    def test_bad_names_rejected(self, config_dir, name):
        assert not cf.valid_name(name)
        with pytest.raises(cf.InvalidNameError):
            cf.check_name(config_dir, name)

    @pytest.mark.parametrize("name", ["state.cfg", "testbed.cfg"])
    def test_excluded_files(self, config_dir, name):
        with pytest.raises(cf.InvalidNameError):
            cf.check_name(config_dir, name)
        assert name not in {f["name"] for f in cf.list_files(config_dir)}

    def test_defaults_read_only(self, config_dir):
        cf.check_name(config_dir, "defaults.cfg")
        with pytest.raises(cf.ReadOnlyError):
            cf.check_name(config_dir, "defaults.cfg", write=True)
        with pytest.raises(cf.ReadOnlyError):
            cf.check_name(config_dir, "led1.defaults.cfg", write=True)

    def test_unlisted_file_rejected(self, config_dir):
        with pytest.raises(cf.InvalidNameError):
            cf.check_name(config_dir, "nothere.cfg", write=True)

    def test_symlink_out_of_dir_rejected(self, config_dir, tmp_path):
        outside = tmp_path / "secret.cfg"
        outside.write_text("[x]\n")
        os.symlink(outside, os.path.join(config_dir, "link.cfg"))
        with pytest.raises(cf.InvalidNameError):
            cf.check_name(config_dir, "link.cfg")

    def test_list_files(self, config_dir):
        write(config_dir, "overrides.cfg", "[configs]\nled_config = led1.cfg,led2.cfg\n")
        write(config_dir, "led1.cfg", "[led]\n")
        write(config_dir, "extra.cfg", "[hardware]\n")
        files = {f["name"]: f for f in cf.list_files(config_dir)}
        assert set(files) == {"overrides.cfg", "led1.cfg", "led2.cfg", "extra.cfg",
                              "defaults.cfg", "led1.defaults.cfg"}
        assert files["led2.cfg"]["exists"] is False
        assert files["led1.cfg"]["kind"] == "led"
        assert files["defaults.cfg"]["read_only"] is True
        assert files["overrides.cfg"]["read_only"] is False

    def test_overrides_listed_when_missing(self, config_dir):
        files = {f["name"]: f for f in cf.list_files(config_dir)}
        assert files["overrides.cfg"]["exists"] is False


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWrites:

    def test_new_overrides_created(self, config_dir):
        result = cf.write_file(config_dir, "overrides.cfg", "[network]\nnetworking = sacn_server",
                               cf.sha256_text(None))
        assert result["backup"] is None
        assert read(config_dir, "overrides.cfg") == "[network]\nnetworking = sacn_server\n"
        mode = stat.S_IMODE(os.stat(os.path.join(config_dir, "overrides.cfg")).st_mode)
        assert mode == 0o644

    def test_save_records_config_change_event(self, config_dir, events_db):
        import event_log
        cf.write_file(config_dir, "overrides.cfg", "[api]\nenabled = True\n",
                      cf.sha256_text(None), user="steve")
        events, total = event_log.fetch_events(types=["config_change"])
        assert total == 1
        assert events[0]["details"]["file"] == "overrides.cfg"
        assert events[0]["details"]["user"] == "steve"

    def test_stale_hash_rejected(self, config_dir):
        write(config_dir, "overrides.cfg", "[api]\nenabled = True\n")
        with pytest.raises(cf.StaleFileError):
            cf.write_file(config_dir, "overrides.cfg", "[api]\n", cf.sha256_text("other"))
        assert read(config_dir, "overrides.cfg") == "[api]\nenabled = True\n"

    def test_parse_error_not_written(self, config_dir):
        write(config_dir, "overrides.cfg", "[api]\n")
        sha = cf.sha256_text("[api]\n")
        with pytest.raises(cf.ParseError):
            cf.write_file(config_dir, "overrides.cfg", "no section header\n", sha)
        with pytest.raises(cf.ParseError):
            cf.write_file(config_dir, "overrides.cfg", "[a]\nx=1\n[a]\ny=2\n", sha)
        assert read(config_dir, "overrides.cfg") == "[api]\n"

    def test_mode_preserved_and_atomic(self, config_dir, monkeypatch):
        path = os.path.join(config_dir, "overrides.cfg")
        write(config_dir, "overrides.cfg", "[api]\n")
        os.chmod(path, 0o600)
        cf.write_file(config_dir, "overrides.cfg", "[api]\nenabled = True\n",
                      cf.sha256_text("[api]\n"))
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

        # A failure before os.replace leaves the old file and no temp files
        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(cf.os, "replace", boom)
        with pytest.raises(OSError):
            cf.write_file(config_dir, "overrides.cfg", "[api]\nenabled = False\n",
                          cf.sha256_text("[api]\nenabled = True\n"))
        assert read(config_dir, "overrides.cfg") == "[api]\nenabled = True\n"
        assert not [n for n in os.listdir(config_dir) if n.endswith(".tmp")]

    def test_backup_and_rotation(self, config_dir):
        write(config_dir, "overrides.cfg", "[api]\n# v0\n")
        text = "[api]\n# v0\n"
        for i in range(1, cf.BACKUP_KEEP + 6):
            new = f"[api]\n# v{i}\n"
            result = cf.write_file(config_dir, "overrides.cfg", new, cf.sha256_text(text))
            assert result["backup"].startswith("overrides.cfg.")
            text = new
        backups = cf.list_backups(config_dir, "overrides.cfg")
        assert len(backups) == cf.BACKUP_KEEP
        # Newest first, and the newest backup holds the previous version
        newest = cf.read_backup(config_dir, "overrides.cfg", backups[0]["id"])
        assert newest == f"[api]\n# v{cf.BACKUP_KEEP + 4}\n"

    def test_restore(self, config_dir):
        write(config_dir, "overrides.cfg", "[api]\n# old\n")
        cf.write_file(config_dir, "overrides.cfg", "[api]\n# new\n",
                      cf.sha256_text("[api]\n# old\n"))
        backup = cf.list_backups(config_dir, "overrides.cfg")[0]["id"]
        cf.restore_backup(config_dir, "overrides.cfg", backup)
        assert read(config_dir, "overrides.cfg") == "[api]\n# old\n"
        # The overwritten version was backed up too
        assert len(cf.list_backups(config_dir, "overrides.cfg")) == 2

    def test_restore_rejects_unknown_backup(self, config_dir):
        write(config_dir, "overrides.cfg", "[api]\n")
        with pytest.raises(cf.InvalidNameError):
            cf.read_backup(config_dir, "overrides.cfg", "../defaults.cfg")
        with pytest.raises(cf.InvalidNameError):
            cf.read_backup(config_dir, "overrides.cfg", "defaults.cfg.20260101-000000-000000")

    def test_set_option_text_keeps_comments(self):
        text = "# top\n[output:nl]\n# the strip\ntype = nanoleaf\nenabled = true\n\n[api]\nx = 1\n"
        out = cf.set_option_text(text, "output:nl", "enabled", "false")
        assert "enabled = false" in out and "# the strip" in out and "# top" in out
        out = cf.set_option_text("[output:nl]\ntype = nanoleaf\n\n[api]\n", "output:nl",
                                 "enabled", "false")
        assert out.index("enabled = false") < out.index("[api]")
        out = cf.set_option_text("[api]\n", "output:new", "enabled", "true")
        assert out.endswith("[output:new]\nenabled = true\n")

    def test_set_option_text_matches_section_spelling(self):
        text = "[Output: nl]\ntype = nanoleaf\nenabled = true\n"
        out = cf.set_option_text(text, "output:nl", "enabled", "false")
        assert out == "[Output: nl]\ntype = nanoleaf\nenabled = false\n"

    def test_set_option_writes_through_safe_path(self, config_dir):
        write(config_dir, "overrides.cfg", "[output:nl]\ntype = nanoleaf\nenabled = true\n")
        cf.set_option(config_dir, "overrides.cfg", "output:nl", "enabled", "false")
        assert "enabled = false" in read(config_dir, "overrides.cfg")
        assert len(cf.list_backups(config_dir, "overrides.cfg")) == 1


# ---------------------------------------------------------------------------
# Warnings and the effective view
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestValidation:

    def test_unknown_section_and_key_warn(self, config_dir):
        warnings = cf.validate(config_dir, "overrides.cfg",
                               "[hardware]\ngpio_pinz = 1\n[bogus]\nx = 1\n")
        assert "Unknown key 'gpio_pinz' in [hardware]" in warnings
        assert "Unknown section [bogus]" in warnings

    def test_output_sections_use_their_template(self, config_dir):
        warnings = cf.validate(config_dir, "overrides.cfg",
                               "[output:nl]\ntype = nanoleaf\nhost = 1.2.3.4\nbogus = 1\n")
        assert warnings == ["Unknown key 'bogus' in [output:nl]"]
        assert cf.validate(config_dir, "overrides.cfg", "[output:x]\nhost = a\n") == [
            "[output:x] has no 'type' (e.g. type = nanoleaf)"]

    def test_led_config_uses_led_template(self, config_dir):
        write(config_dir, "overrides.cfg", "[configs]\nled_config = led1.cfg\n")
        assert cf.validate(config_dir, "led1.cfg", "[led]\nsacn_address = 10.0.0.9\n") == []
        assert cf.validate(config_dir, "led1.cfg", "[led]\nnope = 1\n") == [
            "Unknown key 'nope' in [led]"]

    def test_real_defaults_accept_contrib_and_lightshow_keys(self, tmp_path):
        """The checked-in defaults.cfg: no false warnings for real configs."""
        repo_config = os.path.join(os.path.dirname(__file__), "..", "config")
        d = tmp_path / "real"
        d.mkdir()
        for name in ("defaults.cfg", "led1.defaults.cfg"):
            with open(os.path.join(repo_config, name)) as f:
                (d / name).write_text(f.read())
        contrib = os.path.join(repo_config, "contrib")
        for name in sorted(os.listdir(contrib)):
            if name.endswith(".cfg"):
                with open(os.path.join(contrib, name)) as f:
                    assert cf.validate(str(d), "overrides.cfg", f.read()) == [], name
        # Read from [lightshow] but documented under [ambient] in defaults.cfg
        sample = ("[lightshow]\nlight_delay = 0.2\nlight_delay_legacy_math = False\n"
                  "decay_factor = 0.1\n[ambient]\nbrightness = 0.5\n"
                  "[devices]\nprobe_interval = 30\n")
        assert cf.validate(str(d), "overrides.cfg", sample) == []
        assert cf.effective_config(str(d))["sections"]["devices"]

    def test_effective_masks_secrets(self, config_dir):
        write(config_dir, "overrides.cfg",
              "[api]\ncloud_key = abc\nenabled = True\n"
              "[output:nl]\ntype = nanoleaf\ntoken = s3cret\n")
        eff = cf.effective_config(config_dir)
        assert eff["sections"]["api"]["cloud_key"]["value"] == cf.MASK
        assert eff["sections"]["output:nl"]["token"]["value"] == cf.MASK
        assert eff["sections"]["api"]["enabled"] == {"value": "True", "source": "overrides"}
        assert eff["sections"]["hardware"]["pin_modes"]["source"] == "defaults"
        assert "s3cret" not in repr(eff)

    def test_effective_blank_secret_not_masked(self, config_dir):
        eff = cf.effective_config(config_dir)
        assert eff["sections"]["api"]["cloud_key"]["value"] == ""


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRouter:

    def test_list(self, client):
        res = client.get("/api/config/files")
        assert res.status_code == 200
        assert "overrides.cfg" in {f["name"] for f in res.json()}

    @pytest.mark.parametrize("name", ["state.cfg", "testbed.cfg", "..%2Fdefaults.cfg",
                                      "%2E%2E%2Fpasswd", "nothere.cfg"])
    def test_get_rejected(self, client, name):
        assert client.get(f"/api/config/files/{name}").status_code == 404

    def test_get_put_roundtrip(self, client, config_dir):
        res = client.get("/api/config/files/overrides.cfg")
        assert res.status_code == 200
        body = res.json()
        assert body["exists"] is False and body["content"] == ""
        res = client.put("/api/config/files/overrides.cfg",
                         json={"content": "[api]\nenabled = True\nfoo = 1\n",
                               "base_sha256": body["sha256"]})
        assert res.status_code == 200, res.text
        assert res.json()["warnings"] == ["Unknown key 'foo' in [api]"]
        assert "next song" in res.json()["note"]
        assert read(config_dir, "overrides.cfg") == "[api]\nenabled = True\nfoo = 1\n"

    def test_put_stale_is_409(self, client, config_dir):
        write(config_dir, "overrides.cfg", "[api]\n")
        res = client.put("/api/config/files/overrides.cfg",
                         json={"content": "[api]\n", "base_sha256": "0" * 64})
        assert res.status_code == 409

    def test_put_defaults_is_403(self, client):
        sha = client.get("/api/config/files/defaults.cfg").json()["sha256"]
        res = client.put("/api/config/files/defaults.cfg",
                         json={"content": "[api]\n", "base_sha256": sha})
        assert res.status_code == 403

    def test_put_parse_error_is_400(self, client):
        sha = client.get("/api/config/files/overrides.cfg").json()["sha256"]
        res = client.put("/api/config/files/overrides.cfg",
                         json={"content": "garbage\n", "base_sha256": sha})
        assert res.status_code == 400

    def test_validate(self, client):
        res = client.post("/api/config/files/overrides.cfg/validate",
                          json={"content": "garbage\n", "base_sha256": "0" * 64})
        assert res.json()["ok"] is False

    def test_backups_and_restore(self, client, config_dir):
        write(config_dir, "overrides.cfg", "[api]\n# a\n")
        sha = client.get("/api/config/files/overrides.cfg").json()["sha256"]
        client.put("/api/config/files/overrides.cfg",
                   json={"content": "[api]\n# b\n", "base_sha256": sha})
        backups = client.get("/api/config/files/overrides.cfg/backups").json()
        assert len(backups) == 1
        bid = backups[0]["id"]
        assert client.get(f"/api/config/files/overrides.cfg/backups/{bid}").json()[
            "content"] == "[api]\n# a\n"
        res = client.post(f"/api/config/files/overrides.cfg/backups/{bid}/restore")
        assert res.status_code == 200
        assert read(config_dir, "overrides.cfg") == "[api]\n# a\n"
        assert client.post(
            "/api/config/files/overrides.cfg/backups/nope/restore").status_code == 404

    def test_effective(self, client, config_dir):
        write(config_dir, "overrides.cfg", "[api]\ncloud_key = hunter2\n")
        res = client.get("/api/config/effective")
        assert res.status_code == 200
        assert "hunter2" not in res.text

    def test_effective_with_broken_overrides_is_400(self, client, config_dir):
        write(config_dir, "overrides.cfg", "no header\n")
        assert client.get("/api/config/effective").status_code == 400
        # The file can still be opened to fix it
        assert client.get("/api/config/files/overrides.cfg").status_code == 200
