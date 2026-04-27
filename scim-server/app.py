"""
SCIM 2.0 Service Provider — Flask Application
===============================================
A lightweight, config-driven SCIM server for Entra ID provisioning labs.
One codebase, multiple instances via config files.

Usage:
    python app.py                          # uses config.yaml in current dir
    python app.py --config configs/config-contoso.yaml

Developed for SCIP by Evan H. Yearwood
"""

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import wraps

import yaml
from flask import Flask, Response, g, jsonify, render_template, request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    """Load and validate the YAML config file."""
    with open(path, "r") as f:
        cfg = yaml.safe_load(f)
    required = ["app_name", "port", "bearer_token", "database"]
    for key in required:
        if key not in cfg:
            raise SystemExit(f"[ERROR] Missing required config key: {key}")
    cfg.setdefault("groups_enabled", False)
    return cfg


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DB_PATH: str = ""  # set at startup from config


def get_db() -> sqlite3.Connection:
    """Get a database connection for the current request."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(exc=None):
    """Close the database connection at the end of the request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id              TEXT PRIMARY KEY,
            external_id     TEXT,
            user_name       TEXT UNIQUE NOT NULL,
            given_name      TEXT DEFAULT '',
            family_name     TEXT DEFAULT '',
            display_name    TEXT DEFAULT '',
            email           TEXT DEFAULT '',
            title           TEXT DEFAULT '',
            department      TEXT DEFAULT '',
            active          INTEGER DEFAULT 1,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS groups (
            id              TEXT PRIMARY KEY,
            external_id     TEXT,
            display_name    TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memberships (
            group_id        TEXT NOT NULL,
            user_id         TEXT NOT NULL,
            PRIMARY KEY (group_id, user_id),
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id)  REFERENCES users(id)  ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Auth Middleware
# ---------------------------------------------------------------------------

BEARER_TOKEN: str = ""  # set at startup from config


def require_bearer(f):
    """Decorator that validates the Authorization: Bearer header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return scim_error(401, "Missing or malformed Authorization header")
        token = auth[7:]
        if token != BEARER_TOKEN:
            return scim_error(401, "Invalid bearer token")
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# SCIM Response Helpers
# ---------------------------------------------------------------------------

SCIM_CONTENT_TYPE = "application/scim+json"


def scim_response(data: dict, status: int = 200) -> Response:
    """Return a properly formatted SCIM JSON response."""
    resp = Response(
        json.dumps(data, indent=2),
        status=status,
        content_type=SCIM_CONTENT_TYPE,
    )
    return resp


def scim_error(status: int, detail: str) -> Response:
    """Return a SCIM error response."""
    body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
        "status": str(status),
        "detail": detail,
    }
    return scim_response(body, status)


def now_iso() -> str:
    """Current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# SCIM Resource Serializers
# ---------------------------------------------------------------------------

def user_to_scim(row: sqlite3.Row) -> dict:
    """Convert a database user row to SCIM 2.0 JSON."""
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": row["id"],
        "externalId": row["external_id"] or "",
        "userName": row["user_name"],
        "name": {
            "givenName": row["given_name"],
            "familyName": row["family_name"],
        },
        "displayName": row["display_name"],
        "emails": [
            {"value": row["email"], "type": "work", "primary": True}
        ] if row["email"] else [],
        "title": row["title"],
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {
            "department": row["department"],
        },
        "active": bool(row["active"]),
        "meta": {
            "resourceType": "User",
            "created": row["created_at"],
            "lastModified": row["updated_at"],
            "location": f"{request.url_root}scim/v2/Users/{row['id']}",
        },
    }


def group_to_scim(row: sqlite3.Row, include_members: bool = True) -> dict:
    """Convert a database group row to SCIM 2.0 JSON."""
    result = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "id": row["id"],
        "externalId": row["external_id"] or "",
        "displayName": row["display_name"],
        "meta": {
            "resourceType": "Group",
            "created": row["created_at"],
            "lastModified": row["updated_at"],
            "location": f"{request.url_root}scim/v2/Groups/{row['id']}",
        },
    }
    if include_members:
        db = get_db()
        members = db.execute(
            "SELECT user_id FROM memberships WHERE group_id = ?", (row["id"],)
        ).fetchall()
        result["members"] = [
            {
                "value": m["user_id"],
                "$ref": f"{request.url_root}scim/v2/Users/{m['user_id']}",
                "type": "User",
            }
            for m in members
        ]
    return result


