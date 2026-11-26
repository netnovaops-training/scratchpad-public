#!/usr/bin/env bash
# make_vapid_key.sh - Novashare E2E Step 4 (2026-11-24): make the VAPID key pair ONCE and keep the private key in
# Proton Pass ("NUC services"). Run on the NUC:  bash ~/sandbox/novashare-push/make_vapid_key.sh
#
# The private key (base64url of the raw 32-byte P-256 scalar, 43 characters) goes from python straight into a shell
# variable, then through stdin into `pass item create` (never a file, an argument or the screen). It is read back from
# Pass and compared; only lengths, "match" and an 8-hex fingerprint of the PUBLIC key are printed. If the item already
# exists, nothing new is made (a second key would break every subscription): the script only checks it.
# Intake reads it through ~/bin/dc + secrets.refs as VAPID_PRIVATE_KEY (README.md has the line to add).
set -euo pipefail
VAULT="NUC services"
TITLE="NUC VAPID_PRIVATE_KEY novashare push (2026-11-24)"
PASS=/home/user/bin/pass

pubfp() {   # stdin: the private key; stdout: first 8 hex of sha256(uncompressed public point). Never the key.
  python3 -c 'import base64,hashlib,sys
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
r = sys.stdin.read().strip(); k = base64.urlsafe_b64decode(r + "=" * (-len(r) % 4))
p = ec.derive_private_key(int.from_bytes(k, "big"), ec.SECP256R1()).public_key().public_bytes(
    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
print(hashlib.sha256(p).hexdigest()[:8])'
}

BACK=$($PASS item view --vault-name "$VAULT" --item-title "$TITLE" --field password 2>/dev/null | tr -cd 'A-Za-z0-9_-' || true)
if [ -n "$BACK" ]; then
  echo "already in Pass (length ${#BACK}, want 43): not made again"
  echo "public key fingerprint: $(printf %s "$BACK" | pubfp)"
  unset BACK; exit 0
fi

KEY=$(python3 -c 'import base64
from cryptography.hazmat.primitives.asymmetric import ec
k = ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value.to_bytes(32, "big")
print(base64.urlsafe_b64encode(k).rstrip(b"=").decode())' | tr -cd 'A-Za-z0-9_-')
echo "key made: length ${#KEY} (want 43)"
[ ${#KEY} = 43 ] || { echo "wrong length: stopped, nothing saved"; exit 1; }
KEY="$KEY" T="$TITLE" python3 -c 'import json,os; print(json.dumps({"title":os.environ["T"],"username":"novashare-push","email":None,"password":os.environ["KEY"],"totp_uri":None,"urls":[],"extra_fields":[]}))' \
  | $PASS item create login --vault-name "$VAULT" --from-template - >/dev/null 2>/tmp/vapid_err.txt \
  && echo "saved to Pass ($VAULT)" || { echo "Pass save FAILED:"; grep -v -E '[A-Za-z0-9_-]{40,}' /tmp/vapid_err.txt | head -3; rm -f /tmp/vapid_err.txt; unset KEY; exit 1; }
rm -f /tmp/vapid_err.txt
BACK=$($PASS item view --vault-name "$VAULT" --item-title "$TITLE" --field password 2>/dev/null | tr -cd 'A-Za-z0-9_-' || true)
[ "$BACK" = "$KEY" ] && echo "Pass read-back matches (length ${#BACK})" || { echo "Pass read-back DIFFERS (length ${#BACK})"; unset KEY BACK; exit 1; }
echo "public key fingerprint: $(printf %s "$KEY" | pubfp)"
unset KEY BACK
echo "secrets.refs line: VAPID_PRIVATE_KEY=pass://$VAULT/$TITLE/password"
