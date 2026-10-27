"""MAX6675 reader: VCC=3V3, GND=GND, SCK=18, CS=23, SO=19.

Common five-pin breakout assumed; actual module silk-screen not checked yet.
No MOSI is needed. Each CS-high conversion gets at least 250 ms.
"""

import time


def decode(raw):
    if raw & 0x04:
        raise OSError('MAX6675 thermocouple open or invalid SO data')
    if raw & 0x8002:
        raise OSError('MAX6675 invalid fixed bits; check wiring')
    return ((raw >> 3) & 0x0FFF) * 0.25


class MAX6675:
    def __init__(self, sck=18, cs=23, so=19):
        from machine import Pin
        self.cs = Pin(cs, Pin.OUT, value=1)
        self.sck = Pin(sck, Pin.OUT, value=0)
        self.so = Pin(so, Pin.IN)
        self.conversion_started = time.ticks_ms()

    def read(self):
        elapsed = time.ticks_diff(time.ticks_ms(), self.conversion_started)
        if elapsed < 250:
            time.sleep_ms(250 - elapsed)
        raw = 0
        self.cs.value(0)
        try:
            time.sleep_us(10)
            for _ in range(16):
                self.sck.value(0)
                time.sleep_us(5)
                raw = (raw << 1) | self.so.value()
                self.sck.value(1)
                time.sleep_us(5)
        finally:
            self.cs.value(1)
            self.sck.value(0)
            self.conversion_started = time.ticks_ms()
        return decode(raw)
