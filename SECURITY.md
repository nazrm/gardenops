# Security Policy

Report suspected vulnerabilities through GitHub private vulnerability reporting
for this repository. If that channel is not available, contact a maintainer
through their GitHub profile before sharing exploit details.

Do not open public issues containing exploit details, secrets, production hostnames,
or private deployment data.

## Auth Notes

Production deployments should use session auth with HTTPS, configured passkey
RP/origin values, Redis-backed rate limiting, and a generated MFA secret. Passkey
registration uses short-lived server challenges. Passwordless passkey account
creation is invitation-gated, rejects existing usernames, and does not allow
platform-admin passwordless invitations. Passkey-only accounts require an
explicit `passwordless_recovery` reset token before password authentication can
be restored.

Sensitive session-auth actions treat failed password, TOTP, recovery-code, and
passkey reauthentication as a rejected action and keep the operation available
for a deliberate retry. Garden deletion has a stricter durability boundary:
the deletion and its attributed audit row commit in one transaction, so an
audit insert failure rolls the deletion back. Referenced media files are
unlinked only after that commit; cleanup failures cannot roll back the durable
database decision and must remain observable for operational follow-up.
