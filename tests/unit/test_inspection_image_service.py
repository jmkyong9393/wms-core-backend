import pytest

from app.services.inspection_image_service import (
    InspectionImageValidationError,
    normalize_cloudfront_image_urls,
)


CLOUDFRONT_ORIGIN = "https://d3j61tpuly7r0p.cloudfront.net"


def test_normalize_cloudfront_image_urls_preserves_order_and_removes_duplicates():
    cover_url = f"{CLOUDFRONT_ORIGIN}/uploads/cover.jpg"
    inside_url = f"{CLOUDFRONT_ORIGIN}/uploads/inside.webp"

    result = normalize_cloudfront_image_urls(
        [cover_url, inside_url, cover_url]
    )

    assert result == [cover_url, inside_url]


@pytest.mark.parametrize(
    "image_urls",
    [
        [],
        [""],
        ["http://d3j61tpuly7r0p.cloudfront.net/uploads/book.jpg"],
        ["https://example.com/uploads/book.jpg"],
        [
            "https://d3j61tpuly7r0p.cloudfront.net/"
            "private/book.jpg"
        ],
        [
            "https://d3j61tpuly7r0p.cloudfront.net/"
            "uploads/../private/book.jpg"
        ],
        ["https://d3j61tpuly7r0p.cloudfront.net/uploads/"],
        [
            "https://user@d3j61tpuly7r0p.cloudfront.net/"
            "uploads/book.jpg"
        ],
        [
            "https://d3j61tpuly7r0p.cloudfront.net:443/"
            "uploads/book.jpg"
        ],
        [
            "https://d3j61tpuly7r0p.cloudfront.net/"
            "uploads/book.jpg?expires=1"
        ],
        [
            "https://d3j61tpuly7r0p.cloudfront.net/"
            "uploads/book.jpg#preview"
        ],
        [
            "https://d3j61tpuly7r0p.cloudfront.net/"
            "uploads/book.pdf"
        ],
    ],
)
def test_normalize_cloudfront_image_urls_rejects_untrusted_urls(
    image_urls,
):
    with pytest.raises(InspectionImageValidationError):
        normalize_cloudfront_image_urls(image_urls)
