from pathlib import PurePosixPath
from urllib.parse import urlsplit

from app.core.config import settings


ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class InspectionImageValidationError(ValueError):
    pass


def normalize_cloudfront_image_urls(
    image_urls: list[str],
) -> list[str]:
    if not image_urls:
        raise InspectionImageValidationError(
            "At least one inspection image URL is required"
        )

    allowed_origin = urlsplit(settings.CLOUDFRONT_IMAGE_BASE_URL)
    allowed_host = allowed_origin.hostname
    if allowed_origin.scheme != "https" or allowed_host is None:
        raise RuntimeError(
            "CLOUDFRONT_IMAGE_BASE_URL must be an HTTPS origin"
        )

    path_prefix = settings.CLOUDFRONT_IMAGE_PATH_PREFIX
    if not path_prefix.startswith("/") or not path_prefix.endswith("/"):
        raise RuntimeError(
            "CLOUDFRONT_IMAGE_PATH_PREFIX must start and end with '/'"
        )

    normalized_urls: list[str] = []
    seen_urls: set[str] = set()

    for raw_url in image_urls:
        normalized_url = raw_url.strip()
        if not normalized_url:
            raise InspectionImageValidationError(
                "Inspection image URL must not be blank"
            )

        parsed_url = urlsplit(normalized_url)
        try:
            port = parsed_url.port
        except ValueError as error:
            raise InspectionImageValidationError(
                "Inspection image URL contains an invalid port"
            ) from error

        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != allowed_host
            or parsed_url.username is not None
            or parsed_url.password is not None
            or port is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise InspectionImageValidationError(
                "Inspection image URL must use the configured CloudFront origin"
            )

        if (
            not parsed_url.path.startswith(path_prefix)
            or parsed_url.path == path_prefix
            or ".." in PurePosixPath(parsed_url.path).parts
        ):
            raise InspectionImageValidationError(
                "Inspection image URL must point to an uploaded object"
            )

        extension = PurePosixPath(parsed_url.path).suffix.lower()
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            raise InspectionImageValidationError(
                "Inspection image URL has an unsupported file extension"
            )

        if normalized_url not in seen_urls:
            seen_urls.add(normalized_url)
            normalized_urls.append(normalized_url)

    return normalized_urls
