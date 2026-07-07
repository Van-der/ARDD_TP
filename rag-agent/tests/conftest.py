def pytest_configure(config):
    # langchain-community FAISS sunset warning — no official standalone package yet.
    # The warning fires at import in main.py, so we filter by message text.
    # Tracked in BUGS_AND_ISSUES.md §D2.
    config.addinivalue_line(
        "filterwarnings",
        "ignore:.*langchain-community.*is being sunset.*:DeprecationWarning",
    )
