# sACN Pi-to-Pi bench test (Thu Nov 19)

**Goal:** prove `sacn_server` → `sacn_client` on the bench well enough to decide on Mon Nov 23
whether production switches from legacy `server`/`client` to sACN (decision D9). About 45 minutes.

| Role | Host | Notes |
|------|------|-------|
| Server | `pibench-server` (Pi 5) | No 3.5 mm jack: audio goes to the ALSA loopback (`snd-aloop`), which also paces playback |
| Client | `pibench-client` (Zero 2 W) | Wi-Fi, like the production client |

Everything runs as the `ghrunner` user (in the `gpio` and `audio` groups) from its checkout at
`/home/ghrunner/lightshowpi`. No sudo is needed except for `modprobe` and the Wi-Fi drop.

> **GPIO jumpers:** the loopback jumpers on `pibench-server` (physical 11→13, 15→16) join WiringPi
> pins 0↔2 and 3↔4. The default `gpio_pins = 0..7` would drive both ends of each jumper as outputs
> against each other. The server config below leaves out WiringPi 2 and 4 (the input side). Don't
> run the show on `pibench-server` with the default pins while the jumpers are on.

> **Timing:** the scheduled Pi bench run starts at 14:00 Central, and a merge to `main` also starts
> one. Don't run this procedure while a bench run is in progress, because both use the same Pis.

## 0. Update both bench Pis (from the Mac)

The monitor tool is on branch `sacn-bench`. Merge it first, wait for the bench run that the merge
starts (about 5 min), then:

```bash
make deploy HOST=bench
```

## 1. Server config (`pibench-server`)

```bash
ssh pibench-server.local
sudo modprobe snd-aloop                       # loopback sound card (until reboot)
echo snd-aloop | sudo tee /etc/modules-load.d/snd-aloop.conf   # optional: keep it after reboot
hostname -I                                    # note the server IP
sudo -iu ghrunner
cd ~/lightshowpi && git log --oneline -1       # must show the merged sacn-bench commit
export SYNCHRONIZED_LIGHTS_HOME=$PWD
source ~/miniforge3/bin/activate lightshowpi-neo
aplay -L | grep -i '^plughw:CARD=Loopback,DEV=0'   # must print one line
cp config/overrides.cfg config/overrides.cfg.before-sacn
mkdir -p logs && mv -f logs/music_and_lights.play.dbg logs/music_and_lights.play.dbg.old 2>/dev/null; true
```

Get the client IP (`ssh pibench-client.local hostname -I`), then append this to
`config/overrides.cfg` (if a section already exists there, merge the keys into it instead):

```ini
[hardware]
# 7 channels like production; skips WiringPi 2 and 4 (jumpered inputs)
gpio_pins = 0,1,3,5,6,7,21

[lightshow]
audio_out_card = plughw:CARD=Loopback,DEV=0

[network]
networking = sacn_server
sacn_address = <CLIENT_IP>
```

## 2. Client config (`pibench-client`)

```bash
ssh pibench-client.local
sudo -iu ghrunner
cd ~/lightshowpi && git log --oneline -1       # same commit as the server
export SYNCHRONIZED_LIGHTS_HOME=$PWD
source ~/miniforge3/bin/activate lightshowpi-neo
cp config/overrides.cfg config/overrides.cfg.before-sacn
mkdir -p logs && mv -f logs/music_and_lights.play.dbg logs/music_and_lights.play.dbg.old 2>/dev/null; true
```

Append this to `config/overrides.cfg` (the same mapping as the production client):

```ini
[network]
networking = sacn_client
channels = 0,1,2,3,4,5,6,6
get_overrides = true
```

## 3. Packets arrive (monitor, no client process yet)

Server terminal. `synchronized_lights.py` plays one song per run and `bin/start_music_and_lights`
needs sudo, so loop it by hand (the sample playlist, 20 songs; press Ctrl-C a few times to stop):

```bash
for i in $(seq 20); do python py/synchronized_lights.py --playlist=music/sample/.playlist || sleep 5; done
```

Client terminal:

```bash
python3 tools/sacn_monitor.py --seconds 60 --universe 100 --min-fps 8
```

The server sends one frame per audio chunk, about 10.8 per second with the default
`chunk_size = 4096` (production's 2048 gives about 21.5). Seconds with no packets while the server
moves to the next song count as idle, not as failures.

**Pass when:**
- each line shows `universe   100` from the server IP, and the levels change with the music
- the output ends with `PASS` (at least 10 seconds with packets, median at least 8 per second, under 1% missed)

Stop the monitor before step 4. It and the client both listen on port 5568, and unicast packets go
to only one of them.

## 4. Client plays along

Leave the server playing. On the client:

```bash
python py/synchronized_lights.py
```

In a second client SSH session: `tail -f ~/lightshowpi/logs/music_and_lights.play.dbg`

**Pass when, over two full songs:**
- the client log shows `sACN client mode starting` and `Client channels mapped as`
- no `Traceback` or `ERROR` lines on either Pi
  (server: `grep -nE 'Traceback|ERROR' ~/lightshowpi/logs/music_and_lights.play.dbg`)
- if a relay or LED board is attached to the client, it flickers in time with the music
- the server log shows `sACN server mode - unicast to <CLIENT_IP>:5568`
  (and `Sent overrides` lines, if a song has per-song overrides)

## 5. The client drops off Wi-Fi

With both still running, from a **separate** SSH session on the client (your session will freeze
for 30 s; the command keeps running):

```bash
sudo systemd-run --unit=wifidrop sh -c 'ip link set wlan0 down; sleep 30; ip link set wlan0 up'
```

**Pass when:**
- the server keeps playing: songs change on time and its log has no `Traceback`
  (`sACN send to <CLIENT_IP> failed` errors during the drop are fine; note how many)
- after Wi-Fi returns, `pgrep -af synchronized_lights` on the client still shows the client process
  and its log has no `Traceback`
- then stop the client (Ctrl-C), run `python3 tools/sacn_monitor.py --seconds 30 --min-fps 8`, and
  it ends with `PASS`

## 6. Optional: 1-hour soak

Server looping the playlist (step 3's command; use `$(seq 100)` for a longer run), client running
as in step 4. First set the logs aside on both Pis so step 5's expected send errors don't count:
`mv logs/music_and_lights.play.dbg logs/music_and_lights.play.dbg.step5`. After an hour:

```bash
grep -cE 'Traceback|ERROR' ~/lightshowpi/logs/music_and_lights.play.dbg   # both Pis: expect 0
free -m; uptime                                                           # no swap growth, load < 2
```

## 7. Put the bench back

Ctrl-C both processes, then on each Pi:

```bash
cp config/overrides.cfg.before-sacn config/overrides.cfg
```

## Report back

For each step, send pass or fail, the monitor's last 5 lines from steps 3 and 5, and any
`Traceback` with its 20 lines of context. **The Mon 23 go/no-go** is:
- **Go** if steps 3–5 pass
- **No-go** (production stays on legacy `server`/`client`) if any of them fails and the fix isn't
  small
