"""Playwright 渲染兜底（可选依赖）。

部分电商详情页是前端渲染或用风控校验，纯 requests 拿不到价格。
安装了 playwright 后，这些页面可回退到真实浏览器渲染再解析。

安装：
    pip install playwright && playwright install chromium
"""
from __future__ import annotations

from urllib.parse import urlparse

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class RendererError(RuntimeError):
    """渲染失败（未安装依赖或页面超时）。"""


def available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def render_html(url: str, cookie: str = "", timeout_ms: int = 25000,
                wait_selector: str | None = None) -> str:
    """用 Chromium 打开页面并返回渲染后的 HTML。"""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError as e:
        raise RendererError(
            "未安装 playwright，无法渲染兜底：pip install playwright && playwright install chromium"
        ) from e

    host = urlparse(url).netloc
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(user_agent=UA, locale="zh-CN", viewport={
                "width": 1366, "height": 900,
            })
            if cookie:
                for part in cookie.split(";"):
                    if "=" not in part:
                        continue
                    name, value = part.split("=", 1)
                    ctx.add_cookies([{
                        "name": name.strip(), "value": value.strip(),
                        "domain": host, "path": "/",
                    }])
            page = ctx.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=timeout_ms)
            else:
                page.wait_for_timeout(1200)  # 给前端异步数据一点时间
            html = page.content()
            browser.close()
            return html
    except PWTimeout as e:
        raise RendererError(f"页面渲染超时：{url}") from e
    except RendererError:
        raise
    except Exception as e:  # noqa: BLE001
        raise RendererError(f"渲染失败：{e}") from e
