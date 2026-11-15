import base64

from secretary.images import (
    MAX_PICTURES,
    Picture,
    decode_picture,
    decode_pictures,
    describe,
    picture_mime,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 20


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_png_and_jpeg_are_recognised_by_their_bytes():
    assert picture_mime(PNG) == "image/png"
    assert picture_mime(JPEG) == "image/jpeg"
    assert picture_mime(b"GIF89a...") is None


def test_a_data_url_from_the_browser_is_accepted():
    p = decode_picture(f"data:image/jpeg;base64,{b64(JPEG)}")
    assert p == Picture(b64=b64(JPEG), mime="image/jpeg", source="pasted")


def test_junk_is_rejected():
    assert decode_picture(None) is None
    assert decode_picture("") is None
    assert decode_picture("not base64!!") is None
    assert decode_picture(b64(b"%PDF-1.7 not a picture")) is None


def test_a_list_keeps_good_pictures_and_caps_the_count():
    got = decode_pictures([b64(PNG), "junk", *[b64(JPEG)] * 10])
    assert len(got) == MAX_PICTURES - 1  # "junk" took a slot and was dropped
    assert got[0].mime == "image/png"
    assert decode_pictures("not a list") == []


def test_screen_pictures_say_so():
    assert describe([Picture("x", "image/png")]) == "a picture"
    assert describe([Picture("x", "image/png")] * 2) == "2 pictures"
    assert describe([Picture("x", "image/png", "screen")]) == "a picture of their screen"
