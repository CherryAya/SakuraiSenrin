"""``PicImageSearch`` 的最小类型存根。

``PicImageSearch`` 未随包发布 ``py.typed``，strict 模式下 pyright 会把
``Network`` / ``SauceNAO`` / ``Ascii2D`` 的全部成员判为 Unknown，并顺着
``Network(...)`` 上下文管理器与 ``.search(...)`` 调用向下传播，在以图搜图
插件的 ``search_image`` 链路上制造噪音；同时 ``import PicImageSearch``
本身会报 reportMissingTypeStubs。

这里只描述本仓库实际用到的成员：``Network`` 的异步上下文管理器协议、
``SauceNAO`` / ``Ascii2D`` 的构造与 ``search``。签名与
``.venv/Lib/site-packages/PicImageSearch/`` 下的真实定义对齐。

``search`` 的返回形态是各引擎自己的响应对象，本仓库只通过 ``getattr``
读取其 ``raw`` 列表，故声明为 ``object`` 并由调用方收窄。
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

class Network:
    def __init__(
        self,
        internal: bool = False,
        proxies: str | None = None,
        headers: dict[str, str] | None = None,
        cookies: str | None = None,
        timeout: float = 30,
        verify_ssl: bool = True,
        http2: bool = False,
    ) -> None: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

class _Engine:
    def __init__(self, base_url: str = ..., **request_kwargs: Any) -> None: ...
    async def search(self, **kwargs: Any) -> object: ...

class SauceNAO(_Engine):
    def __init__(
        self,
        base_url: str = "https://saucenao.com",
        api_key: str | None = None,
        numres: int = 5,
        hide: int = 0,
        minsim: int = 30,
        output_type: int = 2,
        testmode: int = 0,
        dbmask: int | None = None,
        dbmaski: int | None = None,
        db: int = 999,
        dbs: list[int] | None = None,
        **request_kwargs: Any,
    ) -> None: ...

class Ascii2D(_Engine):
    def __init__(
        self,
        base_url: str = "https://ascii2d.net",
        bovw: bool = False,
        **request_kwargs: Any,
    ) -> None: ...