# ---------------------------------------------------------------------------
# SCIM Filter Parser (handles eq and 'and' operators)
# ---------------------------------------------------------------------------

def parse_scim_filter(filter_str: str):
    """
    Parse a simple SCIM filter string.
    Supports: attribute eq "value" [and attribute eq "value"]
    Returns a list of (attribute, value) tuples.
    """
    conditions = []
    if not filter_str:
        return conditions
    parts = filter_str.split(" and ")
    for part in parts:
        part = part.strip()
        tokens = part.split(" ", 2)
        if len(tokens) == 3 and tokens[1].lower() == "eq":
            attr = tokens[0].strip()
            val = tokens[2].strip().strip('"')
            conditions.append((attr, val))
    return conditions


def apply_user_filter(conditions: list) -> str:
    """Convert parsed filter conditions to SQL WHERE clause for users."""
    mapping = {
        "userName": "user_name",
        "externalId": "external_id",
        "emails.value": "email",
        "displayName": "display_name",
        "name.givenName": "given_name",
        "name.familyName": "family_name",
    }
    clauses = []
    values = []
    for attr, val in conditions:
        col = mapping.get(attr)
        if col:
            clauses.append(f"{col} = ?")
            values.append(val)
    if clauses:
        return " AND ".join(clauses), values
    return "1=1", []


def apply_group_filter(conditions: list) -> str:
    """Convert parsed filter conditions to SQL WHERE clause for groups."""
    mapping = {
        "displayName": "display_name",
        "externalId": "external_id",
    }
    clauses = []
    values = []
    for attr, val in conditions:
        col = mapping.get(attr)
        if col:
            clauses.append(f"{col} = ?")
            values.append(val)
    if clauses:
        return " AND ".join(clauses), values
    return "1=1", []


# ---------------------------------------------------------------------------
# Flask App Factory
# ---------------------------------------------------------------------------

