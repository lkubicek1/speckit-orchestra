from __future__ import annotations

import random
import sys
import time
from contextlib import contextmanager
from threading import Event, Thread
from typing import Iterator


BRAILLE = [0x01, 0x02, 0x04, 0x40, 0x08, 0x10, 0x20, 0x80]


def progress_label(action: str, position: int, total: int, epic_id: str, title: str) -> str:
    return f"{action} {position}/{total}: {epic_id} {title}"


@contextmanager
def progress_spinner(message: str, *, enabled: bool = True, fps: int = 14) -> Iterator[None]:
    if not enabled or not sys.stderr.isatty():
        yield
        return
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.text import Text
    except Exception:
        yield
        return

    console = Console(stderr=True)
    stop = Event()
    rng = random.Random()
    active = set(rng.sample(range(16), 8))

    def run() -> None:
        with Live(console=console, refresh_per_second=fps, transient=True) as live:
            while not stop.is_set():
                active.intersection_update(dot for dot in active if rng.random() > 0.15)
                while len(active) < rng.randint(6, 10):
                    active.add(rng.randrange(16))

                text = Text()
                text.append(_grid(active), style="bright_cyan")
                text.append(f" {message}", style="cyan")
                live.update(text)
                time.sleep(1 / fps)

    thread = Thread(target=run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def _cell(dots: list[int]) -> str:
    return chr(0x2800 + sum(BRAILLE[dot] for dot in dots))


def _grid(active: set[int]) -> str:
    left: list[int] = []
    right: list[int] = []
    for pos in active:
        row, col = divmod(pos, 4)
        dot = row if col % 2 == 0 else row + 4
        if col < 2:
            left.append(dot)
        else:
            right.append(dot)
    return _cell(left) + _cell(right)
