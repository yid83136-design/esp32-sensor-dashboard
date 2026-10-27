"""Board 1 comparison: SHT3x (S) versus DHT11 (D).

OLED SDA21/SCL22; SHT3x SDA25/SCL26; DHT11 S=GPIO27.
All modules powered from 3V3 with common GND.
Startup entry is selected by main.py after wiring and live reading checks.
"""

import time
from oled_sht3x_monitor import read_sensor, limits

DHT_PIN = 27
SAMPLE_MS = 2000
POINTS = 64
PAGE_MS = 8000
VERSION = 'dual-sht3x-dht11-v1'


def trim_append(history, value):
    history.append(value)
    if len(history) > POINTS:
        history.pop(0)


def readings_row(label, value):
    if value is None:
        return label + '  --C    --%'
    return '%s%5.1fC %5.1f%%' % (label, value[0], value[1])


def numeric_page(oled, current_s, current_d):
    oled.text('S SHT3x D DHT11', 0, 0)
    oled.text(readings_row('S', current_s), 0, 12)
    oled.text(readings_row('D', current_d), 0, 24)
    if current_s is None or current_d is None:
        oled.text('dT(S-D) --C', 0, 40)
        oled.text('dH(S-D) --pp', 0, 52)
    else:
        oled.text('dT(S-D)%+.1fC' % (current_s[0] - current_d[0]), 0, 40)
        oled.text('dH(S-D)%+.1fpp' % (current_s[1] - current_d[1]), 0, 52)


def trace(oled, history, index, bounds, dotted):
    low, high = bounds
    previous = None
    for i, pair in enumerate(history):
        if pair is None:
            previous = None
            continue
        x = i * 2
        fraction = max(0.0, min(1.0, (pair[index] - low) / (high - low)))
        y = 63 - int(fraction * 33 + 0.5)
        if dotted:
            # One unconnected dot per sample; same axes as the solid S trace.
            oled.pixel(x, y, 1)
        elif previous is None:
            oled.pixel(x, y, 1)
        else:
            oled.line(previous[0], previous[1], x, y, 1)
        previous = (x, y)


def curve_page(oled, history_s, history_d, current_s, current_d, index):
    def value_label(value):
        return '--' if value is None else '%.1f' % value[index]

    oled.text('S%s D%s' % (value_label(current_s), value_label(current_d)), 0, 0)
    # Both sensors use exactly the same axis range on each comparison page.
    bounds = limits(history_s + history_d, index)
    name, unit = ('TEMP', 'C') if index == 0 else ('HUM', '%')
    range_text = '--' if bounds is None else '%.1f' % (bounds[1] - bounds[0])
    oled.text('%s %s R%s' % (name, unit, range_text), 0, 10)
    oled.text('S:line D:dots', 0, 20)
    if bounds is not None:
        trace(oled, history_s, index, bounds, False)
        trace(oled, history_d, index, bounds, True)


def draw(oled, page, history_s, history_d, current_s, current_d):
    oled.fill(0)
    if page == 0:
        numeric_page(oled, current_s, current_d)
    else:
        curve_page(oled, history_s, history_d, current_s, current_d, page - 1)
    oled.show()


def main():
    from machine import Pin, SoftI2C
    import ssd1306
    import dht

    oled_bus = SoftI2C(sda=Pin(21), scl=Pin(22), freq=100000)
    sensor_bus = SoftI2C(sda=Pin(25), scl=Pin(26), freq=100000)
    addresses = oled_bus.scan()
    oled_address = next((a for a in addresses if a in (0x3C, 0x3D)), None)
    if oled_address is None:
        raise OSError('OLED missing on SDA21/SCL22')
    oled = ssd1306.SSD1306_I2C(128, 64, oled_bus, addr=oled_address)
    sensor_d = dht.DHT11(Pin(DHT_PIN))
    sensor_s_address = None
    history_s, history_d = [], []
    print(VERSION, 'DHT11 signal GPIO27, period', SAMPLE_MS, 'ms')
    time.sleep_ms(2000)  # Allow the added module to settle after power-up.
    page, page_started = 0, time.ticks_ms()
    print('Comparison page', page)
    while True:
        started = time.ticks_ms()
        current_s, current_d = None, None
        try:
            if sensor_s_address is None:
                addresses = sensor_bus.scan()
                sensor_s_address = next((a for a in addresses if a in (0x44, 0x45)), None)
                if sensor_s_address is None:
                    raise OSError('SHT3x missing')
            current_s = read_sensor(sensor_bus, sensor_s_address)
        except (OSError, ValueError) as error:
            sensor_s_address = None
            print('SHT3x error:', error)
        try:
            sensor_d.measure()
            measured = (sensor_d.temperature(), sensor_d.humidity())
            if not 0 <= measured[0] <= 50 or not 0 <= measured[1] <= 100:
                raise ValueError('DHT11 result outside temperature/humidity bounds')
            current_d = measured
        except (OSError, ValueError) as error:
            print('DHT11 error:', error)
        trim_append(history_s, current_s)
        trim_append(history_d, current_d)
        print(readings_row('S', current_s), '|', readings_row('D', current_d))
        if current_s is not None and current_d is not None:
            print('DELTA S-D: T=%.2f C, RH=%.2f percentage points' %
                  (current_s[0] - current_d[0], current_s[1] - current_d[1]))
        if time.ticks_diff(started, page_started) >= PAGE_MS:
            page = (page + 1) % 3
            page_started = started
            print('Comparison page', page)
        try:
            draw(oled, page, history_s, history_d, current_s, current_d)
        except OSError as error:
            print('OLED error:', error)
        elapsed = time.ticks_diff(time.ticks_ms(), started)
        missed = elapsed // SAMPLE_MS
        for _ in range(min(missed, POINTS)):
            trim_append(history_s, None)
            trim_append(history_d, None)
        time.sleep_ms(SAMPLE_MS - elapsed % SAMPLE_MS)


if __name__ == '__main__':
    main()
