from src.app import greeting


def test_greeting():
    assert greeting("developer") == "Hello developer"
