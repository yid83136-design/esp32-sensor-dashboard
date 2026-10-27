"""OLED pages for board 1: SHT3x air temperature/humidity and MAX6675 probe."""


def label(value, digits=1):
    return '--' if value is None else ('%.' + str(digits) + 'f') % value


def numeric_page(oled, current_s, current_k):
    oled.text('SHT3x + MAX6675', 0, 0)
    oled.text('AIR ' + label(None if current_s is None else current_s[0]) + ' C', 0, 14)
    oled.text('RH  ' + label(None if current_s is None else current_s[1]) + ' %', 0, 26)
    oled.text('PROBE ' + label(current_k, 2) + ' C', 0, 38)
    difference = None if current_s is None or current_k is None else current_k - current_s[0]
    oled.text('K-S ' + ('--' if difference is None else '%+.2f' % difference) + ' C', 0, 52)


def plot_page(oled, title, history, current, index, unit, minimum_span):
    value = None if current is None else (current if index is None else current[index])
    oled.text(title + ' ' + label(value, 2 if index is None else 1) + unit, 0, 0)
    values = [item if index is None else item[index] for item in history if item is not None]
    if not values:
        oled.text('WAITING FOR DATA', 0, 18)
        return
    low, high = min(values), max(values)
    span = max(minimum_span, (high - low) * 1.25)
    mid = (high + low) / 2
    low, high = mid - span / 2, mid + span / 2
    oled.text('HI ' + label(high, 1) + ' LO ' + label(low, 1), 0, 14)
    oled.hline(0, 27, 128, 1)
    previous = None
    for i, item in enumerate(history):
        if item is None:
            previous = None
            continue
        measured = item if index is None else item[index]
        x = i * 2
        fraction = max(0.0, min(1.0, (measured - low) / span))
        y = 63 - int(fraction * 33 + 0.5)
        if previous is None:
            oled.pixel(x, y, 1)
        else:
            oled.line(previous[0], previous[1], x, y, 1)
        previous = (x, y)


def draw(oled, page, histories, current_s, current_k):
    oled.fill(0)
    if page == 0:
        numeric_page(oled, current_s, current_k)
    elif page == 1:
        plot_page(oled, 'AIR TEMP', histories[0], current_s, 0, 'C', 1.0)
    elif page == 2:
        plot_page(oled, 'AIR RH', histories[0], current_s, 1, '%', 5.0)
    else:
        plot_page(oled, 'PROBE', histories[1], current_k, None, 'C', 1.0)
    oled.show()
