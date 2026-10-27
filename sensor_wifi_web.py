"""Local Wi-Fi dashboard for board 1. AP fallback; optional saved STA.

Only explicitly configured network names are tried. No password guessing or
automatic attempts against unrelated nearby access points. All measurements
come from SHT3x and MAX6675; HTML is served from local flash.
"""

import time
import os
import gc
import json
import binascii
import asyncio

AP_SSID = 'ESP32-Sensors-1'
AP_PASSWORD = '88888888'
AP_IP = '192.168.4.1'
SETTINGS_FILE = 'sensor_wifi_settings.json'
HTML_FILE = 'sensor_dashboard.html'
JAVASCRIPT_FILE = 'sensor_dashboard.js'
HOSTNAME = 'esp32-sensors'
MDNS_NAME = HOSTNAME + '.local'
MDNS_ADDR = '224.0.0.251'
MDNS_PORT = 5353
VERSION = 'sensor-wifi-web-v4'
SAMPLE_MS = 2000
MAX_ROWS = 150
MAX_CLIENTS = 4
AP_HANDOFF_MS = 15000
RETRY_WAIT_MS = 10000
STATUS_EVERY_MS = 20000


def ticks_ms():
    return time.ticks_ms() if hasattr(time, 'ticks_ms') else int(time.monotonic() * 1000)


def ticks_diff(a, b):
    return time.ticks_diff(a, b) if hasattr(time, 'ticks_diff') else a - b


def ticks_add(a, delta):
    return time.ticks_add(a, delta) if hasattr(time, 'ticks_add') else a + delta


def rounded(value):
    return None if value is None else round(value, 2)


def free_bytes():
    """MicroPython reports free heap as gc.mem_free(); CPython host checks lack it."""
    try:
        return gc.mem_free()
    except AttributeError:
        return None


def mdns_answer(name, ip_bytes):
    labels = b''.join(bytes((len(part),)) + part for part in name.split(b'.') if part)
    header = b'\x00\x00\x84\x00\x00\x00\x00\x01\x00\x00\x00\x00'
    body = (labels + b'\x00' + b'\x00\x01' + b'\x80\x01' +
            b'\x00\x00\x00\x78' + b'\x00\x04' + ip_bytes)
    return header + body


def mdns_query_name(packet):
    if len(packet) < 13:
        return None
    index = 12
    labels = []
    while index < len(packet):
        length = packet[index]
        if length == 0 or length & 0xC0:
            break
        start = index + 1
        labels.append(packet[start:start + length])
        index = start + length
    return b'.'.join(labels).lower() if labels else None


