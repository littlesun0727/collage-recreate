import asyncio
from urllib.parse import quote, urlsplit


class AccessStopped(RuntimeError):
    pass


def access_error(url, title, text):
    if '/website-login/error' in url or title == '安全限制' or 'IP存在风险' in text:
        return '小红书安全限制：' + text[:300].replace('\n', ' ')
    return None


async def browse(store, cfg, mode, limit):
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(cfg.get('browser_profile',store.root / 'browser-profile')), headless=False if mode == 'login' else cfg.get('headless', False), channel=cfg.get('browser_channel'),
            viewport={'width':1440,'height':1000})
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            if mode == 'login':
                await page.goto('https://www.xiaohongshu.com', wait_until='domcontentloaded')
                await asyncio.to_thread(input, '请在浏览器完成登录，回到这里按 Enter 保存会话：')
                return
            tasks = set()
            stopped = []
            keyword = 'detail'

            async def capture(response):
                parsed = urlsplit(response.url)
                if not (parsed.hostname or '').endswith('.xiaohongshu.com') or '/api/' not in parsed.path:
                    return
                if response.status in (401,403,429):
                    stopped.append(f'平台返回 HTTP {response.status}，请稍后人工检查。')
                    return
                if '/feed' not in parsed.path and '/search/notes' not in parsed.path:
                    return
                try:
                    data = await response.json()
                except Exception:
                    return
                if data.get('success') is False:
                    stopped.append('笔记接口返回失败，请人工检查登录/访问限制。')
                    return
                # Search payloads are discovery only; a cover is not a full gallery.
                if '/search/notes' in parsed.path:
                    for item in data.get('data',{}).get('items',[]):
                        ident = item.get('id')
                        token = item.get('xsec_token', '')
                        if ident and token:
                            store.discover(f'https://www.xiaohongshu.com/explore/{ident}?xsec_token={quote(token)}&xsec_source=pc_search', keyword)
                else:
                    store.ingest(data, keyword)

            def on_response(response):
                task = asyncio.create_task(capture(response))
                tasks.add(task)

            async def settle():
                if tasks:
                    pending = list(tasks)
                    tasks.clear()
                    await asyncio.gather(*pending)
                title = await page.title()
                body = await page.locator('body').inner_text()
                reason = access_error(page.url, title, body)
                if reason:
                    import json
                    debug = store.root/'diagnostics'
                    debug.mkdir(exist_ok=True)
                    await page.screenshot(path=str(debug/'access-stopped.png'))
                    (debug/'access-stopped.json').write_text(json.dumps(
                        {'title':title,'reason':reason},ensure_ascii=False,indent=2),encoding='utf-8')
                    raise AccessStopped(reason)
                if stopped:
                    raise AccessStopped(stopped[0])
                for selector in ('iframe[src*="captcha"]', '[class*="captcha"]', '.login-container'):
                    if await page.locator(selector).first.is_visible():
                        raise AccessStopped('检测到登录或验证界面，已暂停。请运行 login 人工处理后续跑。')

            page.on('response', on_response)
            if mode == 'discover':
                for keyword in cfg['keywords']:
                    await page.goto('https://www.xiaohongshu.com/search_result?keyword='+quote(keyword)+'&source=web_search_result_notes', wait_until='domcontentloaded')
                    stale = 0
                    previous = 0
                    for _ in range(cfg['scrolls_per_keyword']):
                        await page.wait_for_timeout(cfg['page_delay_seconds']*1000)
                        await settle()
                        links = await page.locator('a[href*="/explore/"],a[href*="/search_result/"]').evaluate_all('(nodes)=>nodes.map(n=>n.href)')
                        for url in links:
                            store.discover(url,keyword)
                        count = store.db.execute('SELECT COUNT(*) FROM sources WHERE keyword=?',(keyword,)).fetchone()[0]
                        stale = stale + 1 if count == previous else 0
                        previous = count
                        if stale >= 3:
                            break
                        await page.mouse.wheel(0,1600)
                    if previous == 0:
                        raise AccessStopped('搜索未发现笔记：请检查登录状态、关键词或页面结构。')
                    store.db.execute('''INSERT INTO searches VALUES(?,1,datetime('now'),?)
                        ON CONFLICT(keyword) DO UPDATE SET runs=runs+1,updated=datetime('now'),discovered=excluded.discovered''',(keyword,previous))
                    store.db.commit()
                    print(f'{keyword}: {previous} 条笔记', flush=True)
            else:
                rows = store.db.execute("SELECT * FROM notes WHERE status IN ('pending','error') AND attempts<? ORDER BY attempts,id LIMIT ?",(cfg['max_attempts'],limit)).fetchall()
                for row in rows:
                    keyword = row['keyword']
                    store.db.execute('UPDATE notes SET attempts=attempts+1 WHERE id=?',(row['id'],))
                    store.db.commit()
                    try:
                        await page.goto(row['url'],wait_until='domcontentloaded')
                    except PlaywrightTimeout:
                        store.db.execute("UPDATE notes SET status='error',error='Navigation timeout' WHERE id=?",(row['id'],))
                        store.db.commit()
                        await settle()
                        continue
                    await page.wait_for_timeout(cfg['page_delay_seconds']*1000)
                    try:
                        await settle()
                    except AccessStopped as exc:
                        store.db.execute("UPDATE notes SET status='blocked',error=? WHERE id=?",(str(exc),row['id']))
                        store.db.commit()
                        raise
                    # Some visits hydrate the note without a feed request.
                    state = await page.evaluate('() => {try { return JSON.parse(JSON.stringify(window.__INITIAL_STATE__?.note || {})); } catch {return {};}}')
                    store.ingest(state,keyword)
                    if store.db.execute('SELECT status FROM notes WHERE id=?',(row['id'],)).fetchone()[0] != 'done':
                        import json
                        debug = store.root/'diagnostics'
                        debug.mkdir(exist_ok=True)
                        await page.screenshot(path=str(debug/'last-detail.png'),full_page=False)
                        summary = await page.evaluate('''() => ({url:location.href,title:document.title,
                            text:document.body.innerText.slice(0,12000),
                            stateKeys:Object.keys(window.__INITIAL_STATE__ || {}),
                            noteKeys:Object.keys(window.__INITIAL_STATE__?.note || {}),
                            imageCount:document.images.length})''')
                        (debug/'last-detail.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
                    store.db.execute("UPDATE notes SET status='error',error='No gallery; refresh discovery or inspect page' WHERE id=? AND status!='done'",(row['id'],))
                    store.db.commit()
                    print(f'笔记 {row["id"]}: 已处理',flush=True)
            await settle()
        finally:
            await context.close()
