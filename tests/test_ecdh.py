from shortauthstrings import urbit_like, emoji


def test_urbit_like() -> None:
    got = urbit_like(bytes([255, 255, 255, 255, 0, 0, 0, 0]))
    want = "fipfes-fipfes-dozzod-dozzod"
    assert got == want


def test_emoji() -> None:
    got = emoji(bytes([255, 48, 73, 25, 7, 88, 99, 190, 0]))
    want = "⚡ 🍔 🍛 🫑 🥭 🍧 🍮 🐋 🍇"
    assert got == want


if __name__ == "__main__":
    test_urbit_like()
    test_emoji()
