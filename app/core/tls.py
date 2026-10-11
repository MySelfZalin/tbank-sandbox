import ssl
from pathlib import Path


def tinvest_ssl_context(ca_file: Path | None) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if ca_file is not None:
        context.load_verify_locations(cafile=str(ca_file))
    return context
