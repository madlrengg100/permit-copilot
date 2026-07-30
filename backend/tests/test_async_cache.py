import asyncio
import unittest

from app.cache import async_ttl_cache


class AsyncTTLCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_result_and_returns_independent_copy(self):
        calls = 0

        @async_ttl_cache(ttl_seconds=60, maxsize=8)
        async def load(key):
            nonlocal calls
            calls += 1
            return {"key": key, "items": []}

        first = await load("pnu")
        first["items"].append("changed")
        second = await load("pnu")

        self.assertEqual(calls, 1)
        self.assertEqual(second["items"], [])

    async def test_deduplicates_concurrent_requests(self):
        calls = 0

        @async_ttl_cache(ttl_seconds=60, maxsize=8)
        async def load(key):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return {"key": key}

        results = await asyncio.gather(load("same"), load("same"), load("same"))

        self.assertEqual(calls, 1)
        self.assertEqual(results, [{"key": "same"}] * 3)


if __name__ == "__main__":
    unittest.main()