class Monitor:
    def __init__(self, settings_file=SETTINGS_FILE, html_file=HTML_FILE,
                 javascript_file=JAVASCRIPT_FILE):
        self.started = ticks_ms()
        self.boot = binascii.hexlify(os.urandom(4)).decode()
        self.rows = []
        self.sequence = 0
        self.sample_at = None
        self.errors = {}
        self.ap = None
        self.sta = None
        self.active_clients = 0
        self.request_count = 0
        self.revision = 0
        self.handoff_deadline = None
        self.handoff_pending = False
        self.task_errors = {}
        self.settings_file = settings_file
        self.html_file = html_file
        self.javascript_file = javascript_file
        self.demo = False
        self.net = {'ap_ssid': AP_SSID, 'ap_ip': AP_IP, 'ap_on': True,
                    'hostname': MDNS_NAME, 'sta_state': 'hotspot_only',
                    'sta_ssid': '', 'sta_ip': ''}
        self.networks = self.load_settings()

    def uptime(self):
        return ticks_diff(ticks_ms(), self.started) // 1000

    def remaining_seconds(self, deadline):
        if deadline is None:
            return 0
        remaining = ticks_diff(deadline, ticks_ms())
        return max(0, (remaining + 999) // 1000)

    async def supervise(self, name, factory):
        while True:
            try:
                await factory()
            except Exception as error:
                self.task_errors[name] = str(error)
                print('TASK_RESTART', name, repr(error))
            else:
                self.task_errors[name] = 'Task stopped unexpectedly'
                print('TASK_RESTART', name, 'stopped')
            await asyncio.sleep(2)

    def load_settings(self):
        try:
            with open(self.settings_file) as stream:
                data = json.load(stream)
            return self.validate_networks(data.get('networks', []))
        except OSError:
            return []
        except (ValueError, TypeError, AttributeError):
            print('Saved Wi-Fi settings invalid; retaining local hotspot')
            return []

    @staticmethod
    def validate_networks(networks):
        if not isinstance(networks, list) or len(networks) > 4:
            raise ValueError('At most four configured Wi-Fi networks are supported')
        checked = []
        for item in networks:
            if not isinstance(item, dict):
                raise ValueError('Invalid network item')
            ssid, password = item.get('ssid'), item.get('password')
            if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32:
                raise ValueError('Wi-Fi name must contain 1-32 UTF-8 bytes')
            if not isinstance(password, str) or not 8 <= len(password.encode()) <= 63:
                raise ValueError('Wi-Fi password must contain 8-63 UTF-8 bytes')
            if ssid == AP_SSID:
                raise ValueError('Choose your own router or phone hotspot, not this device')
            checked.append({'ssid': ssid, 'password': password})
        return checked

    def update_settings(self, data):
        if not isinstance(data, dict):
            raise ValueError('Expected a JSON object')
        if data.get('hotspot_only') is True:
            networks = []
        else:
            new_item = self.validate_networks([{'ssid': data.get('ssid'),
                                               'password': data.get('password')}])[0]
            networks = [new_item] + [item for item in self.networks if item['ssid'] != new_item['ssid']]
            networks = networks[:4]
        temporary = self.settings_file + '.tmp'
        with open(temporary, 'w') as stream:
            json.dump({'networks': networks}, stream)
        if hasattr(os, 'replace'):
            os.replace(temporary, self.settings_file)
        else:
            os.rename(temporary, self.settings_file)
        self.networks = networks
        self.revision += 1
        self.handoff_deadline = None
        self.handoff_pending = bool(networks)

    def record(self, current_s, current_k, errors):
        self.sequence += 1
        self.sample_at = ticks_ms()
        self.errors = errors
        row = [self.sequence, self.uptime(),
               rounded(current_s[0]) if current_s is not None else None,
               rounded(current_s[1]) if current_s is not None else None,
               rounded(current_k)]
        self.rows.append(row)
        if len(self.rows) > MAX_ROWS:
            self.rows.pop(0)

    def snapshot(self, after=0, boot=''):
        if boot != self.boot or after > self.sequence:
            after = 0
        return {'version': VERSION, 'boot': self.boot, 'uptime': self.uptime(),
                'interval_ms': SAMPLE_MS, 'capacity': MAX_ROWS, 'demo': self.demo,
                'free': free_bytes(), 'clients': self.active_clients,
                'latest': self.rows[-1] if self.rows else None,
                'rows': [row for row in self.rows if row[0] > after],
                'sample_age_ms': None if self.sample_at is None else ticks_diff(ticks_ms(), self.sample_at),
                'errors': self.errors, 'task_errors': self.task_errors,
                'handoff_remaining_s': self.remaining_seconds(self.handoff_deadline),
                'net': self.net,
                'configured_ssids': [item['ssid'] for item in self.networks]}

    def setup_network(self):
        import network
        self.network = network
        self.sta = network.WLAN(network.STA_IF)
        self.sta.active(False)
        self.ap = network.WLAN(network.AP_IF)
        try:
            network.hostname(HOSTNAME)
        except (AttributeError, OSError, ValueError):
            pass
        self.ap_on()

    def ap_on(self):
        if self.ap.active():
            self.net['ap_ip'] = self.ap.ifconfig()[0]
            return
        self.ap.active(True)
        self.ap.config(essid=AP_SSID, password=AP_PASSWORD,
                       authmode=self.network.AUTH_WPA2_PSK, max_clients=4)
        self.ap.ifconfig((AP_IP, '255.255.255.0', AP_IP, AP_IP))
        self.net['ap_ip'] = self.ap.ifconfig()[0]
        self.net['ap_on'] = True
        print('HOTSPOT_READY', AP_SSID, 'http://' + self.net['ap_ip'])

    def ap_off(self):
        if not self.ap.active():
            return
        self.ap.active(False)
        self.net['ap_on'] = False
        print('HOTSPOT_OFF router connected; use http://' + (self.net['sta_ip'] or 'router-assigned ip'))

    async def network_loop(self):
        seen_revision = -1
        last_status = None
        while True:
            try:
                now = ticks_ms()
                if last_status is None or ticks_diff(now, last_status) >= STATUS_EVERY_MS:
                    last_status = now
                    try:
                        rssi = self.sta.status('rssi') if self.sta and self.sta.active() else None
                    except (OSError, TypeError, ValueError):
                        rssi = None
                    print('NET_STATUS', self.net['sta_state'], self.net['sta_ip'] or '-',
                          'rssi', rssi, 'ap_on', self.net['ap_on'])
                if seen_revision != self.revision:
                    self.sta.active(True)
                    self.sta.disconnect()
                    self.sta.active(bool(self.networks))
                    self.net.update(sta_ssid='', sta_ip='')
                    seen_revision = self.revision
                if not self.networks:
                    self.net['sta_state'] = 'hotspot_only'
                    self.ap_on()
                    self.task_errors.pop('network', None)
                    await asyncio.sleep(1)
                    continue
                if self.sta.isconnected():
                    self.net['sta_state'] = 'connected'
                    self.net['sta_ip'] = self.sta.ifconfig()[0]
                    if self.handoff_pending:
                        self.handoff_deadline = ticks_add(ticks_ms(), AP_HANDOFF_MS)
                        self.handoff_pending = False
                    if self.remaining_seconds(self.handoff_deadline) == 0:
                        self.ap_off()
                        self.handoff_deadline = None
                    self.task_errors.pop('network', None)
                    await asyncio.sleep(2)
                    continue
                self.ap_on()
                for item in self.networks[:]:
                    if seen_revision != self.revision:
                        break
                    self.net.update(sta_state='connecting', sta_ssid=item['ssid'], sta_ip='')
                    self.sta.disconnect()
                    self.sta.connect(item['ssid'], item['password'])
                    for _ in range(24):
                        if self.sta.isconnected() or seen_revision != self.revision:
                            break
                        await asyncio.sleep(0.5)
                    if self.sta.isconnected():
                        self.net.update(sta_state='connected', sta_ip=self.sta.ifconfig()[0])
                        print('ROUTER_CONNECTED', item['ssid'], self.net['sta_ip'])
                        break
                    self.sta.disconnect()
                if not self.sta.isconnected() and seen_revision == self.revision:
                    self.net.update(sta_state='retry_wait', sta_ip='')
                    print('ROUTER_RETRY_WAIT')
                    # Configured networks only; no continuous blocking scans.
                    for _ in range(max(1, RETRY_WAIT_MS // 1000)):
                        if seen_revision != self.revision:
                            break
                        await asyncio.sleep(1)
            except OSError as error:
                self.net.update(sta_state='retry_wait', sta_ip='')
                print('Wi-Fi connection error:', error)
                self.ap_on()
                await asyncio.sleep(5)

    def network_page(self, oled):
        oled.fill(0)
        connected = self.net['sta_state'] == 'connected'
        oled.text('WIFI WEB', 0, 0)
        if connected:
            oled.text('STA CONNECTED', 0, 12)
            # A dotted IPv4 address fits in 16 OLED characters; "LAN " + IP does not.
            oled.text(self.net['sta_ip'], 0, 24)
            oled.text('PHONE SAME WIFI', 0, 36)
            oled.text('OPEN IP ABOVE', 0, 48)
        else:
            oled.text(AP_SSID, 0, 12)
            oled.text(self.net['ap_ip'], 0, 24)
            oled.text('PW ' + AP_PASSWORD, 0, 36)
            try:
                clients = len(self.ap.status('stations')) if self.ap.active() else 0
            except (OSError, TypeError):
                clients = 0
            oled.text('CLIENTS %d' % clients, 0, 48)
        oled.show()

    async def mdns_loop(self):
        import select
        import socket
        sock = None
        for attempt in range(6):
            candidate = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                candidate.bind(('0.0.0.0', MDNS_PORT))
                candidate.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                     socket.inet_aton(MDNS_ADDR) + socket.inet_aton('0.0.0.0'))
                sock = candidate
                break
            except OSError as error:
                print('MDNS_RETRY', attempt, error)
                try:
                    candidate.close()
                except OSError:
                    pass
                await asyncio.sleep(2)
        if sock is None:
            print('MDNS_DISABLED port busy; IP address still works')
            return
        poller = select.poll()
        poller.register(sock, select.POLLIN)
        wanted = MDNS_NAME.encode()
        print('MDNS_READY', MDNS_NAME)
        while True:
            if not poller.poll(0):
                await asyncio.sleep(0.1)
                continue
            try:
                packet, address = sock.recvfrom(512)
            except OSError:
                continue
            if not packet or mdns_query_name(packet) != wanted:
                continue
            ip = self.net['sta_ip'] if self.net['sta_state'] == 'connected' else self.net['ap_ip']
            try:
                reply = mdns_answer(wanted, socket.inet_aton(ip))
                sock.sendto(reply, (MDNS_ADDR, MDNS_PORT))
                sock.sendto(reply, address)
            except OSError as error:
                print('MDNS_REPLY_ERROR', error)

    async def sensor_loop(self):
        from machine import Pin, SoftI2C
        import ssd1306
        from max6675 import MAX6675
        from oled_sht3x_monitor import read_sensor
        from dual_sensor_compare import trim_append
        import sht_max_oled

        oled_bus = SoftI2C(sda=Pin(21), scl=Pin(22), freq=100000)
        sensor_bus = SoftI2C(sda=Pin(25), scl=Pin(26), freq=100000)
        oled = None
        try:
            address = next((a for a in oled_bus.scan() if a in (0x3C, 0x3D)), None)
            if address is not None:
                oled = ssd1306.SSD1306_I2C(128, 64, oled_bus, addr=address)
                self.network_page(oled)
        except OSError as error:
            print('OLED startup error:', error)
        sensor_k = MAX6675(sck=18, cs=23, so=19)
        sensor_s_address = None
        histories = ([], [])
        await asyncio.sleep(2)
        previous_page = -1
        while True:
            started = ticks_ms()
            current_s, current_k = None, None
            errors = {}
            try:
                if sensor_s_address is None:
                    sensor_s_address = next((a for a in sensor_bus.scan() if a in (0x44, 0x45)), None)
                    if sensor_s_address is None:
                        raise OSError('SHT3x missing')
                current_s = read_sensor(sensor_bus, sensor_s_address)
            except (OSError, ValueError) as error:
                sensor_s_address = None
                errors['s'] = str(error)
            try:
                current_k = sensor_k.read()
            except (OSError, ValueError) as error:
                errors['k'] = str(error)
            for history, value in zip(histories, (current_s, current_k)):
                trim_append(history, value)
            self.record(current_s, current_k, errors)
            self.task_errors.pop('sensor', None)
            page = (self.uptime() // 8) % 5
            if page != previous_page:
                print('WEB_OLED_PAGE', page)
                previous_page = page
            if oled is not None:
                try:
                    if page == 4:
                        self.network_page(oled)
                    else:
                        sht_max_oled.draw(oled, page, histories, current_s, current_k)
                except OSError as error:
                    print('OLED error:', error)
            print('WEB_SAMPLE', self.sequence, current_s, current_k, 'free', free_bytes())
            if errors:
                print('SENSOR_ERRORS', errors)
            elapsed = ticks_diff(ticks_ms(), started)
            await asyncio.sleep(max(0.05, (SAMPLE_MS - elapsed) / 1000))

    @staticmethod
    async def response(writer, status, content_type, body):
        if isinstance(body, str):
            body = body.encode()
        header = ('HTTP/1.0 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n'
                  'Cache-Control: no-store\r\nConnection: close\r\n'
                  'X-Content-Type-Options: nosniff\r\n\r\n') % (status, content_type, len(body))
        writer.write(header.encode())
        writer.write(body)
        await writer.drain()

    async def serve_file(self, writer, path, content_type):
        size = os.stat(path)[6]
        writer.write(('HTTP/1.0 200 OK\r\nContent-Type: %s\r\n'
                      'Content-Length: %d\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n'
                      % (content_type, size)).encode())
        with open(path, 'rb') as stream:
            while True:
                chunk = stream.read(1024)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()

    async def dispatch(self, reader, writer):
        line = await asyncio.wait_for(reader.readline(), 3)
        if len(line) > 512:
            raise ValueError('Request line too long')
        parts = line.decode().strip().split()
        if len(parts) != 3:
            raise ValueError('Invalid request')
        method, target = parts[0], parts[1]
        headers, total = {}, 0
        while True:
            line = await asyncio.wait_for(reader.readline(), 3)
            total += len(line)
            if total > 4096:
                raise ValueError('Headers too long')
            if line in (b'\r\n', b'\n', b''):
                break
            key, value = line.decode().split(':', 1)
            headers[key.lower().strip()] = value.strip()
        path, _, query = target.partition('?')
        self.request_count += 1
        if self.request_count % 10 == 1:
            print('HTTP', self.request_count, target, 'clients', self.active_clients,
                  'free', free_bytes())
        if method == 'GET' and path == '/':
            await self.serve_file(writer, self.html_file, 'text/html; charset=utf-8')
        elif method == 'GET' and path == '/app.js':
            await self.serve_file(writer, self.javascript_file,
                                  'application/javascript; charset=utf-8')
        elif method == 'GET' and path == '/api/data':
            params = {}
            for item in query.split('&'):
                key, _, value = item.partition('=')
                params[key] = value
            try:
                after = max(0, int(params.get('after', '0')))
            except ValueError:
                after = 0
            payload = json.dumps(self.snapshot(after, params.get('boot', '')))
            await self.response(writer, '200 OK', 'application/json; charset=utf-8', payload)
        elif method == 'POST' and path == '/api/wifi':
            if headers.get('content-type', '').split(';')[0] != 'application/json':
                raise ValueError('Use application/json')
            length = int(headers.get('content-length', '0'))
            if not 1 <= length <= 1024:
                raise ValueError('Invalid settings length')
            # Browser requests must originate from this same local dashboard.
            origin = headers.get('origin')
            if origin and origin != 'http://' + headers.get('host', ''):
                raise ValueError('Settings must be sent from this device page')
            body = await asyncio.wait_for(reader.readexactly(length), 3)
            self.update_settings(json.loads(body.decode()))
            await self.response(writer, '200 OK', 'application/json', '{"ok":true}')
        elif path == '/favicon.ico':
            await self.response(writer, '204 No Content', 'text/plain', b'')
        else:
            await self.response(writer, '404 Not Found', 'text/plain', 'Not found')

    async def handle(self, reader, writer):
        if self.active_clients >= MAX_CLIENTS:
            print('HTTP_BUSY clients', self.active_clients)
            try:
                await self.response(writer, '503 Busy', 'text/plain', 'Busy; retry in two seconds')
            finally:
                writer.close()
            return
        self.active_clients += 1
        try:
            await self.dispatch(reader, writer)
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            try:
                await self.response(writer, '400 Bad Request', 'application/json',
                                    json.dumps({'ok': False, 'message': str(error)}))
            except OSError:
                pass
        except (OSError, EOFError, asyncio.TimeoutError):
            pass
        finally:
            self.active_clients -= 1
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (OSError, asyncio.TimeoutError):
                pass
            gc.collect()

    async def run(self):
        self.setup_network()
        server = await asyncio.start_server(self.handle, '0.0.0.0', 80, backlog=4)
        print('WEB_SERVER_READY', VERSION)
        asyncio.create_task(self.supervise('sensor', self.sensor_loop))
        asyncio.create_task(self.supervise('network', self.network_loop))
        asyncio.create_task(self.mdns_loop())
        while True:
            await asyncio.sleep(60)


def main():
    gc.collect()
    monitor = Monitor()
    try:
        asyncio.run(monitor.run())
    finally:
        asyncio.new_event_loop()


if __name__ == '__main__':
    main()
