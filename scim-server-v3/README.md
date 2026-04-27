# SCIM 2.0 Service Provider — Flask Lab Application

A lightweight, config-driven SCIM 2.0 server for Entra ID provisioning labs. One codebase runs as multiple mock SaaS applications, each with its own configuration, database, and dashboard.

Developed with **Claude.Ai** by **Evan H. Yearwood**.

---

## What This Is

This Flask application acts as a SCIM **Service Provider** — the target application that receives provisioning requests from Entra ID. When Entra creates, updates, or disables a user, this app receives the SCIM request, persists the change to a SQLite database, and displays the result on a web dashboard.

Three pre-built configurations simulate different customer scenarios:

| App | Port | Groups | Use Case |
|-----|------|--------|----------|
| Contoso HR Portal | 5010 | Disabled | User-only provisioning |
| Fabrikam Wiki | 5011 | Enabled | Users + group sync with expression mappings |
| Woodgrove Ticketing | 5012 | Disabled | User-only provisioning |

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
git clone https://github.com/YOUR-USERNAME/scim-server.git
cd scim-server
pip install -r requirements.txt
```

### 2. Copy a config

```bash
cp configs/config-contoso.yaml config.yaml
```

Edit `config.yaml` and change the `bearer_token` to something unique.

### 3. Run

```bash
python app.py
```

The app starts on the port specified in config. Open the dashboard in your browser:

```
http://localhost:5010/dashboard
```

### 4. Run all three apps

Open three terminal windows:

```bash
# Terminal 1
python app.py --config configs/config-contoso.yaml

# Terminal 2
python app.py --config configs/config-fabrikam.yaml

# Terminal 3
python app.py --config configs/config-woodgrove.yaml
```

---

## Project Structure

```
scim-server/
├── app.py                        # Flask SCIM server (all logic in one file)
├── requirements.txt              # Python dependencies
├── config.yaml                   # Active config (gitignored, copy from configs/)
├── configs/
│   ├── config-contoso.yaml       # Contoso HR Portal config
│   ├── config-fabrikam.yaml      # Fabrikam Wiki config
│   └── config-woodgrove.yaml     # Woodgrove Ticketing config
├── templates/
│   └── dashboard.html            # Web dashboard template
└── README.md
```

---

## SCIM Endpoints

All endpoints require `Authorization: Bearer <token>` matching the token in your config.

### Users

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/scim/v2/Users` | List/filter users (supports `filter`, `startIndex`, `count`) |
| GET | `/scim/v2/Users/{id}` | Get a single user |
| POST | `/scim/v2/Users` | Create a user |
| PATCH | `/scim/v2/Users/{id}` | Update user attributes (used for movers and leavers) |
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

### Discovery

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/scim/v2/ServiceProviderConfig` | SCIM capability advertisement |
| GET | `/scim/v2/Schemas` | Schema discovery |
| GET | `/scim/v2/ResourceTypes` | Supported resource types |

---

## Testing with curl

Before connecting to Entra, verify the app works locally:

```bash
# Health check
curl http://localhost:5010/

# Create a user
curl -X POST http://localhost:5010/scim/v2/Users \
  -H "Authorization: Bearer contoso-scim-token-change-me" \
  -H "Content-Type: application/scim+json" \
  -d '{
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
    "userName": "test.user01@yearwood.local",
    "name": {"givenName": "Test", "familyName": "User"},
    "displayName": "Test User 01",
    "emails": [{"value": "test.user01@yearwood.local", "type": "work", "primary": true}],
    "active": true
  }'

# List users
curl http://localhost:5010/scim/v2/Users \
  -H "Authorization: Bearer contoso-scim-token-change-me"

# Filter by userName (this is what Entra sends)
curl "http://localhost:5010/scim/v2/Users?filter=userName%20eq%20%22test.user01%40yearwood.local%22" \
  -H "Authorization: Bearer contoso-scim-token-change-me"

# Disable a user (leaver event)
curl -X PATCH http://localhost:5010/scim/v2/Users/{USER_ID} \
  -H "Authorization: Bearer contoso-scim-token-change-me" \
  -H "Content-Type: application/scim+json" \
  -d '{
    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
    "Operations": [{"op": "replace", "path": "active", "value": false}]
  }'
```

---

## Connecting to Entra ID

### Install the Provisioning Agent

1. In the Entra portal, go to **Entra ID > On-premises provisioning**
2. Download and install the **Microsoft Entra Connect Provisioning Agent** on your Windows Server
3. Register it with your Entra tenant using admin credentials
4. Verify the agent shows as **Active**

### Register the App in Entra

1. Go to **Entra ID > Enterprise Applications > New application**
2. Search for **"On-premises SCIM app"** in the gallery
3. Name it (e.g., "Contoso HR Portal")
4. Go to **Provisioning > Get started**
5. Set Provisioning Mode to **Automatic**
6. Tenant URL: `https://localhost:5010/scim/v2`
7. Secret Token: the bearer token from your config file
8. Click **Test Connection**
9. Configure attribute mappings
10. Set scope to **Sync only assigned users and groups**
11. Assign users/groups and turn provisioning **On**

---

## Resetting Between Reps

For Strip Sets training, reset the environment between reps:

```bash
# Delete all databases
rm -f contoso.db fabrikam.db woodgrove.db

# Databases are automatically recreated on next app start
```

The five baseline test users and two security groups in Entra persist. Only the Flask-side data and the Entra enterprise app configurations get torn down.

---

## License

Built for educational use within SCIP. Use freely for learning and lab environments.
