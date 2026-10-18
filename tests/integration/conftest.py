"""БД для інтеграційних тестів: TEST_DATABASE_URL."""

import os

import pytest


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    # У CI тест без БД — помилка конфігурації, а не привід мовчки пропустити
    if os.environ.get("CI"):
        pytest.fail("TEST_DATABASE_URL не задано в CI")
    pytest.skip("TEST_DATABASE_URL не задано — інтеграційні тести потребують БД")
