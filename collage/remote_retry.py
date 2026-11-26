"""Retry temporary provider failures within a single operation."""
import time
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from .timings import log


def with_retries(operation):
    for attempt in range(3):
        try:
            return operation()
        except HTTPError as error:
            if error.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                raise
            error.close()
            reason = f"HTTP {error.code}"
        except (URLError, TimeoutError, ConnectionError, IncompleteRead) as error:
            if attempt == 2:
                raise
            reason = type(error).__name__
        log(f"第 {attempt+1}/3 次失败（{reason}），{2 ** (attempt+1)}s 后重试")
        time.sleep(2 ** (attempt + 1))
