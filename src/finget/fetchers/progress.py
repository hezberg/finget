"""进度展示工具，基于 rich."""

from collections.abc import Iterable
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
)

console = Console()


def create_progress() -> Progress:
    """创建一个通用进度条实例.

    列布局（精简，只保留 4 列核心）:
    - TextColumn: 任务描述
    - BarColumn: 进度条（固定宽度 30，视觉整齐）
    - TaskProgressColumn: 百分比
    - MofNCompleteColumn: 计数
    """
    return Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30, finished_style="green", pulse_style="accent"),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        console=console,
        expand=False,
        transient=False,
    )


def iter_with_progress(
    iterable: Iterable[Any],
    description: str = "Processing",
    total: int | None = None,
) -> Iterable[Any]:
    """带进度条的迭代器.

    Args:
        iterable: 可迭代对象.
        description: 进度条描述.
        total: 总数; None 则自动推断.

    Yields:
        原始元素.
    """
    with create_progress() as progress:
        task = progress.add_task(description, total=total)
        for item in iterable:
            yield item
            progress.advance(task)
