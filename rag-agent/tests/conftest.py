def pytest_configure(config):
    # langchain-community itself emits a general "being sunset" deprecation
    # notice on import (unrelated to which vectorstore class is used — this
    # applied to FAISS before the M7 Chroma swap and still applies now).
    # The warning fires at import in main.py, so we filter by message text.
    config.addinivalue_line(
        "filterwarnings",
        "ignore:.*langchain-community.*is being sunset.*:DeprecationWarning",
    )
