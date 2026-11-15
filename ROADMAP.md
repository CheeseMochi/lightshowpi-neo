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
