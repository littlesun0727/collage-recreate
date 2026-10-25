import inspect
import unittest
from concurrent.futures import Future
from tenacity import RetryError, retry, stop_after_attempt
from mediacrawler_worker import classify_exception, safe_exception_detail


class WorkerErrorTests(unittest.TestCase):
    def test_nested_retry_reports_original_restriction_without_message_leak(self):
        class IPBlockError(Exception):pass
        inner=IPBlockError('secret cookie must not appear')
        for _ in range(2):
            future=Future();future.set_exception(inner);inner=RetryError(future)
        self.assertEqual(classify_exception(inner)[0],'blocked')
        detail=safe_exception_detail(inner)
        self.assertEqual(detail['root_type'],'IPBlockError')
        self.assertNotIn('secret',str(detail))

    def test_full_unwrap_reaches_original_async_function(self):
        async def raw():return 'ok'
        wrapped=retry(stop=stop_after_attempt(3))(raw)
        self.assertIs(inspect.unwrap(wrapped),raw)


if __name__=='__main__':unittest.main()
