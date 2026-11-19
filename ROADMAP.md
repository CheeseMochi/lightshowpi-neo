# lightshowpi-neo Development Roadmap

This file documents the development roadmap and priorities for lightshowpi-neo.

## Key Initiatives

These are the near term priorities for lightshowpi-neo

### Automated Test Suite

The test suite has been upgraded and improved in this repo, but there are a few additions that are needed:

- Automated tests that run on every github PR
- Automated test harness for running the full test suite on Raspberry Pi and exercising the GPIOs

### Web Portal

A web portal for monitoring, controlling, and configuring lightshowpi-neo. The key requirements are:

- Can be hosted locally or in the cloud (with automated deployment scripts)
- Secure remote access for administrators
- Web portal drives configuration of new nodes

### Robust Multicolor LED support

When forked from lightshowpi, LED support was mostly experimental. LED support should be feature-complete

- Additional pattern generators for LEDs
- Automated configuration and zone assigment
- Support for additional LED array shapes

### Support for Additional Lighting APIs

Many light ecosystems enable their own APIs for controlling their lights, which lightshowpi-neo should be able to connect to.

- Modular output plugin interface to add more ecosystems in the future
- Support for Nanoleaf API

### Light Algorithm Research

Today the algorithm for creating shows is purely FFT based and separates channels by frequency bands. The goal is for this library to create more impactful shows that trigger on more than just frequency spikes in the music.

- TBD

## After the 2026 Season

Ideas noted during the November 2026 sprint, to decide on after the holidays.

### Move sACN onto the `sacn` library

Pi-to-Pi sACN (`py/networking_sacn.py`) and the LED sACN driver (`py/driver_sacn.py`, `py/e131packet.py`) use in-house E1.31 code. It works for our own server and client but skips parts of the spec: sync packets, priority, stream termination and idle keepalive. Switching to the [`sacn`](https://pypi.org/project/sacn/) library (MIT, 1.11.0 as of Feb 2025) would make the show interoperable with xLights and other sACN tools.

- Its sender and receiver run their own threads with callbacks, so the client loop in `hardware_controller.receive_and_process` needs reworking, not just a swap
- Not on conda-forge: install it with pip inside the conda env
- Validate on the Pi 3 / Zero 2 W class hardware before switching production
- LED strips: `driver_sacn.SacnSender.send()` is the one seam to swap. Use `manual_flush=True` and call `flush()` once per frame so show timing, not the library's own 30 fps thread, decides when packets go out. Set its universe size to match `universe_boundary` (it caps universes at 512 channels)

### Finish removing BiblioPixel

LED strips over sACN no longer need BiblioPixel (Nov 2026: `py/led_module.py`, `py/led_colors.py`). MATRIX layouts and the SPI/SERIAL connections still go through `py/led_bibliopixel.py`, which can't run on Neo's Python. Port the matrix patterns (SBARS, MBARS, IMAGE, PINWHEEL, CBARS, CIRCLES, BANNER) to the native `PixelStrip` with a coordinate map, Pillow for GIF frames and a small bitmap font; then decide whether SPI/SERIAL are worth keeping.