def create_app(config: dict) -> Flask:
    """Create and configure the Flask application."""

    global DB_PATH, BEARER_TOKEN
    DB_PATH = config["database"]
    BEARER_TOKEN = config["bearer_token"]

    app = Flask(__name__, template_folder="templates")
    app.config["APP_NAME"] = config["app_name"]
    app.config["GROUPS_ENABLED"] = config.get("groups_enabled", False)

    app.teardown_appcontext(close_db)
    init_db()

    # -------------------------------------------------------------------
    # Health / Root
    # -------------------------------------------------------------------

    @app.route("/")
    def index():
        return jsonify({
            "app": config["app_name"],
            "status": "running",
            "scim_endpoint": "/scim/v2",
            "dashboard": "/dashboard",
        })

    # -------------------------------------------------------------------
    # SCIM Service Provider Configuration (discovery)
    # -------------------------------------------------------------------

    @app.route("/scim/v2/ServiceProviderConfig")
    @require_bearer
    def service_provider_config():
        return scim_response({
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False},
            "filter": {"supported": True, "maxResults": 100},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [
                {
                    "type": "oauthbearertoken",
                    "name": "OAuth Bearer Token",
                    "description": "Authentication via bearer token in Authorization header",
                }
            ],
        })

    @app.route("/scim/v2/Schemas")
    @require_bearer
    def schemas():
        return scim_response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": 0,
            "Resources": [],
        })

    @app.route("/scim/v2/ResourceTypes")
    @require_bearer
    def resource_types():
        types = [
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": "User",
                "name": "User",
                "endpoint": "/scim/v2/Users",
                "schema": "urn:ietf:params:scim:schemas:core:2.0:User",
            }
        ]
        if config.get("groups_enabled"):
            types.append({
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": "Group",
                "name": "Group",
                "endpoint": "/scim/v2/Groups",
                "schema": "urn:ietf:params:scim:schemas:core:2.0:Group",
            })
        return scim_response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": len(types),
            "Resources": types,
        })

    # -------------------------------------------------------------------
    # /Users endpoints
    # -------------------------------------------------------------------

    @app.route("/scim/v2/Users", methods=["GET"])
    @require_bearer
    def list_users():
        db = get_db()
        filter_str = request.args.get("filter", "")
        start = max(int(request.args.get("startIndex", 1)), 1)
        count = min(int(request.args.get("count", 100)), 100)

        conditions = parse_scim_filter(filter_str)
        where, values = apply_user_filter(conditions)

        total = db.execute(f"SELECT COUNT(*) FROM users WHERE {where}", values).fetchone()[0]
        rows = db.execute(
            f"SELECT * FROM users WHERE {where} ORDER BY created_at LIMIT ? OFFSET ?",
            values + [count, start - 1],
        ).fetchall()

        return scim_response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total,
            "startIndex": start,
            "itemsPerPage": len(rows),
            "Resources": [user_to_scim(r) for r in rows],
        })

    @app.route("/scim/v2/Users/<user_id>", methods=["GET"])
    @require_bearer
    def get_user(user_id):
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return scim_error(404, f"User {user_id} not found")
        return scim_response(user_to_scim(row))

    @app.route("/scim/v2/Users", methods=["POST"])
    @require_bearer
    def create_user():
        db = get_db()
        data = request.get_json()
        if not data:
            return scim_error(400, "Request body is required")

        user_name = data.get("userName", "")
        if not user_name:
            return scim_error(400, "userName is required")

        # Check for duplicate
        existing = db.execute(
            "SELECT id FROM users WHERE user_name = ?", (user_name,)
        ).fetchone()
        if existing:
            return scim_error(409, f"User with userName '{user_name}' already exists")

        name = data.get("name", {})
        emails = data.get("emails", [])
        email = emails[0]["value"] if emails else ""
        enterprise = data.get(
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User", {}
        )

        user_id = str(uuid.uuid4())
        ts = now_iso()

        db.execute(
            """INSERT INTO users
               (id, external_id, user_name, given_name, family_name,
                display_name, email, title, department, active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                data.get("externalId", ""),
                user_name,
                name.get("givenName", ""),
                name.get("familyName", ""),
                data.get("displayName", ""),
                email,
                data.get("title", ""),
                enterprise.get("department", ""),
                1 if data.get("active", True) else 0,
                ts,
                ts,
            ),
        )
        db.commit()

        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return scim_response(user_to_scim(row), 201)

    @app.route("/scim/v2/Users/<user_id>", methods=["PATCH"])
    @require_bearer
    def patch_user(user_id):
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return scim_error(404, f"User {user_id} not found")

        data = request.get_json()
        if not data:
            return scim_error(400, "Request body is required")

        operations = data.get("Operations", [])
        for op in operations:
            op_type = op.get("op", "").lower()
            path = op.get("path", "")
            value = op.get("value")

            if op_type == "replace":
                _apply_user_replace(db, user_id, path, value)
            elif op_type == "add":
                _apply_user_replace(db, user_id, path, value)
            elif op_type == "remove":
                _apply_user_remove(db, user_id, path)

        db.execute("UPDATE users SET updated_at = ? WHERE id = ?", (now_iso(), user_id))
        db.commit()

        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return scim_response(user_to_scim(row))

    @app.route("/scim/v2/Users/<user_id>", methods=["PUT"])
    @require_bearer
    def put_user(user_id):
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return scim_error(404, f"User {user_id} not found")

        data = request.get_json()
        if not data:
            return scim_error(400, "Request body is required")

        name = data.get("name", {})
        emails = data.get("emails", [])
        email = emails[0]["value"] if emails else ""
        enterprise = data.get(
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User", {}
        )
        ts = now_iso()

        db.execute(
            """UPDATE users SET
                external_id = ?, user_name = ?, given_name = ?, family_name = ?,
                display_name = ?, email = ?, title = ?, department = ?,
                active = ?, updated_at = ?
               WHERE id = ?""",
            (
                data.get("externalId", ""),
                data.get("userName", row["user_name"]),
                name.get("givenName", ""),
                name.get("familyName", ""),
                data.get("displayName", ""),
                email,
                data.get("title", ""),
                enterprise.get("department", ""),
                1 if data.get("active", True) else 0,
                ts,
                user_id,
            ),
        )
        db.commit()

        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return scim_response(user_to_scim(row))

    @app.route("/scim/v2/Users/<user_id>", methods=["DELETE"])
    @require_bearer
    def delete_user(user_id):
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return scim_error(404, f"User {user_id} not found")
        db.execute("DELETE FROM memberships WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        return Response(status=204)

    # -------------------------------------------------------------------
    # PATCH helpers for Users
    # -------------------------------------------------------------------

    def _apply_user_replace(db, user_id, path, value):
        """Handle a SCIM PATCH replace or add operation on a user."""
        col_map = {
            "userName": "user_name",
            "name.givenName": "given_name",
            "name.familyName": "family_name",
            "displayName": "display_name",
            "title": "title",
            "active": "active",
            "externalId": "external_id",
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department": "department",
        }

        if path and path in col_map:
            db_val = value
            if path == "active":
                db_val = 1 if value in (True, "True", "true") else 0
            db.execute(
                f"UPDATE users SET {col_map[path]} = ? WHERE id = ?",
                (db_val, user_id),
            )
        elif path and path.startswith("emails["):
            if isinstance(value, dict):
                db.execute(
                    "UPDATE users SET email = ? WHERE id = ?",
                    (value.get("value", ""), user_id),
                )
            elif isinstance(value, str):
                db.execute(
                    "UPDATE users SET email = ? WHERE id = ?", (value, user_id)
                )
        elif not path and isinstance(value, dict):
            # Entra sometimes sends replace with no path and a dict of attrs
            for k, v in value.items():
                if k in col_map:
                    db_val = v
                    if k == "active":
                        db_val = 1 if v in (True, "True", "true") else 0
                    db.execute(
                        f"UPDATE users SET {col_map[k]} = ? WHERE id = ?",
                        (db_val, user_id),
                    )
                elif k == "name" and isinstance(v, dict):
                    for nk, nv in v.items():
                        full = f"name.{nk}"
                        if full in col_map:
                            db.execute(
                                f"UPDATE users SET {col_map[full]} = ? WHERE id = ?",
                                (nv, user_id),
                            )
                elif k == "emails" and isinstance(v, list) and v:
                    db.execute(
                        "UPDATE users SET email = ? WHERE id = ?",
                        (v[0].get("value", ""), user_id),
                    )
                elif (
                    k == "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
                    and isinstance(v, dict)
                ):
                    if "department" in v:
                        db.execute(
                            "UPDATE users SET department = ? WHERE id = ?",
                            (v["department"], user_id),
                        )

    def _apply_user_remove(db, user_id, path):
        """Handle a SCIM PATCH remove operation on a user."""
        col_map = {
            "title": "title",
            "displayName": "display_name",
            "externalId": "external_id",
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department": "department",
        }
        if path in col_map:
            db.execute(
                f"UPDATE users SET {col_map[path]} = '' WHERE id = ?", (user_id,)
            )

    # -------------------------------------------------------------------
    # /Groups endpoints (only active if groups_enabled)
    # -------------------------------------------------------------------

    @app.route("/scim/v2/Groups", methods=["GET"])
    @require_bearer
    def list_groups():
        if not config.get("groups_enabled"):
            return scim_error(501, "Groups endpoint is not enabled for this application")
        db = get_db()
        filter_str = request.args.get("filter", "")
        start = max(int(request.args.get("startIndex", 1)), 1)
        count = min(int(request.args.get("count", 100)), 100)

        conditions = parse_scim_filter(filter_str)
        where, values = apply_group_filter(conditions)

        total = db.execute(f"SELECT COUNT(*) FROM groups WHERE {where}", values).fetchone()[0]
        rows = db.execute(
            f"SELECT * FROM groups WHERE {where} ORDER BY created_at LIMIT ? OFFSET ?",
            values + [count, start - 1],
        ).fetchall()

        return scim_response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total,
            "startIndex": start,
            "itemsPerPage": len(rows),
            "Resources": [group_to_scim(r) for r in rows],
        })

    @app.route("/scim/v2/Groups/<group_id>", methods=["GET"])
    @require_bearer
    def get_group(group_id):
        if not config.get("groups_enabled"):
            return scim_error(501, "Groups endpoint is not enabled for this application")
        db = get_db()
        row = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            return scim_error(404, f"Group {group_id} not found")
        return scim_response(group_to_scim(row))

    @app.route("/scim/v2/Groups", methods=["POST"])
    @require_bearer
    def create_group():
        if not config.get("groups_enabled"):
            return scim_error(501, "Groups endpoint is not enabled for this application")
        db = get_db()
        data = request.get_json()
        if not data:
            return scim_error(400, "Request body is required")

        display_name = data.get("displayName", "")
        if not display_name:
            return scim_error(400, "displayName is required")

        group_id = str(uuid.uuid4())
        ts = now_iso()

        db.execute(
            "INSERT INTO groups (id, external_id, display_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (group_id, data.get("externalId", ""), display_name, ts, ts),
        )

        # Handle initial members
        for member in data.get("members", []):
            member_id = member.get("value")
            if member_id:
                user_exists = db.execute(
                    "SELECT id FROM users WHERE id = ?", (member_id,)
                ).fetchone()
                if user_exists:
                    db.execute(
                        "INSERT OR IGNORE INTO memberships (group_id, user_id) VALUES (?, ?)",
                        (group_id, member_id),
                    )
        db.commit()

        row = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        return scim_response(group_to_scim(row), 201)

    @app.route("/scim/v2/Groups/<group_id>", methods=["PATCH"])
    @require_bearer
    def patch_group(group_id):
        if not config.get("groups_enabled"):
            return scim_error(501, "Groups endpoint is not enabled for this application")
        db = get_db()
        row = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            return scim_error(404, f"Group {group_id} not found")

        data = request.get_json()
        operations = data.get("Operations", [])

        for op in operations:
            op_type = op.get("op", "").lower()
            path = op.get("path", "")
            value = op.get("value")

            if op_type == "replace" and path == "displayName":
                db.execute(
                    "UPDATE groups SET display_name = ? WHERE id = ?",
                    (value, group_id),
                )
            elif op_type in ("add", "replace") and path == "members":
                members = value if isinstance(value, list) else [value]
                for m in members:
                    member_id = m.get("value") if isinstance(m, dict) else m
                    if member_id:
                        db.execute(
                            "INSERT OR IGNORE INTO memberships (group_id, user_id) VALUES (?, ?)",
                            (group_id, member_id),
                        )
            elif op_type == "remove" and path and "members[" in path:
                # path format: members[value eq "user-id"]
                member_id = path.split('"')[1] if '"' in path else ""
                if member_id:
                    db.execute(
                        "DELETE FROM memberships WHERE group_id = ? AND user_id = ?",
                        (group_id, member_id),
                    )

        db.execute(
            "UPDATE groups SET updated_at = ? WHERE id = ?", (now_iso(), group_id)
        )
        db.commit()

        row = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        return scim_response(group_to_scim(row))

    @app.route("/scim/v2/Groups/<group_id>", methods=["DELETE"])
    @require_bearer
    def delete_group(group_id):
        if not config.get("groups_enabled"):
            return scim_error(501, "Groups endpoint is not enabled for this application")
        db = get_db()
        row = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            return scim_error(404, f"Group {group_id} not found")
        db.execute("DELETE FROM memberships WHERE group_id = ?", (group_id,))
        db.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        db.commit()
        return Response(status=204)

    # -------------------------------------------------------------------
    # Dashboard
    # -------------------------------------------------------------------

    @app.route("/dashboard")
    def dashboard():
        db = get_db()
        users = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        groups = []
        memberships = {}
        if config.get("groups_enabled"):
            groups = db.execute(
                "SELECT * FROM groups ORDER BY created_at DESC"
            ).fetchall()
            for grp in groups:
                members = db.execute(
                    """SELECT u.user_name, u.display_name, u.active
                       FROM memberships m JOIN users u ON m.user_id = u.id
                       WHERE m.group_id = ?""",
                    (grp["id"],),
                ).fetchall()
                memberships[grp["id"]] = members
        return render_template(
            "dashboard.html",
            app_name=config["app_name"],
            users=users,
            groups=groups,
            memberships=memberships,
            groups_enabled=config.get("groups_enabled", False),
        )

    return app


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SCIM 2.0 Service Provider")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config file (default: config.yaml)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    app = create_app(config)

    print(f"\n{'='*60}")
    print(f"  {config['app_name']} — SCIM Service Provider")
    print(f"  SCIM endpoint:  http://localhost:{config['port']}/scim/v2")
    print(f"  Dashboard:      http://localhost:{config['port']}/dashboard")
    print(f"  Database:       {config['database']}")
    print(f"  Groups:         {'Enabled' if config.get('groups_enabled') else 'Disabled'}")
    print(f"{'='*60}\n")

    app.run(host="0.0.0.0", port=config["port"], debug=True)
