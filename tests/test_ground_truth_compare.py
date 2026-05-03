from __future__ import annotations

import unittest

from compare_extractions_to_ground_truth import page_ref_matches


class GroundTruthCompareTest(unittest.TestCase):
    def test_page_ref_matches_normalized_page_references(self) -> None:
        self.assertTrue(page_ref_matches("p. 88", 88))
        self.assertTrue(page_ref_matches("pp. 88-89", 88))
        self.assertFalse(page_ref_matches("p. 89", 88))


if __name__ == "__main__":
    unittest.main()
