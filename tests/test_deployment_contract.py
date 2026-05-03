from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTest(unittest.TestCase):
    def test_streamlit_cloud_entrypoint_and_dependencies_are_present(self) -> None:
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")

        self.assertTrue((PROJECT_ROOT / "streamlit_app.py").is_file())
        self.assertIn("streamlit", requirements)
        self.assertIn("pydantic", requirements)
        self.assertIn("PyMuPDF", requirements)
        self.assertIn("pdfplumber", requirements)

    def test_demo_environment_defaults_do_not_force_optional_arbitration(self) -> None:
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        run_script = (PROJECT_ROOT / "scripts/run_streamlit_anthropic.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("RSE_ENABLE_ARBITRATION=false", env_example)
        self.assertIn('RSE_ENABLE_ARBITRATION:-false', run_script)
        self.assertNotIn("claude-opus", env_example)
        self.assertNotIn("claude-opus", run_script)

    def test_repository_uses_readme_landing_page_without_github_pages(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("GitHub Repository", readme)
        self.assertFalse((PROJECT_ROOT / ".github/workflows/pages.yml").exists())
        self.assertFalse((PROJECT_ROOT / "docs/.nojekyll").exists())
        self.assertFalse((PROJECT_ROOT / "docs/index.html").exists())
        self.assertTrue((PROJECT_ROOT / "docs/README.md").is_file())

    def test_readme_points_to_review_gate_and_deployment_docs(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Block final export", readme)
        self.assertIn("Streamlit Community Cloud", readme)
        self.assertIn("docs/ARCHITECTURE_DECISIONS.md", readme)
        self.assertIn("docs/STREAMLIT_COMMUNITY_CLOUD.md", readme)


if __name__ == "__main__":
    unittest.main()
