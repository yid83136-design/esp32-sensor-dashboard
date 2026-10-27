"""Board 1: SSD1306 128x64 + SHT3x, independent 3.3V I2C buses.

OLED: VCC=3V3, GND=GND, SDA=21, SCL=22.
SHT3x: VCC=3V3, GND=GND, SDA=25, SCL=26.
Run temporarily with mpremote run, or start through main.py.
No Wi-Fi, generated measurements, or flash logging.
"""

import time

SAMPLE_MS = 1000
POINTS = 64
X_STEP = 2
LAYOUT_VERSION = 'clean-curves-v2'
OLED_SDA, OLED_SCL = 21, 22
SENSOR_SDA, SENSOR_SCL = 25, 26


def crc8(data):
    value = 0xFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (((value << 1) ^ 0x31) if value & 0x80 else value << 1) & 0xFF
    return value


def read_sensor(bus, address):
    bus.writeto(address, b'\x24\x00')
    time.sleep_ms(20)
    data = bus.readfrom(address, 6)
    if len(data) != 6:
        raise ValueError('SHT3x short read')
    if crc8(data[:2]) != data[2] or crc8(data[3:5]) != data[5]:
        raise ValueError('SHT3x CRC mismatch')
    raw_t = (data[0] << 8) | data[1]
    raw_h = (data[3] << 8) | data[4]
    temperature = -45 + 175 * raw_t / 65535
    humidity = 100 * raw_h / 65535
    if not -40 <= temperature <= 125 or not 0 <= humidity <= 100:
        raise ValueError('SHT3x out of specified range')
    return temperature, humidity


def limits(history, index):
    values = [pair[index] for pair in history if pair is not None]
    if not values:
        return None
    low, high = min(values), max(values)
    minimum_span = 1.0 if index == 0 else 5.0
    span = max(minimum_span, (high - low) * 1.2)
    center = (low + high) / 2
    domain_low, domain_high = (-40.0, 125.0) if index == 0 else (0.0, 100.0)
    bottom = max(domain_low, min(center - span / 2, domain_high - span))
    top = min(domain_high, bottom + span)
    return bottom, top


def graph(oled, history, current, index, label_y, plot_y):
    name, unit = ('T', 'C') if index == 0 else ('H', '%')
    value_label = name + '--' + unit if current is None else '%s%.1f%s' % (name, current[index], unit)
    oled.text(value_label, 0, label_y)
    bounds = limits(history, index)
    if bounds is None:
        return
    low, high = bounds
    # R is the total vertical range, not measurement precision. Each chart has
    # its own scale. A compact label keeps 23 pixel rows free for the trace.
    range_label = 'R%.1f%s' % (high - low, unit)
    oled.text(range_label, 128 - 8 * len(range_label), label_y)
    previous = None
    for offset, pair in enumerate(history):
        if pair is None:
            previous = None  # Missing samples remain gaps.
            continue
        # Fill left-to-right during startup; once history is full, dropping
        # the oldest sample scrolls the trace left by two pixels per second.
        x = offset * X_STEP
        fraction = (pair[index] - low) / (high - low)
        y = plot_y + 22 - int(max(0.0, min(1.0, fraction)) * 22 + 0.5)
        if previous is None:
            oled.pixel(x, y, 1)
        else:
            oled.line(previous[0], previous[1], x, y, 1)
        previous = (x, y)
    # A 2x2 head makes the newest reading visible even with one sample.
    if history and history[-1] is not None and previous is not None:
        x, y = previous
        other_y = y + 1 if y < plot_y + 22 else y - 1
        oled.pixel(x + 1, y, 1)
        oled.pixel(x, other_y, 1)
        oled.pixel(x + 1, other_y, 1)


def draw(oled, history, current):
    oled.fill(0)
    graph(oled, history, current, 0, 0, 9)
    graph(oled, history, current, 1, 32, 41)
    oled.show()


def main():
    from machine import Pin, SoftI2C
    import ssd1306

    oled_bus = SoftI2C(sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=100000)
    sensor_bus = SoftI2C(sda=Pin(SENSOR_SDA), scl=Pin(SENSOR_SCL), freq=100000)
    addresses = oled_bus.scan()
    print('OLED bus GPIO21/22:', [hex(a) for a in addresses])
    oled_address = next((a for a in addresses if a in (0x3C, 0x3D)), None)
    if oled_address is None:
        raise RuntimeError('OLED missing: check 3V3/GND/SDA21/SCL22')
    oled = ssd1306.SSD1306_I2C(128, 64, oled_bus, addr=oled_address)
    history = []
    sensor_address = None
    print('Layout', LAYOUT_VERSION, 'points', POINTS, 'x-step', X_STEP)
    print('Sampling SHT3x on SDA25/SCL26 every', SAMPLE_MS, 'ms')
    while True:
        started = time.ticks_ms()
        current = None
        try:
            if sensor_address is None:
                addresses = sensor_bus.scan()
                print('Sensor bus GPIO25/26:', [hex(a) for a in addresses])
                sensor_address = next((a for a in addresses if a in (0x44, 0x45)), None)
                if sensor_address is None:
                    raise OSError('SHT3x missing: check 3V3/GND/SDA25/SCL26')
            current = read_sensor(sensor_bus, sensor_address)
            print('T=%.2f C, RH=%.2f %%' % current)
        except (OSError, ValueError) as error:
            sensor_address = None
            print('Sensor error:', error)
        history.append(current)
        if len(history) > POINTS:
            history.pop(0)
        try:
            draw(oled, history, current)
        except OSError as error:
            print('OLED communication error:', error)
        elapsed = time.ticks_diff(time.ticks_ms(), started)
        # If a bus operation stalls, reserve blank slots for the missed times.
        missed = elapsed // SAMPLE_MS
        if missed:
            history.extend([None] * min(missed, POINTS))
            history[:] = history[-POINTS:]
        time.sleep_ms(SAMPLE_MS - elapsed % SAMPLE_MS)


if __name__ == '__main__':
    main()
