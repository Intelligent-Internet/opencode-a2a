from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import TypeVar

T = TypeVar("T")


async def iter_async(
    items: Iterable[T] = (),
    *,
    terminal_error: BaseException | None = None,
) -> AsyncIterator[T]:
    for item in items:
        yield item
    if terminal_error is not None:
        raise terminal_error
