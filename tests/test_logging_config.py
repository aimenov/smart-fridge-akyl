from backend.app.logging_config import setup_logging


def test_setup_logging_idempotent():
    setup_logging("DEBUG")
    setup_logging("WARNING")
