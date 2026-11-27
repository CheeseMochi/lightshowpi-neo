"""LedOutput: wraps a led_module.Led (or its multiprocess proxy) as an Output."""
from outputs.base import Output


class LedOutput(Output):
    """Drive one LED config from show frames.

    Which slice of the show's channels the LEDs follow depends on the LED
    config's ``led_channel_configuration``:

    * ``EXTEND``: LEDs take the channels after the physical GPIO channels, up
      to ``extend_end`` when set (Hardware sets it to the first virtual
      channel when ``[hardware] virtual_channels`` > 0, so strips never see
      virtual columns; None = to the end of the frame, as before)
    * anything else (``MIRROR``, ``LEDONLY``): the first ``gpio_len`` channels

    Network clients receive the server's whole array and pass it through
    unsliced (``slice_levels=False``), matching v0.9.x behaviour.
    """

    def __init__(self, led, channel_configuration, physical_gpio_len, gpio_len,
                 slice_levels=True, name="led"):
        self.led = led
        self.channel_configuration = (channel_configuration or "").upper()
        self.physical_gpio_len = physical_gpio_len
        self.gpio_len = gpio_len
        self.slice_levels = slice_levels
        self.name = name
        self.extend_end = None

    def select(self, levels):
        """Return the part of a show frame this LED config displays."""
        if not self.slice_levels:
            return levels
        if self.channel_configuration == "EXTEND":
            return levels[self.physical_gpio_len:self.extend_end]
        return levels[:self.gpio_len]

    def write_frame(self, levels):
        self.led.write_all(self.select(levels))

    def all_on(self):
        self.led.all_set_on = True
        self.led.all_leds_on()

    def all_off(self):
        self.led.all_set_on = False
        self.led.all_leds_off()
