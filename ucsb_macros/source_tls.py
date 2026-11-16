"""Complete NetNutrition's certificate chain without adding a trust anchor."""
import ssl
from functools import lru_cache
from pathlib import Path

from requests.adapters import HTTPAdapter
from requests.certs import where


@lru_cache(maxsize=1)
def source_context():
    context = ssl.create_default_context(cafile=where())
    # Python 3.13+ enables partial chains by default. Require a trusted root.
    context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
    context.load_verify_locations(cafile=str(Path(__file__).with_name("netnutrition-issuer.pem")))
    return context


class NetNutritionTLS(HTTPAdapter):
    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host, options = super().build_connection_pool_key_attributes(request, verify, cert)
        if verify is True:
            options["ssl_context"] = source_context()
        return host, options
