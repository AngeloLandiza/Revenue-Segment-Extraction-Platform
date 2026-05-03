from __future__ import annotations

import unittest
from decimal import Decimal

from fitch_extractor.enrichment import (
    ROW_TYPE_BUSINESS_SEGMENT,
    ROW_TYPE_ELIMINATION,
    ROW_TYPE_OTHER_RECONCILIATION,
    ROW_TYPE_SUBTOTAL,
    ROW_TYPE_TOTAL,
    SEGMENT_TYPE_GEOGRAPHIC,
    SEGMENT_TYPE_MIXED,
    SEGMENT_TYPE_MULTI_ACTIVITY,
    SEGMENT_TYPE_SINGLE_ACTIVITY,
    classify_segment_row,
)


class EnrichmentClassificationTest(unittest.TestCase):
    def test_multilingual_row_type_classification(self) -> None:
        cases = {
            "I alt": ROW_TYPE_TOTAL,
            "Eliminierungen": ROW_TYPE_ELIMINATION,
            "Sous-total segments": ROW_TYPE_SUBTOTAL,
            "Nicht zugeordnet": ROW_TYPE_OTHER_RECONCILIATION,
            "Government grant income": ROW_TYPE_OTHER_RECONCILIATION,
            "Business interruption proceeds": ROW_TYPE_OTHER_RECONCILIATION,
            "Insurance": ROW_TYPE_BUSINESS_SEGMENT,
        }

        for label, expected in cases.items():
            with self.subTest(label=label):
                classification = classify_segment_row(label)
                self.assertEqual(expected, classification.row_type)

    def test_segment_type_marks_mixed_and_geographic_rows_for_review(self) -> None:
        mixed = classify_segment_row(
            "Bioenergy & Other",
            evidence_text="Revenue from CHP power generation, heat generation, and gas sales.",
        )
        geographic = classify_segment_row("Europe")
        single = classify_segment_row("Insurance", evidence_text="Non-life insurance premiums.")

        self.assertEqual(SEGMENT_TYPE_MULTI_ACTIVITY, mixed.segment_type)
        self.assertTrue(mixed.needs_review)
        self.assertEqual(SEGMENT_TYPE_GEOGRAPHIC, geographic.segment_type)
        self.assertTrue(geographic.needs_review)
        self.assertEqual(SEGMENT_TYPE_SINGLE_ACTIVITY, single.segment_type)
        self.assertFalse(single.needs_review)

    def test_negative_business_value_requires_reconciliation_review(self) -> None:
        classification = classify_segment_row("Retail", normalized_value=Decimal("-10"))

        self.assertEqual(ROW_TYPE_OTHER_RECONCILIATION, classification.row_type)
        self.assertEqual(SEGMENT_TYPE_MIXED, classification.segment_type)


if __name__ == "__main__":
    unittest.main()
