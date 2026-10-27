"""Host-only isolated Edge mobile/desktop QA against labelled mock preview."""
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from time import perf_counter
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel='msedge', headless=True)
    context = browser.new_context(viewport={'width': 390, 'height': 844},
                                  is_mobile=True, has_touch=True, accept_downloads=True)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('dialog', lambda dialog: dialog.accept())
    page.goto('http://127.0.0.1:8769', wait_until='networkidle')
    expect(page.get_by_role('heading', name='温度与湿度，实时对照')).to_be_visible()
    expect(page.locator('#demo')).to_be_visible()
    expect(page.locator('#connection-text')).to_have_text('实时连接')
    assert page.locator('#st').inner_text() != '--'
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile overflow'
    page.screenshot(path=str(ROOT / '手机网页预览_模拟数据.png'), full_page=True)
    page.get_by_role('button', name='暂停刷新', exact=True).click()
    expect(page.locator('#connection-text')).to_have_text('已暂停')
    page.get_by_role('button', name='继续刷新', exact=True).click()
    expect(page.locator('#connection-text')).to_have_text('实时连接')
    page.get_by_label('曲线时间范围').select_option('60')
    page.get_by_label('温度曲线刻度').select_option('air')
    expect(page.locator('#temp-axis-label')).to_contain_text('独立刻度')
    page.get_by_label('温度曲线刻度').select_option('shared')
    page.get_by_label('MAX6675', exact=True).uncheck()
    expect(page.locator('#show-k')).not_to_be_checked()
    page.get_by_label('MAX6675', exact=True).check()
    with page.expect_download() as event:
        page.get_by_role('button', name='导出 CSV', exact=True).click()
    download_path = ROOT / '网页导出测试_模拟数据.csv'
    event.value.save_as(str(download_path))
    csv = download_path.read_text(encoding='utf-8-sig')
    assert 'MAX6675_temperature_C' in csv and 'browser_estimated_time_utc' in csv
    assert len(csv.splitlines()) > 100
    with page.expect_download() as event:
        page.get_by_role('button', name='导出 Excel', exact=True).click()
    xlsx_path = ROOT / '网页导出测试_模拟数据.xlsx'
    event.value.save_as(str(xlsx_path))
    with ZipFile(xlsx_path) as book:
        assert book.testzip() is None
        sheet = ET.fromstring(book.read('xl/worksheets/sheet1.xml'))
        ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        rows = sheet.findall('.//x:row', ns)
        assert len(rows) > 100
        assert rows[0].find("x:c[@r='H1']/x:is/x:t", ns).text == 'MAX6675_temperature_C'
        assert rows[1].find("x:c[@r='F2']/x:v", ns) is not None
    page.wait_for_timeout(500)
    page.reload(wait_until='networkidle')
    expect(page.locator('#storage-info')).to_contain_text('本浏览器已保存')
    assert page.evaluate('samples.length') >= 150
    page.evaluate("showSwitch({sta_state:'connected',sta_ip:'172.24.111.14',ap_ip:'127.0.0.1'},10)")
    expect(page.locator('#switch-link')).to_have_attribute('href', 'http://172.24.111.14/')
    page.locator('summary').click()
    page.locator('#wifi-ssid').fill('OWN_BROWSER_TEST_NETWORK')
    page.get_by_role('button', name='保存并连接', exact=True).click()
    expect(page.locator('#wifi-message')).to_contain_text('已保存')
    expect(page.locator('#network-status')).to_contain_text('OWN_BROWSER_TEST_NETWORK')
    page.get_by_role('button', name='仅使用设备热点', exact=True).click()
    expect(page.locator('#wifi-message')).to_contain_text('ESP32-Sensors-1')
    page.locator('summary').click()
    page.set_viewport_size({'width': 1180, 'height': 820})
    page.screenshot(path=str(ROOT / '电脑网页预览_模拟数据.png'), full_page=True)
    render_ms = page.evaluate("""() => {
      const now = Date.now();
      samples = Array.from({length: 43200}, (_, i) => ({
        id: 'load:' + i, boot: 'load', wall: now - (43200 - i) * 2000,
        row: [i, i * 2, 25 + Math.sin(i / 300), 65 + Math.sin(i / 420), 26 + Math.sin(i / 280)]
      }));
      document.getElementById('window').value = '86400';
      const started = performance.now();
      drawCharts(true);
      return performance.now() - started;
    }""")
    assert render_ms < 5000, f'24-hour chart draw took {render_ms:.0f} ms'
    started = perf_counter()
    with page.expect_download(timeout=30000) as event:
        page.get_by_role('button', name='导出 Excel', exact=True).click()
    large_xlsx = ROOT / '网页导出测试_24小时模拟数据.xlsx'
    event.value.save_as(str(large_xlsx))
    export_s = perf_counter() - started
    with ZipFile(large_xlsx) as book:
        assert book.testzip() is None
        assert b'<dimension ref="A1:H43201"/>' in book.read('xl/worksheets/sheet1.xml')[:200]
    large_xlsx.unlink()
    assert not errors, errors
    context.close()
    browser.close()
print('PASS: mobile layout, no overflow/JS errors, live refresh, pause/resume,')
print('      sensor toggle, time range, real browser CSV/XLSX download, Wi-Fi form, desktop screenshot')
print(f'      24-hour chart with 43200 samples: {render_ms:.0f} ms')
print(f'      24-hour Excel export with 43200 samples: {export_s:.2f} s')
