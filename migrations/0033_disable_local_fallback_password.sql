-- The auth-disabled compatibility owner is an internal data principal, not a
-- login account. Remove the historical fixed password without changing data
-- ownership or garden memberships.

UPDATE public.auth_users
SET password_hash = NULL,
    password_auth_disabled = 1,
    must_change_password = 0
WHERE username = '__local_admin__';
