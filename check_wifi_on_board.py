"""Temporary on-board check of SHT3x, MAX6675, AP, HTTP, and concurrent requests."""
import asyncio
import gc
import json
from sensor_wifi_web import Monitor, AP_IP


async def request(path):
    reader, writer = await asyncio.open_connection(AP_IP, 80)
    writer.write(('GET %s HTTP/1.0\r\nHost: %s\r\n\r\n' % (path, AP_IP)).encode())
    await writer.drain()
    data = await reader.read()
    writer.close()
    await writer.wait_closed()
    headers, body = data.split(b'\r\n\r\n', 1)
    assert headers.startswith(b'HTTP/1.0 200'), headers
    for line in headers.split(b'\r\n'):
        if line.lower().startswith(b'content-length:'):
            assert len(body) == int(line.split(b':')[1]), 'HTTP body length mismatch'
    return body


async def check():
    gc.collect()
    monitor = Monitor()
    monitor.setup_network()
    server = await asyncio.start_server(monitor.handle, '0.0.0.0', 80, backlog=4)
    sampler = asyncio.create_task(monitor.sensor_loop())
    await asyncio.sleep(5)
    body = await request('/api/data')
    initial = json.loads(body)
    assert initial['latest'] is not None and initial['latest'][2] is not None
    assert initial['latest'][3] is not None and initial['latest'][4] is not None
    assert not initial['errors'], initial['errors']
    assert initial['version'] == 'sensor-wifi-web-v4' and not initial['task_errors']
    print('LIVE_HTTP_SENSORS', initial['latest'])
    html = await request('/')
    assert '温度与湿度'.encode() in html
    print('LIVE_HTML_BYTES', len(html))
    javascript = await request('/app.js')
    assert b'indexedDB.open' in javascript
    print('LIVE_JAVASCRIPT_BYTES', len(javascript))
    replies = await asyncio.gather(request('/api/data'), request('/api/data'))
    assert all(json.loads(reply)['boot'] == monitor.boot for reply in replies)
    print('LIVE_CONCURRENT_HTTP_OK', len(replies))
    await asyncio.sleep(3)
    current = json.loads(await request('/api/data'))
    assert current['latest'][0] > initial['latest'][0]
    print('LIVE_SAMPLE_CONTINUES', current['latest'], 'free', gc.mem_free())
    with open('wifi_live_check.json', 'w') as stream:
        json.dump(current, stream)
    sampler.cancel()
    server.close()
    await server.wait_closed()
    print('LIVE_WIFI_HTTP_CHECK_PASS')


try:
    asyncio.run(check())
finally:
    asyncio.new_event_loop()
