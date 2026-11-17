"""Postshow: random twinkle that keeps about half the lights on.

Steve's production postshow, carried over from the original LightShowPi
install. Configure it with:

    postshow_script = $SYNCHRONIZED_LIGHTS_HOME/py/examples/random_pattern_ci.py

`hc` is injected by prepostshow.start_script(). Channel numbers are show
channels (indexes into gpio_pins).
"""

import time
import random

# Channels that take turns being on (only one at a time)
SPECIALS = [1, 2]
# Channels that are never turned off / never turned on
NEVER_OFF = [0, 6]
NEVER_ON = []
# Run time in seconds
RUN_SECONDS = 1800


def main(exit_event):
    """Random flashing lights until RUN_SECONDS pass or the show interrupts."""
    gpio_pins = hc.cm.hardware.gpio_pins
    # Per light: [level, time it may turn back on]
    lights = {light: [1.0, time.time()] for light in range(len(gpio_pins))}

    # At most about half the lights off at once
    max_off = int(round(len(lights) * .5))

    # Start with all the lights on
    for light in lights:
        if light not in NEVER_ON:
            lights[light][0] = 1.0
            hc.turn_on_light(light)

    # Only one of the specials on at a time
    for special in SPECIALS[1:]:
        hc.turn_off_light(special)

    end = time.time() + RUN_SECONDS

    while time.time() < end:
        # Turn lights back on once their off time has passed
        for light in lights:
            if not lights[light][0] > 0 and light not in NEVER_ON:
                if lights[light][1] < time.time():
                    lights[light][0] = 1.0
                    hc.turn_on_light(light)

        off = [k for (k, v) in lights.items() if not v[0] > 0]

        # Below the limit: turn a random light off for a while
        if len(off) < max_off:
            choice = random.randrange(0, len(gpio_pins))
            if lights[choice][0] > 0:
                if choice in SPECIALS:
                    # Hand over to the next special in 1 second
                    lights[choice] = [0, end]
                    nxt = SPECIALS[(SPECIALS.index(choice) + 1) % len(SPECIALS)]
                    lights[nxt] = [0, time.time() + 1]
                else:
                    lights[choice] = [0, time.time() + random.uniform(1, 5)]

                if choice not in NEVER_OFF:
                    hc.turn_off_light(choice)

        # Let a play-now command or the show stop end the script
        if exit_event.is_set():
            break

        time.sleep(0.01)

    hc.turn_off_lights()
