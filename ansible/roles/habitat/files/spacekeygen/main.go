// Mint the multibase P-256 private key Habitat wants in HABITAT_SPACE_SIGNING_KEY.
//
// Why this file exists: pear marks --space_signing_key Required with no default
// and no auto-generation, but nothing upstream will produce one. cmd/keygen
// returns encrypt.GenerateKey(), which is the 32-byte base64 shape the three
// OAuth/PDS secrets use; cmd/didgen emits a *secp256k1* key as hex. Neither
// parses. Upstream's own docker-entrypoint.sh generates the three base64 secrets
// and never sets this one, so the published container cannot start without it
// either. This is a gap in their self-hosting path, not in ours.
//
// pear parses the value with atcrypto.ParsePrivateMultibase (indigo), which
// expects multibase base58btc with a multicodec varint prefix, 0x86 0x26 for
// P-256. Rather than hand-roll that encoding, generate with the same library at
// the exact version Habitat pins: this file is copied INTO the habitat source
// tree as a subdirectory of cmd/pear, so it compiles against cmd/pear/go.mod and
// cannot drift from what the server will accept.
//
// Deliberately prints only the key, with no trailing decoration, so the calling
// task can take stdout verbatim.
package main

import (
	"fmt"
	"os"

	"github.com/bluesky-social/indigo/atproto/atcrypto"
)

func main() {
	key, err := atcrypto.GeneratePrivateKeyP256()
	if err != nil {
		fmt.Fprintf(os.Stderr, "generate P-256 key: %v\n", err)
		os.Exit(1)
	}
	fmt.Print(key.Multibase())
}
