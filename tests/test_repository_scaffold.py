from pathlib import Path
import unittest


class RepositoryScaffoldTest(unittest.TestCase):
    def test_required_audit_documents_exist(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        required_docs = [
            "docs/IMPLEMENTATION_PLAN.md",
            "docs/ARCHITECTURE_DECISIONS.md",
            "docs/TESTING_STRATEGY.md",
            "docs/API_CONTRACT.md",
            "docs/REVIEW_WORKFLOW.md",
            "docs/DATA_CONTRACT.md",
            "docs/PARSING_AND_RETRIEVAL.md",
            "docs/LLM_EXTRACTION.md",
        ]

        missing_docs = [
            doc_path for doc_path in required_docs if not (repo_root / doc_path).is_file()
        ]

        self.assertEqual([], missing_docs)


if __name__ == "__main__":
    unittest.main()
