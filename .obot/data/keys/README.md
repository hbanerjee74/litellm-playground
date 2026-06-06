# Obot keys / credentials directory

This directory is mounted into the Obot container at `/data/keys/` and
holds secrets Obot reads at startup. The **actual files are gitignored**;
only this README is committed so contributors know what to generate.

The setup steps below are the same ones documented in
`docs/superpowers/specs/2026-06-06-mcp-obot-linear-repl-design.md`.

## Files you need to create

### `obot-bootstrap-token` — required

Obot's admin token. Authenticates Studio's installer (or you, in local
mode) for admin REST operations: catalog enable/disable, user listing,
API-key creation, role management.

Format: 64 hex characters (32 bytes from `openssl rand -hex 32`).

Generate from this directory:

```bash
openssl rand -hex 32 > obot-bootstrap-token
chmod 600 obot-bootstrap-token
```

Pass to Obot at startup:

```bash
docker run ... \
  -e OBOT_BOOTSTRAP_TOKEN="$(cat .obot/data/keys/obot-bootstrap-token)" \
  ...
```

**Keep this file stable across container restarts.** Obot persists the
value supplied on first launch. Replacing it later silently invalidates
the previous one and breaks Studio's admin access until updated.

### `obot-encryption.yaml` — production only, NOT used in the sample

Kubernetes-format `EncryptionConfiguration` that encrypts OAuth refresh
tokens and DCR client secrets at rest in Postgres.

**Not configured in the local sample.** Obot v0.22.x's `custom` / `aescbc`
provider has a known issue (the postgres credential store can't find a
transformer for `credentials.obot.obot.ai` even with a correctly-formed
YAML). The sample runs with unencrypted storage; the lock-in #7 production
target is a cloud-KMS provider (`aws` / `gcp` / `azure`) which paths
through different Obot code.

If you want to try the custom provider anyway (e.g., to reproduce the
upstream bug for a report):

```bash
ENC_KEY=$(openssl rand -base64 32)
cat > obot-encryption.yaml <<EOF
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - credentials.obot.obot.ai
      - users.obot.obot.ai
      - identities.obot.obot.ai
      - mcpoauthtokens.obot.obot.ai
      - mcpoauthpendingstates.obot.obot.ai
      - mcpauditlogs.obot.obot.ai
      - policyviolations.obot.obot.ai
      - properties.obot.obot.ai
    providers:
      - aescbc:
          keys:
            - name: vibedata-obot-key-1
              secret: ${ENC_KEY}
      - identity: {}
EOF
chmod 600 obot-encryption.yaml
```

Then add to the Obot docker run:

```bash
  -e OBOT_SERVER_ENCRYPTION_PROVIDER=custom \
  -e OBOT_SERVER_ENCRYPTION_CONFIG_FILE=/data/keys/obot-encryption.yaml \
```

## Wiping and starting over

If you want a fresh Obot install (different bootstrap token, clean PG):

```bash
docker stop obot && docker rm obot
rm -rf .obot/data
mkdir -p .obot/data/keys
# re-generate obot-bootstrap-token per above
# re-run docker run command from the spec
```

The README itself is gitignored against deletion via the `!.obot/data/keys/README.md`
exception in the repo's `.gitignore`.
