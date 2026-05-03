from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from revenue_segment_extractor.extraction.providers import _redact_sensitive


class ProviderSecurityTest(unittest.TestCase):
    def test_redact_sensitive_removes_api_key_values_from_error_text(self) -> None:
        key = "sk-ant-test-secret-value"
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": key}):
            redacted = _redact_sensitive(f"request failed with {key}")

        self.assertNotIn(key, redacted)
        self.assertIn("[REDACTED_API_KEY]", redacted)


if __name__ == "__main__":
    unittest.main()
