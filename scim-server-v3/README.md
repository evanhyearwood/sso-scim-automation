# SSO + SCIM Service Provider — Flask Lab Application

A lightweight, config-driven SSO and SCIM 2.0 server for Entra ID labs. One codebase runs as multiple mock SaaS applications, each with its own SSO protocol, SCIM provisioning, database, and dashboard.

Each app functions as a real **SAML Service Provider** or **OIDC Relying Party** that receives and processes authentication responses from Entra ID, and simultaneously as a **SCIM Service Provider** that receives provisioning requests through the Entra provisioning agent.

Developed by **Evan H. Yearwood**.

---

## What This Is

Three mock SaaS applications, each simulating a different customer onboarding scenario:

| App | Port | SSO Protocol | SCIM Groups | Use Case |
|-----|------|-------------|-------------|----------|
| Contoso HR Portal | 5010 | SAML 2.0 | Disabled | SAML SSO + user-only provisioning |
| Fabrikam Wiki | 5011 | SAML 2.0 | Enabled | SAML SSO + users and group sync |
| Woodgrove Ticketing | 5012 | OIDC | Disabled | OIDC SSO + user-only provisioning |

**SSO side:** Each app has a login page, processes real SAML assertions or OIDC tokens from Entra, extracts claims, and displays them on a profile page.

**SCIM side:** Each app receives provisioning requests from Entra (via the on-premises provisioning agent), persists users and groups to SQLite, and displays them on a dashboard with a full activity log.

---

## Prerequisites

- Python 3.10 or later
- pip
- A Windows machine for the Entra provisioning agent (Windows Server 2016+ or Windows 10/11)
- An Entra ID tenant with P1 or P2 licensing

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/ADD-YOUR-USERNAME-HERE/SSO-SCIM-Automation.git
cd SSO-SCIM-Automation/scim-server
pip install -r requirements.txt
```

### 2. Run all three apps

**Option A — PowerShell launcher (recommended):**

```powershell
.\Start-SCIMApps.ps1
```

This starts all three apps in separate windows and opens dashboards in your browser.

**Option B — Manual (three terminals):**

```bash
# Terminal 1
python app.py --config configs/config-contoso.yaml

# Terminal 2
python app.py --config configs/config-fabrikam.yaml

# Terminal 3
python app.py --config configs/config-woodgrove.yaml
```

### 3. Create a desktop shortcut

```powershell
.\Start-SCIMApps.ps1 -CreateShortcut
```

---

## Project Structure

```
scim-server/
├── app.py                        # Flask SSO + SCIM server
├── requirements.txt              # Python dependencies
├── Start-SCIMApps.ps1            # PowerShell launcher + shortcut creator
├── configs/
│   ├── config-contoso.yaml       # Contoso HR Portal (SAML + SCIM)
│   ├── config-fabrikam.yaml      # Fabrikam Wiki (SAML + SCIM + groups)
│   └── config-woodgrove.yaml     # Woodgrove Ticketing (OIDC + SCIM)
├── metadata/
│   └── README.md                 # Place Entra Federation Metadata XMLs here
├── templates/
│   ├── dashboard.html            # SCIM dashboard with activity log
│   ├── login.html                # SSO login landing page
│   ├── sso_profile.html          # Post-authentication claims display
│   └── sso_error.html            # SSO error page
├── .gitignore
└── README.md
```

---

## SSO Endpoints

### SAML (Contoso HR Portal, Fabrikam Wiki)

| Endpoint | Description |
|----------|-------------|
| `/login` | Landing page with "Sign in with Microsoft Entra ID" button |
| `/saml/login` | Builds AuthnRequest and redirects to Entra SSO URL |
| `/saml/acs` | Receives SAML assertion POST, parses claims, displays profile |
| `/saml/metadata` | Serves SP metadata XML for Entra configuration |

### OIDC (Woodgrove Ticketing)

| Endpoint | Description |
|----------|-------------|
| `/login` | Landing page with "Sign in with Microsoft Entra ID" button |
| `/auth/login` | Generates PKCE challenge and redirects to Entra authorize endpoint |
| `/auth/callback` | Receives auth code, exchanges for tokens, displays claims |

---

## SCIM Endpoints

All SCIM endpoints require `Authorization: Bearer <token>` matching the token in your config.

### Users

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/scim/v2/Users` | List/filter users (supports `filter`, `startIndex`, `count`) |
| GET | `/scim/v2/Users/{id}` | Get a single user |
| POST | `/scim/v2/Users` | Create a user |
| PATCH | `/scim/v2/Users/{id}` | Update user attributes (movers and leavers) |
| PUT | `/scim/v2/Users/{id}` | Full replace of a user |
| DELETE | `/scim/v2/Users/{id}` | Hard delete a user |

### Groups (only when `groups_enabled: true`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/scim/v2/Groups` | List/filter groups |
| GET | `/scim/v2/Groups/{id}` | Get a single group with members |
| POST | `/scim/v2/Groups` | Create a group |
| PATCH | `/scim/v2/Groups/{id}` | Update group name or membership |
| DELETE | `/scim/v2/Groups/{id}` | Delete a group |

---

## Configuring SSO with Entra ID

### SAML Apps (Contoso, Fabrikam)

1. In Entra, create a **non-gallery enterprise app**
2. Go to **Single sign-on > SAML**
3. Set **Entity ID** to: `http://localhost:5010/saml/metadata` (or 5011 for Fabrikam)
4. Set **Reply URL (ACS)** to: `http://localhost:5010/saml/acs` (or 5011)
5. Set **Sign-on URL** to: `http://localhost:5010/login` (or 5011)
6. Configure **User Attributes & Claims** per the intake template
7. Download the **Federation Metadata XML** from the SAML Signing Certificate section
8. Save the metadata file to: `metadata/contoso-idp-metadata.xml` (or `fabrikam-idp-metadata.xml`)
9. Assign users/groups to the enterprise app
10. Test by navigating to `http://localhost:5010/login` and clicking "Sign in with Microsoft Entra ID"

### OIDC App (Woodgrove)

1. In Entra, create an **App Registration** named "Woodgrove Ticketing"
2. Set supported account type to **Single tenant**
3. Set **Redirect URI** to: `http://localhost:5012/auth/callback` (platform: Web)
4. Enable **PKCE** under Authentication
5. Add **API permissions**: Microsoft Graph > Delegated > User.Read. Grant admin consent.
6. Copy the **Application (client) ID** and **Directory (tenant) ID**
7. Update `configs/config-woodgrove.yaml` with the client ID and tenant ID
8. Test by navigating to `http://localhost:5012/login`

---

## Configuring SCIM with Entra ID

### Install the Provisioning Agent

1. In Entra, create an enterprise app using the **"On-premises SCIM app"** gallery template
2. Go to **Provisioning > Get started**, set mode to **Automatic**
3. Under **On-premises Connectivity**, download and install the provisioning agent on your Windows Server
4. Register the agent with your Entra tenant

### Connect to the Flask App

1. **Tenant URL**: `https://localhost:5010/scim/v2` (or 5011, 5012)
2. **Secret Token**: the bearer token from your config file
3. Click **Test Connection**
4. Configure **attribute mappings**
5. Set scope to **Sync only assigned users and groups**
6. Assign users/groups and set provisioning to **On**

---

## Resetting Between Reps

```bash
# Delete all databases
rm -f contoso.db fabrikam.db woodgrove.db

# Databases are automatically recreated on next app start
```

In Entra, delete the enterprise apps and app registrations created during the rep. Retain the baseline test users and security groups.

---

## License

Built for educational use. Use freely for learning and lab environments.
