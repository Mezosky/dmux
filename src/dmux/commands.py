"""Bounded output from explicitly selected observation commands."""
from __future__ import annotations

import os
import selectors
import subprocess
import time


def read_command(argv, *, timeout=2.0, max_bytes=65_536):
    """Never use a shell; on overflow/timeout stop only the owned probe process."""
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL, bufsize=0, start_new_session=True) as process:
        chunks, size = [], 0
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError(f"{argv[0]} timed out after {timeout:g}s")
                    for key, _ in selector.select(remaining):
                        block = os.read(key.fd, min(8192, max_bytes + 1 - size))
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        size += len(block)
                        if size > max_bytes:
                            raise ValueError(f"{argv[0]} output exceeded {max_bytes} bytes")
                        chunks.append(block)
            try:
                code = process.wait(timeout=max(.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise ValueError(f"{argv[0]} timed out after {timeout:g}s") from exc
            text = b"".join(chunks).decode("utf-8", errors="replace")
            if code:
                raise ValueError(f"{argv[0]} exited {code}: {text.strip()[:240]}")
            return text
        except BaseException:
            process.kill()
            process.wait()
            raise
