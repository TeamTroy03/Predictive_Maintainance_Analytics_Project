"""The Silver-check tool finds the repository from its own location: moving it must not break its defaults."""
from data.generate.checks import silver_check


def test_tool_imports_without_spark_and_resolves_the_repository_root():
    assert (silver_check.REPO / "pyproject.toml").is_file()
    assert (silver_check.REPO / "src" / "data" / "generate" / "checks" / "silver_check.py").is_file()


def test_default_paths_point_at_the_data_and_the_reference_notebook():
    assert (silver_check.DEFAULT_RAW_DIR / "chain.json").is_file()
    assert silver_check.DEFAULT_NOTEBOOK.parent == silver_check.REPO / ".claude" / "reference"
    assert silver_check.DEFAULT_NOTEBOOK.name == "test_pipeline.ipynb"
