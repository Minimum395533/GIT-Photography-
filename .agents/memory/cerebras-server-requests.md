---
name: Cerebras server requests
description: Environment-specific Cerebras API request behavior.
---

The Cerebras API rejected Python's default urllib request with HTTP 403/error code 1010, while the same request succeeded when an explicit User-Agent header was supplied.

**Why:** The edge layer treats the default Python request signature as automated traffic.

**How to apply:** Keep an explicit, non-secret User-Agent on server-side Cerebras requests.