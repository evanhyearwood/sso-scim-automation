# Customer Intake Template — SaaS SSO & SCIM Onboarding

Use this template to gather requirements before configuring any enterprise app in Entra ID.

---

## General Information

| Field | Value |
|-------|-------|
| Customer Name | |
| Application Name | |
| Date of Request | |
| Requested By | |
| Priority | |

---

## SSO Configuration

| Field | Value |
|-------|-------|
| SSO Protocol Requested | SAML 2.0 / OIDC |
| SP Entity ID (SAML) | |
| ACS URL / Reply URL (SAML) | |
| Sign-on URL (SAML) | |
| Redirect URI (OIDC) | |
| Supported Account Type (OIDC) | Single tenant / Multi-tenant |
| Client Authentication (OIDC) | Client secret / PKCE (public client) |
| NameID Format Expected | emailAddress / UPN / persistent |
| NameID Source Attribute | user.mail / user.userprincipalname / user.objectid |

---

## Required Claims / Attributes

| Entra Source Attribute | Claim Name / Target Attribute |
|------------------------|-------------------------------|
| | |
| | |
| | |
| | |

---

## Group Claims

| Field | Value |
|-------|-------|
| Group Claim Required | Yes / No |
| Group Claim Source | Security groups / All groups |
| Group Claim Format | Group ID / Display Name |
| Group-to-Role Mapping | (describe mapping if applicable) |

---

## SCIM Provisioning

| Field | Value |
|-------|-------|
| SCIM Provisioning Required | Yes / No |
| SCIM Connector Type | On-premises SCIM app (via provisioning agent) / Entra gallery / Custom non-gallery |
| SCIM Tenant URL | |
| SCIM Authentication | Bearer token |
| Secret Token | (stored securely, not in this document) |

---

## SCIM Attribute Mapping Scope

| Entra Source Attribute | SCIM Target Attribute |
|------------------------|-----------------------|
| | |
| | |
| | |
| | |

---

## Group Provisioning

| Field | Value |
|-------|-------|
| Group Provisioning Needed | Yes / No |
| Group Attribute Mappings | displayName → displayName, objectId → externalId, members → members |

---

## Deprovisioning Behavior

| Field | Value |
|-------|-------|
| When user is unassigned | Disable account / Delete account / Disable + remove group membership |
| When user is deleted from Entra | Hard delete in target app |

---

## Notes

(Any additional context, constraints, or dependencies)
