-- Demo and CI only: a login role for the gateway, inside the insert-only role.
-- In staging and production the login role and its password come from
-- Terraform and Secret Manager instead.
CREATE ROLE derva_app LOGIN PASSWORD 'derva-demo' IN ROLE derva_audit_writer;
