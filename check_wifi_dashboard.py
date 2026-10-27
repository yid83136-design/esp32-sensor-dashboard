"""Host-only HTTP checks and optional labelled browser preview. Never upload."""

import asyncio
import json
import math
import py_compile
import sys
import tempfile
from pathlib import Path
from sensor_wifi_web import Monitor, MAX_ROWS

ROOT = Path(__file__).resolve().parent


async def request(port, target, method='GET', data=None):
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    body = b'' if data is None else json.dumps(data).encode()
    header = '%s %s HTTP/1.0\r\nHost: 127.0.0.1:%d\r\n' % (method, target, port)
    if data is not None:
        header += 'Content-Type: application/json\r\nContent-Length: %d\r\n' % len(body)
    writer.write(header.encode() + b'\r\n' + body)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, payload = response.split(b'\r\n\r\n', 1)
    status = int(head.split(b' ')[1])
    for line in head.split(b'\r\n'):
        if line.lower().startswith(b'content-length:'):
            assert len(payload) == int(line.split(b':')[1]), 'HTTP body length mismatch'
    return status, payload


async def check():
    for name in ('sensor_wifi_web.py', 'sht_max_oled.py'):
        py_compile.compile(str(ROOT / name), doraise=True)
    with tempfile.TemporaryDirectory(prefix='esp32_web_', dir=ROOT) as folder:
        monitor = Monitor(str(Path(folder) / 'settings.json'),
                          str(ROOT / 'sensor_dashboard.html'),
                          str(ROOT / 'sensor_dashboard.js'))
        server = await asyncio.start_server(monitor.handle, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        status, body = await request(port, '/')
        assert status == 200 and '温度与湿度'.encode() in body
        status, body = await request(port, '/app.js')
        assert status == 200 and b'indexedDB.open' in body
        status, body = await request(port, '/api/data')
        initial = json.loads(body)
        assert initial['latest'] is None and not initial['rows']
        assert initial['task_errors'] == {} and initial['handoff_remaining_s'] == 0
        for i in range(MAX_ROWS + 3):
            monitor.record((25 + i / 100, 60), 26.25, {})
        assert len(monitor.rows) == MAX_ROWS and monitor.rows[0][0] == 4
        status, body = await request(port, '/api/data?after=152&boot=' + monitor.boot)
        response = json.loads(body)
        assert status == 200 and len(response['rows']) == 1 and response['latest'][4] == 26.25
        _, body = await request(port, '/api/data?after=99999&boot=old-boot')
        assert len(json.loads(body)['rows']) == MAX_ROWS
        monitor.record(None, None, {'k': 'open thermocouple'})
        assert monitor.snapshot()['latest'][2:] == [None] * 3
        assert monitor.snapshot()['errors']['k'] == 'open thermocouple'
        status, _ = await request(port, '/api/wifi', 'POST', {'ssid': 'OWN_TEST_NETWORK', 'password': '88888888'})
        assert status == 200 and monitor.networks[0]['ssid'] == 'OWN_TEST_NETWORK'
        assert monitor.handoff_pending is True
        _, body = await request(port, '/api/data')
        assert b'88888888' not in body and b'"password"' not in body
        status, _ = await request(port, '/api/wifi', 'POST', {'ssid': 'SECOND_OWN_NETWORK', 'password': '88888888'})
        assert status == 200 and len(monitor.networks) == 2
        status, _ = await request(port, '/api/wifi', 'POST', {'ssid': 'Bad', 'password': 'short'})
        assert status == 400 and len(monitor.networks) == 2
        replies = await asyncio.gather(*[request(port, '/api/data') for _ in range(3)])
        assert all(status == 200 for status, _ in replies)
        status, _ = await request(port, '/api/wifi', 'POST', {'hotspot_only': True})
        assert status == 200 and monitor.networks == []
        assert Monitor(monitor.settings_file, monitor.html_file).networks == []
        status, _ = await request(port, '/not-found')
        assert status == 404
        server.close()
        await server.wait_closed()
    print('PASS: HTML, HTTP byte lengths, live JSON, incremental/reboot history, missing values,')
    print('      settings validation/persistence, no password exposure in JSON, three concurrent clients')


async def preview():
    monitor = Monitor(str(ROOT / 'preview_wifi_settings.json'),
                      str(ROOT / 'sensor_dashboard.html'),
                      str(ROOT / 'sensor_dashboard.js'))
    monitor.networks = []
    monitor.demo = True
    for i in range(150):
        monitor.record((25.3 + .3 * math.sin(i / 20), 65 + 2 * math.sin(i / 30)),
                       26.25 + .25 * math.sin(i / 17), {})
        monitor.rows[-1][1] = i * 2
    server = await asyncio.start_server(monitor.handle, '127.0.0.1', 8769)
    print('LABELLED PREVIEW http://127.0.0.1:8769', flush=True)
    i = 150
    while True:
        await asyncio.sleep(2)
        monitor.record((25.3 + .3 * math.sin(i / 20), 65 + 2 * math.sin(i / 30)),
                       26.25 + .25 * math.sin(i / 17), {})
        monitor.rows[-1][1] = i * 2
        i += 1


if __name__ == '__main__':
    asyncio.run(preview() if '--preview' in sys.argv else check())
