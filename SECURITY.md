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
be restored. A passkey-only user may add a backup passkey after passkey-backed
reauthentication, but cannot remove the final passkey until another passkey or
password authentication is available.
First passkey enrollment does not itself unlock protected administrator actions.
The clients immediately authenticate with the newly bound passkey before
resuming protected application reads.
Enrolled editors and viewers also advertise passkey step-up capability so
passwordless users can safely manage backup credentials without a password.

Sensitive session-auth actions treat failed password, TOTP, recovery-code, and
passkey reauthentication as a rejected action and keep the operation available
for a deliberate retry. Garden deletion has a stricter durability boundary:
the deletion and its attributed audit row commit in one transaction, so an
audit insert failure rolls the deletion back. Referenced media files are
unlinked only after that commit; cleanup failures cannot roll back the durable
database decision and must remain observable for operational follow-up.

API mutations reserve a durable audit row before application code runs. The
database-generated audit row ID is the server-only admission and finalization
identity; `X-Request-ID` remains a correlation value and may legitimately be
reused by a client. A final audit write may update only the incomplete row bound
to the current server request context, so correlation-ID reuse cannot overwrite
or capture another request's reservation. If reservation fails, the mutation is
rejected. Routes with stricter audit requirements finalize the reservation in
the same transaction as their domain change; an interrupted ordinary route
leaves an explicit incomplete reservation instead of an unaudited successful
request.
