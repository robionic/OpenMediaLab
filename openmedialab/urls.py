import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeMediaUrl(ValueError):
    """Raised when a media URL is not safe to fetch."""


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com"}
VIMEO_HOSTS = {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}


def _is_public_host(host: str) -> bool:
    try:
        addresses = {ipaddress.ip_address(host.split("%", 1)[0])}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(result[4][0].split("%", 1)[0])
                for result in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError):
            return False
    return bool(addresses) and all(address.is_global for address in addresses)


def validate_media_url(value: str) -> tuple[str, bool]:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError as exc:
        raise UnsafeMediaUrl("Enter a valid HTTP(S) media URL.") from exc

    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise UnsafeMediaUrl("Only public HTTP(S) URLs without embedded credentials are supported.")
    if port not in (None, 80, 443):
        raise UnsafeMediaUrl("Custom ports are not supported.")

    is_video_site = any(
        host == domain or host.endswith("." + domain)
        for domain in YOUTUBE_HOSTS | VIMEO_HOSTS
    )
    if is_video_site:
        if not _is_public_host(host):
            raise UnsafeMediaUrl("The video URL host could not be verified as public.")
        return host, True

    if not _is_public_host(host):
        raise UnsafeMediaUrl("The media URL must resolve to a public internet address.")
    return host, False
