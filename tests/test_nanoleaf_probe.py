"""Tests for py/nanoleaf_protocol.py and tools/nanoleaf_probe.py.

Network tests use a mock Nanoleaf HTTP server and a UDP receiver on 127.0.0.1.
If the environment forbids binding local sockets they are skipped with an
explicit reason (visible in pytest -ra output).
"""
import importlib.util
import json
import os
import socket
import stat
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import nanoleaf_protocol as nl

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("nanoleaf_probe",
                                               REPO / "tools" / "nanoleaf_probe.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

pytestmark = pytest.mark.unit

TOKEN = "ppGL6lMTx6bjC3Lri3VLWyNDEh8olxk5"


def hx(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", ""))


# --------------------------------------------------------------------------
# Frame encoding (byte exact)
# --------------------------------------------------------------------------

class TestEncodeV2:
    def test_single_led(self):
        assert nl.encode_frame_v2([(0, 255, 0, 0)]) == hx("0001 0000 FF 00 00 00 0000")

    def test_spec_records_match_nanoleaf_doc(self):
        # Essentials doc example, transition 5 (=500ms). The doc's header (00 04)
        # and LED 128 colour bytes are inconsistent; LED 23 and 282 are not.
        frame = nl.encode_frame_v2(
            [(128, 0xFF, 0, 0), (23, 0xFF, 0, 0xFF), (282, 0x80, 0xFF, 0)], transition=5)
        assert frame[:2] == hx("0003")
        assert frame[2:10] == hx("0080 FF 00 00 00 0005")
        assert frame[10:18] == hx("0017 FF 00 FF 00 0005")   # verbatim from doc
        assert frame[18:26] == hx("011A 80 FF 00 00 0005")   # verbatim from doc
        assert len(frame) == 26

    def test_matches_ledfx_reference_packing(self):
        # LedFx (commit 0683715) packs >H count then >HBBBBH id,r,g,b,w=0,trans=0.
        pixels = [(i, i, 2 * i, 3 * i) for i in range(20)]
        ref = struct.pack(">H", 20) + b"".join(
            struct.pack(">HBBBBH", i, r, g, b, 0, 0) for i, r, g, b in pixels)
        assert nl.encode_frame_v2(pixels) == ref

    def test_clamps_colours_and_roundtrips(self):
        frame = nl.encode_frame_v2([(7, 300, -5, 128)], transition=1)
        assert nl.decode_frame_v2(frame) == [(7, 255, 0, 128, 0, 1)]

    def test_id_out_of_range(self):
        with pytest.raises(ValueError):
            nl.encode_frame_v2([(70000, 1, 2, 3)])


class TestEncodeV1:
    def test_two_panels(self):
        frame = nl.encode_frame_v1([(1, 255, 0, 0), (2, 0, 255, 0)], transition=1)
        assert frame == hx("02" "01 01 FF 00 00 00 01" "02 01 00 FF 00 00 01")

    def test_id_out_of_range(self):
        with pytest.raises(ValueError):
            nl.encode_frame_v1([(256, 1, 2, 3)])

    def test_encode_packets_truncates_to_v1_range(self):
        pixels = nl.colors_to_pixels([(1, 2, 3)] * 300)
        (pkt,) = nl.encode_packets(nl.LAYOUTS["v1"], pixels)
        assert pkt[0] == 255 and len(pkt) == 1 + 7 * 255


class TestPackets:
    def test_unsplit_is_single_datagram(self):
        pixels = nl.colors_to_pixels([(1, 1, 1)] * 300)
        pkts = nl.encode_packets(nl.LAYOUTS["v2"], pixels)
        assert len(pkts) == 1 and len(pkts[0]) == 2 + 8 * 300

    def test_split_fits_mtu_and_covers_all_ids(self):
        pixels = nl.colors_to_pixels([(9, 8, 7)] * 300)
        pkts = nl.encode_packets(nl.LAYOUTS["v2"], pixels, max_payload=nl.SAFE_UDP_PAYLOAD)
        assert [len(p) for p in pkts] == [2 + 8 * 183, 2 + 8 * 117]
        ids = [rec[0] for p in pkts for rec in nl.decode_frame_v2(p)]
        assert ids == list(range(300))

    def test_default_transition_per_layout(self):
        assert nl.encode_packets(nl.LAYOUTS["v2"], [(0, 1, 2, 3)])[0][-2:] == hx("0000")
        assert nl.encode_packets(nl.LAYOUTS["v1"], [(0, 1, 2, 3)])[0][-1:] == hx("00")


class TestRawHttp:
    """Pairing bypasses http.client, which discards bodies on 204 responses."""

    def test_204_with_body_keeps_token(self):
        raw = (b"HTTP/1.1 204 No Content\r\nContent-Type: application/json\r\n"
               b"Content-Length: 21\r\n\r\n{\"auth_token\": \"abc\"}")
        r = nl.parse_raw_http_response(raw)
        assert r.status == 204 and r.body == {"auth_token": "abc"}

    def test_204_without_length_reads_to_close(self):
        r = nl.parse_raw_http_response(b"HTTP/1.0 204 No Content\r\n\r\n{\"auth_token\":\"x\"}")
        assert r.body == {"auth_token": "x"}

    def test_chunked_and_403(self):
        r = nl.parse_raw_http_response(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"9\r\n{\"auth_to\r\nb\r\nken\": \"q1\"}\r\n0\r\n\r\n")
        assert r.body == {"auth_token": "q1"}
        r = nl.parse_raw_http_response(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        assert r.status == 403 and r.body is None


class TestPatternsAndPlan:
    def test_head_pattern(self):
        c = probe.pattern_head(6, 0)
        assert c == [(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0), (0, 0, 0), (255, 255, 255)]

    def test_chase_advances_one_per_frame(self):
        c0, c1 = probe.pattern_chase(30, 0), probe.pattern_chase(30, 1)
        lit = lambda c: [i for i, v in enumerate(c) if v != (0, 0, 0)]  # noqa: E731
        assert lit(c0) == list(range(0, 10)) and lit(c1) == list(range(1, 11))

    def test_plan_order_and_split(self):
        plan = probe.build_plan(["v2", "v1"], [5, 10, 20], n_leds=300)
        assert plan[0].pid == "P01" and plan[0].layout == "v2" and plan[0].fps == 10
        assert plan[0].pattern == "head"
        assert any(p.split for p in plan)
        assert [p.pid for p in plan] == [f"P{i:02d}" for i in range(1, len(plan) + 1)]

    def test_plan_no_split_when_frame_fits(self):
        assert not any(p.split for p in probe.build_plan(["v2"], [10], n_leds=50))


# --------------------------------------------------------------------------
# Token store
# --------------------------------------------------------------------------

class TestTokenStore:
    def test_save_creates_0600_file(self, tmp_path):
        path = tmp_path / "cfg" / "nanoleaf.json"
        nl.save_token("10.0.0.5", "abc", str(path), port=16021)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert json.loads(path.read_text()) == {
            "devices": {"10.0.0.5": {"port": 16021, "auth_token": "abc"}}}

    def test_update_keeps_other_hosts_and_lookup(self, tmp_path):
        path = str(tmp_path / "n.json")
        nl.save_token("a", "t1", path)
        assert nl.lookup_device(None, path) == ("a", {"auth_token": "t1"})
        nl.save_token("b", "t2", path)
        nl.save_token("a", "t3", path)
        devs = nl.load_tokens(path)["devices"]
        assert devs["a"]["auth_token"] == "t3" and devs["b"]["auth_token"] == "t2"
        assert nl.lookup_device(None, path) == (None, None)  # ambiguous


# --------------------------------------------------------------------------
# mDNS parsing (canned packet, no network)
# --------------------------------------------------------------------------

def _name(n):
    return b"".join(bytes([len(p)]) + p.encode() for p in n.split(".")) + b"\x00"


def _rr(name_bytes, rtype, rdata):
    return name_bytes + struct.pack(">HHIH", rtype, 0x8001, 120, len(rdata)) + rdata


class TestMdns:
    def test_query_format(self):
        q = nl.build_mdns_query(qid=7)
        assert q[:12] == struct.pack(">HHHHHH", 7, 0, 1, 0, 0, 0)
        assert q[12:] == _name("_nanoleafapi._tcp.local") + struct.pack(">HH", 12, 0x8001)

    def test_parse_response_with_compression(self):
        svc = _name("_nanoleafapi._tcp.local")       # at offset 12
        inst_ptr = b"\x0dPermanent 1A2" + b"\xc0\x0c"  # instance -> pointer to svc
        hdr = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 3)
        pkt = bytearray(hdr)
        pkt += _rr(svc, 12, inst_ptr)
        inst_off = len(pkt) - len(inst_ptr)
        inst_ref = struct.pack(">H", 0xC000 | inst_off)
        host = _name("Nanoleaf-1A2.local")
        pkt += _rr(inst_ref, 33, struct.pack(">HHH", 0, 0, 16021) + host)
        txt = b"".join(bytes([len(s)]) + s for s in (b"md=NL73K3", b"srcvers=4.1.2", b"id=AA:BB"))
        pkt += _rr(inst_ref, 16, txt)
        pkt += _rr(_name("Nanoleaf-1A2.local"), 1, socket.inet_aton("192.168.1.50"))
        recs = nl.parse_mdns_records(bytes(pkt))
        assert [r["type"] for r in recs] == [12, 33, 16, 1]
        (svc_info,) = nl.summarize_services(recs)
        assert svc_info == {
            "instance": "Permanent 1A2._nanoleafapi._tcp.local",
            "host": "Nanoleaf-1A2.local", "address": "192.168.1.50", "port": 16021,
            "txt": {"md": "NL73K3", "srcvers": "4.1.2", "id": "AA:BB"}}


# --------------------------------------------------------------------------
# Mock device (HTTP + UDP on localhost)
# --------------------------------------------------------------------------

INFO = {
    "name": "Nanoleaf Permanent", "serialNo": "S1", "manufacturer": "Nanoleaf",
    "firmwareVersion": "4.1.2", "hardwareVersion": "1.1.0", "model": "NL73K3",
    "state": {"brightness": {"value": 40, "max": 100, "min": 0}, "colorMode": "effect",
              "ct": {"value": 2700, "max": 6500, "min": 1200},
              "hue": {"value": 38, "max": 360, "min": 0}, "on": {"value": False},
              "sat": {"value": 3, "max": 100, "min": 0}},
}


class MockDevice:
    def __init__(self):
        self.requests = []          # (method, path, body)
        self.pair_open = True
        self.pair_status = 200      # doc says 204-with-body, LedFx expects 200
        self.reject_v1 = False
        dev = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=None):
                if body is None:
                    raw = b""
                elif isinstance(body, bytes):
                    raw = body
                else:
                    raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                dev.requests.append((method, self.path, body))
                p = self.path
                if p == "/api/v1/new" and method == "POST":
                    return self._send(dev.pair_status, {"auth_token": TOKEN}) \
                        if dev.pair_open else self._send(403)
                prefix = f"/api/v1/{TOKEN}/"
                if not p.startswith(prefix):
                    return self._send(401)
                sub = p[len(prefix):]
                if method == "GET" and sub == "":
                    return self._send(200, INFO)
                if method == "GET" and sub == "length":
                    return self._send(200, {"numLEDs": 5})
                if method == "GET" and sub == "effects/select":
                    return self._send(200, b'"Northern Lights"')
                if method == "GET" and sub == "panelLayout/layout":
                    return self._send(404)
                if method == "PUT" and sub == "effects":
                    w = (body or {}).get("write", {})
                    if dev.reject_v1 and w.get("extControlVersion") == "v1":
                        return self._send(400)
                    return self._send(204)
                if method == "PUT" and sub == "state":
                    return self._send(204)
                return self._send(404)

            def do_GET(self):
                self._handle("GET")

            def do_PUT(self):
                self._handle("PUT")

            def do_POST(self):
                self._handle("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        self.udp.bind(("127.0.0.1", 0))
        self.udp_port = self.udp.getsockname()[1]

    def datagrams(self):
        self.udp.settimeout(0.2)
        out = []
        try:
            while True:
                out.append(self.udp.recv(65535))
        except socket.timeout:
            return out

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.udp.close()


@pytest.fixture
def device():
    try:
        d = MockDevice()
    except PermissionError as e:
        pytest.skip(f"binding 127.0.0.1 sockets not permitted here ({e}); "
                    "run outside the sandbox to exercise the mock-device tests")
    yield d
    d.close()


def base_args(device, tmp_path, *extra):
    return ["--host", "127.0.0.1", "--port", str(device.port),
            "--token-file", str(tmp_path / "nanoleaf.json"), *extra]


class TestAgainstMockDevice:
    @pytest.mark.parametrize("status", [200, 204])  # doc: "204 No Content" + JSON body
    def test_pair_saves_token_then_reuses_saved_host_and_port(self, device, tmp_path, capsys,
                                                              status):
        device.pair_status = status
        rc = probe.main(base_args(device, tmp_path, "--pair", "--pair-wait", "5"))
        assert rc == 0
        path = tmp_path / "nanoleaf.json"
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        entry = json.loads(path.read_text())["devices"]["127.0.0.1"]
        assert entry == {"port": device.port, "auth_token": TOKEN}
        assert ("POST", "/api/v1/new", None) in device.requests
        assert "Connect to API" in capsys.readouterr().out
        # No --host/--port/--token: everything comes from the saved file.
        assert probe.main(["--token-file", str(path), "--info"]) == 0
        assert ("GET", f"/api/v1/{TOKEN}/length", None) in device.requests

    def test_pair_403_when_window_closed(self, device):
        c = nl.NanoleafClient("127.0.0.1", device.port)
        device.pair_open = False
        assert c.pair().status == 403

    def test_info_parsing(self, device, tmp_path, capsys):
        nl.save_token("127.0.0.1", TOKEN, str(tmp_path / "nanoleaf.json"))
        assert probe.main(base_args(device, tmp_path, "--info")) == 0
        out = capsys.readouterr().out
        assert "NL73K3" in out and "4.1.2" in out
        assert "numLEDs      5" in out
        assert "Northern Lights" in out
        c = nl.NanoleafClient("127.0.0.1", device.port, TOKEN)
        info = nl.parse_info(c.info().body)
        assert info["model"] == "NL73K3" and info["brightness"] == 40
        assert info["color_mode"] == "effect" and info["has_panel_layout"] is False
        assert c.length() == 5

    def test_state_command(self, device, tmp_path):
        rc = probe.main(base_args(device, tmp_path, "--token", TOKEN, "--state", "on",
                                  "--brightness", "70", "--hue", "120", "--sat", "100"))
        assert rc == 0
        assert ("PUT", f"/api/v1/{TOKEN}/state",
                {"on": {"value": True}, "brightness": {"value": 70},
                 "hue": {"value": 120}, "sat": {"value": 100}}) in device.requests

    def test_stream_test_enables_extcontrol_streams_and_restores(self, device, tmp_path):
        rc = probe.main(base_args(device, tmp_path, "--token", TOKEN, "--stream-test",
                                  "--layouts", "v2", "--fps", "10", "--phase-scale", "0.1",
                                  "--gap", "0.05", "--udp-port", str(device.udp_port)))
        assert rc == 0
        puts = [(p, b) for m, p, b in device.requests if m == "PUT"]
        eff = f"/api/v1/{TOKEN}/effects"
        assert (eff, {"write": {"command": "display", "animType": "extControl",
                                "extControlVersion": "v2"}}) in puts
        # on+brightness before extControl, restore (select effect, brightness, off) after
        idx_ext = puts.index((eff, nl.ext_control_body("v2")))
        assert [b for _, b in puts[:idx_ext]] == [{"on": {"value": True}},
                                                  {"brightness": {"value": 60}}]
        assert puts[idx_ext + 1:] == [
            (eff, {"select": "Northern Lights"}),
            (f"/api/v1/{TOKEN}/state", {"brightness": {"value": 40}}),
            (f"/api/v1/{TOKEN}/state", {"on": {"value": False}}),
        ]
        grams = device.datagrams()
        blank = nl.encode_frame_v2([(i, 0, 0, 0) for i in range(5)])
        head = nl.encode_frame_v2(nl.colors_to_pixels(probe.pattern_head(5, 0)))
        assert grams[0] == blank
        assert head in grams
        assert grams[grams.index(head)] == hx(
            "0005" "0000 FF0000 00 0000" "0001 00FF00 00 0000" "0002 0000FF 00 0000"
            "0003 000000 00 0000" "0004 FFFFFF 00 0000")

    def test_stream_test_skips_rejected_layout(self, device, tmp_path, capsys):
        device.reject_v1 = True
        rc = probe.main(base_args(device, tmp_path, "--token", TOKEN, "--stream-test",
                                  "--layouts", "v1", "--fps", "10", "--phase-scale", "0.1",
                                  "--gap", "0.05", "--udp-port", str(device.udp_port)))
        assert rc == 0
        out = capsys.readouterr().out
        assert "SKIPPED (extControl v1 HTTP 400)" in out
        assert device.datagrams() == []


class TestDryRun:
    def test_dry_run_prints_hex_without_network(self, capsys):
        rc = probe.main(["--stream-test", "--dry-run", "--leds", "3", "--layouts", "v2",
                         "--fps", "10", "--phase-scale", "0.1", "--hex-full"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "DRY-RUN HTTP PUT /effects" in out and '"extControlVersion": "v2"' in out
        head = nl.encode_frame_v2(nl.colors_to_pixels(probe.pattern_head(3, 0)))
        assert head.hex(" ") in out
