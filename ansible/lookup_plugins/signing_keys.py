# The one place a signing key is generated and read (docs/decisions/0011).
#
#   query('signing_keys', 'scn_chat_oauth')                 -> [current, previous?]
#   query('signing_keys', 'corliss_oidc_rsa', type='rsa')   -> RSA 2048 when generated
#   query('signing_keys', 'pds_oauth', generate=false)      -> missing is an error
#
# Each key is a PKCS8 PEM under /root/.zai-secrets: <name>.pem is the current
# key, <name>.previous.pem the one being rotated out (rotate-key.yml writes it).
# They ride the Tier-1 backup of that directory like every other secret.
#
# Use `query`, not `lookup`: `lookup` joins a list into one comma-separated
# string, which would glue two PEMs together.
#
# Values are returned with trailing whitespace stripped. That is what the
# `pipe` lookup this replaces did, so a key file rendered from it before is
# rendered byte-for-byte the same now and its service is not restarted.
#
# Runs on the control node, like every lookup, whatever host the play targets.

import os
import tempfile

from ansible.errors import AnsibleError
from ansible.module_utils.common.text.converters import to_text
from ansible.plugins.lookup import LookupBase
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

DEFAULT_DIR = "/root/.zai-secrets"


def _check(path, text):
    if not text.strip():
        raise AnsibleError(
            f"{path} is empty. A half-written key is not a key: if nothing has "
            "used it yet, delete it and run again to generate a new one."
        )
    try:
        serialization.load_pem_private_key(text.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise AnsibleError(f"{path} is not a PEM private key: {exc}")


def _read(path):
    with open(path, encoding="ascii") as f:
        text = f.read()
    _check(path, text)
    return text.rstrip()


def _generate(path, key_type):
    if key_type == "ec":
        key = ec.generate_private_key(ec.SECP256R1())
    elif key_type == "rsa":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    else:
        raise AnsibleError(f"signing_keys: unknown type {key_type!r} (ec or rsa)")
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    # Written beside the target and renamed into place, so an interrupted run
    # leaves no file rather than an empty one that later reads as a key.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".signing-key-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):
        key_type = kwargs.get("type", "ec")
        generate = kwargs.get("generate", True)
        if isinstance(generate, str):
            generate = generate.lower() not in ("false", "no", "0")
        # _zai_secrets_dir (group_vars) is a plain string, so it needs no
        # templating; the fallback only matters outside a playbook.
        secrets_dir = (
            kwargs.get("dir")
            or (variables or {}).get("_zai_secrets_dir")
            or DEFAULT_DIR
        )

        if len(terms) != 1:
            raise AnsibleError("signing_keys takes exactly one key name")
        name = to_text(terms[0])
        if not name or "/" in name or name.startswith("."):
            raise AnsibleError(f"signing_keys: bad key name {name!r}")

        current = os.path.join(secrets_dir, f"{name}.pem")
        previous = os.path.join(secrets_dir, f"{name}.previous.pem")

        if not os.path.exists(current):
            if not generate:
                raise AnsibleError(
                    f"{current} does not exist, and this key is never generated "
                    "here. Its role creates it (for the PDS: adopted from the "
                    "key it signs with now, or generated on a fresh one)."
                )
            _generate(current, key_type)

        keys = [_read(current)]
        if os.path.exists(previous):
            keys.append(_read(previous))
        return keys
