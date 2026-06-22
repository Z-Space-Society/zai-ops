"""Generate the two signing keys ZAI Auth needs, for local/dev use.

  manage.py generate_keys              # writes to ./keys/, skips existing
  manage.py generate_keys --force      # overwrite

Produces an EC P-256 key (atproto DPoP + client assertion, ES256) and an RSA
key (OIDC id_token, RS256). Keys are written PKCS#8 PEM, mode 0600, into a
git-ignored directory — they are never committed. In production the keys are
provisioned out-of-band (deployment-spec decision); this command just makes
local development one step.
"""

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Generate dev signing keys (EC P-256 for atproto, RSA for OIDC)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            default="keys",
            help="Directory for the PEM files (default: ./keys/).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing key files.",
        )

    def handle(self, *args, **opts):
        directory = Path(opts["dir"])
        if not directory.is_absolute():
            directory = Path(settings.BASE_DIR) / directory
        directory.mkdir(parents=True, exist_ok=True)

        self._write(
            directory / "atproto_ec_private.pem",
            ec.generate_private_key(ec.SECP256R1()),
            opts["force"],
        )
        self._write(
            directory / "oidc_rsa_private.pem",
            rsa.generate_private_key(public_exponent=65537, key_size=2048),
            opts["force"],
        )

    def _write(self, path: Path, key, force: bool):
        if path.exists() and not force:
            self.stdout.write(f"exists, skipping (use --force): {path}")
            return
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        path.write_bytes(pem)
        path.chmod(0o600)
        self.stdout.write(self.style.SUCCESS(f"wrote {path}"))
