# JML SCIM Pipeline W/ Failure Recovery
**Yearwood.Local | Active Directory + Entra ID + Okta | PowerShell | SCIM | Entra ID Governance**

Hi,

Here is a hands-on IAM lab where I build the complete Joiner-Mover-Leaver identity lifecycle across a hybrid Active Directory, Entra ID, and Okta environment with SCIM provisioning to downstream applications. The pipeline is then systematically broken at every stage to document failure modes, root cause analysis, and recovery procedures.

---

## Table of Contents
1. [Lab Overview](#lab-overview)
2. [Environment](#environment)
3. [Architecture](#architecture)
4. [Setup Phases](#setup-phases)
    - [Setup Phase 0 — Entra ID P2 Licensing and Tenant Configuration](#setup-phase-0--entra-id-p2-licensing-and-tenant-configuration)
    - [Setup Phase 1 — HR System Simulator (Flask App)](#setup-phase-1--hr-system-simulator-flask-app)
    - [Setup Phase 2 — Downstream SCIM Applications](#setup-phase-2--downstream-scim-applications)
    - [Setup Phase 3 — Entra Connect Sync Configuration](#setup-phase-3--entra-connect-sync-configuration)
    - [Setup Phase 4 — Dynamic Groups and Group-Based Licensing](#setup-phase-4--dynamic-groups-and-group-based-licensing)
    - [Setup Phase 5 — SCIM Provisioning Configuration](#setup-phase-5--scim-provisioning-configuration)
    - [Setup Phase 6 — Lifecycle Workflows (Entra ID Governance)](#setup-phase-6--lifecycle-workflows-entra-id-governance)
    - [Setup Phase 7 — Conditional Access and MFA Policies](#setup-phase-7--conditional-access-and-mfa-policies)
5. [Pipeline Phases](#pipeline-phases)
    - [Phase 1 — Joiner Provisioning](#phase-1--joiner-provisioning)
    - [Phase 2 — Post-Provisioning Verification](#phase-2--post-provisioning-verification)
    - [Phase 3 — Mover Provisioning](#phase-3--mover-provisioning)
    - [Phase 4 — Leaver Offboarding](#phase-4--leaver-offboarding)
6. [Failure Scenarios](#failure-scenarios)
    - [Joiner Failures (Scenarios 1-6)](#joiner-failures-scenarios-1-6)
    - [Mover Failures (Scenarios 7-9)](#mover-failures-scenarios-7-9)
    - [Leaver Failures (Scenarios 10-13)](#leaver-failures-scenarios-10-13)
7. [Compliance Detection Scripts](#compliance-detection-scripts)
8. [Deliverables](#deliverables)
9. [Key Lessons Learned](#key-lessons-learned)
10. [What I'd Do Differently in Production](#what-id-do-differently-in-production)

---

## Lab Overview

The goal of this lab is to build a production-realistic JML identity pipeline with SCIM provisioning to downstream applications, then systematically break it at every stage and document the failure mode, diagnosis, and recovery.

- **Joiners** — HR trigger creates user in AD, Entra Connect syncs to Entra ID, dynamic groups evaluate attributes and assign group membership, group-based licensing assigns M365 licenses, SCIM provisions accounts in downstream apps, lifecycle workflow generates temporary access pass for Day 1
- **Movers** — role change updates attributes in AD, Entra Connect syncs changes, dynamic group membership re-evaluates (drops old groups, joins new groups), license assignment adjusts, SCIM updates role and permissions in downstream apps
- **Leavers** — termination trigger fires lifecycle workflow, account disabled, group memberships removed, sessions revoked, licenses pulled, SCIM deprovisions from downstream apps

Active Directory is the **source of truth for identity**. Entra ID is the **cloud identity and governance layer**. Okta remains the **access layer for SAML applications**. SCIM handles **downstream app provisioning**. Every change flows through AD first.

### What makes this lab different

This is not a build-and-screenshot lab. Each pipeline stage is intentionally broken, diagnosed using production tools (sign-in logs, audit logs, provisioning logs), classified by root cause type (Process, Automation, or Detection gap), and documented with the prevention fix. The deliverables are operational runbooks and troubleshooting guides, not just configuration walkthroughs.

---

## Environment

| Component | Detail |
|---|---|
| Domain | `yearwood.local` |
| Server | Windows Server VM (ARM) |
| Shell | PowerShell 7 |
| AD module | RSAT ActiveDirectory |
| Entra ID | P2 licensing |
| Entra Connect | Hybrid sync (AD → Entra ID) |
| Okta org | Developer tenant |
| Okta auth | Certificate-based `private_key_jwt` |
| SCIM apps | 3 downstream applications (see Setup Phase 2) |
| SAML apps | Contoso HR Portal, Fabrikam Wiki, Woodgrove Ticketing (Flask, hosted on `yearwood.local`) |
| Governance | Entra ID Governance (lifecycle workflows, access reviews) |

### AD Structure
```
yearwood.local
└── _NA
    ├── Users              ← active accounts
    ├── DisabledUsers      ← leavers moved here
    ├── Groups             ← role-based security groups
    ├── Admins
    ├── ServiceAccounts
    └── Workstations
```

### RBAC Matrix

<!-- TODO: Insert RBAC matrix screenshot showing role-to-group-to-app mapping -->

Group membership in AD drives dynamic group evaluation in Entra ID. Dynamic groups drive license assignment and SCIM provisioning. No direct user-to-app assignments anywhere in the system.

---

## Architecture

```
HR Simulator (Flask App)
     │
     ▼
AD Account Created (New-ADUser / Set-ADUser / Disable-ADAccount)
     │
     ▼
Entra Connect Sync
     │
     ▼
Entra ID User Object (attributes populate)
     │
     ├── Dynamic Groups evaluate attributes
     │        │
     │        ├── Group-Based Licensing assigns M365 licenses
     │        │
     │        └── SCIM Provisioning pushes accounts to downstream apps
     │                 │
     │                 ├── App 1 — attribute mapping + transformation
     │                 ├── App 2 — attribute mapping + transformation
     │                 └── App 3 — attribute mapping + transformation
     │
     ├── Lifecycle Workflows (Entra ID Governance)
     │        │
     │        ├── Joiner: generate temporary access pass, send welcome notification
     │        └── Leaver: disable account, remove groups, revoke sessions
     │
     ├── Conditional Access Policies evaluate at sign-in
     │        │
     │        ├── Require MFA for all cloud apps
     │        ├── Require compliant device for sensitive apps
     │        └── Block sign-in from untrusted locations
     │
     └── Okta AD Agent (pull model)
              │
              └── Okta Universal Directory → SAML app access
```

---

## Setup Phases

### Setup Phase 0 — Entra ID P2 Licensing and Tenant Configuration

Before any pipeline work begins, the tenant must be configured with the correct licensing and base settings.

**Objective:** Activate Entra ID P2 features required for dynamic groups, Conditional Access, lifecycle workflows, and access reviews.

**Tasks:**
- Activate Entra ID P2 trial or paid license on the tenant
- Verify the following features are available in the Entra admin center:
    - Dynamic group creation (Identity → Groups)
    - Conditional Access policy creation (Protection → Conditional Access)
    - Lifecycle Workflows (Identity Governance → Lifecycle Workflows)
    - Access Reviews (Identity Governance → Access Reviews)
    - Entitlement Management (Identity Governance → Entitlement Management)
- Configure tenant-level settings:
    - Default user permissions
    - External collaboration settings
    - Password protection settings
- Assign P2 licenses to test users as they are created

**Verification:** Navigate to each feature listed above and confirm it is accessible without licensing errors.

---

### Setup Phase 1 — HR System Simulator (Flask App)

In production, the identity pipeline begins with an HR system (Workday, BambooHR, Rippling). This lab simulates that trigger with a Flask application that serves as the source of truth for employee data.

**Objective:** Build a lightweight HR portal that allows you to create, update, and terminate employees, outputting the data in a format your provisioning scripts consume.

**Tasks:**
- Build a Flask app with a simple web UI:
    - **New Hire form:** FirstName, LastName, Department, JobTitle, Manager, EmployeeID, StartDate
    - **Role Change form:** EmployeeID, NewDepartment, NewJobTitle, EffectiveDate
    - **Termination form:** EmployeeID, TerminationDate, TerminationType (voluntary/involuntary)
- Each form submission writes a record to a CSV file in the format your `Invoke-JMLProvisioning.ps1` master script expects (Joiners.csv, Movers.csv, Leavers.csv)
- The app enforces required fields and validates department values against a canonical list (this prevents the attribute mismatch scenarios you will intentionally create in the failure phase)

**Why build this instead of just editing CSVs manually:**
- Students in TotalThreat will need to understand that the pipeline starts with an HR trigger, not a CSV file
- The Flask app makes the lab feel like a real workflow rather than a scripting exercise
- The canonical department list introduces the concept of attribute governance — the same concept that prevents dynamic group mismatches in production

**Verification:** Submit a new hire through the Flask app. Confirm the CSV is generated with the correct schema. Run `Invoke-JMLProvisioning.ps1` against the CSV and confirm the AD account is created.

---

### Setup Phase 2 — Downstream SCIM Applications

SCIM provisioning needs apps on the receiving end. This phase sets up three downstream applications that Entra ID will provision users into.

**Objective:** Configure three applications that accept SCIM pushes from Entra ID, each with different attribute requirements to create realistic mapping scenarios.

**Option A — Entra Gallery Apps (Recommended for speed)**
- Use sandbox or trial accounts for three gallery apps that support SCIM (examples: Dropbox, Slack sandbox, Box, or any app with SCIM support in the Entra gallery)
- Each app should have different required attributes to create realistic mapping variation

**Option B — Custom Flask SCIM Endpoints (Recommended for depth)**
- Build three Flask apps that implement the SCIM 2.0 spec (Users endpoint)
- Each app has a different schema:
    - **App 1 (Clinical Platform):** Requires `externalId`, `department`, `role`. Expects department in lowercase with underscores (e.g., `clinical_operations`)
    - **App 2 (Analytics Dashboard):** Requires `externalId`, `email`, `jobTitle`. Expects jobTitle exactly as received
    - **App 3 (Internal Wiki):** Requires `externalId`, `displayName`. Minimal required fields
- Each app stores provisioned users in a local SQLite database so you can query and verify what was received

**Why different schemas matter:**
- In production, every SaaS app has different attribute requirements and format expectations
- The attribute mismatch between what Entra sends and what the app expects is the single most common SCIM failure
- Building three apps with different schemas creates the realistic conditions for failure scenarios 3, 4, 5, and 9

**Verification:** Manually POST a SCIM user payload to each app endpoint. Confirm the user is created and stored correctly.

---

### Setup Phase 3 — Entra Connect Sync Configuration

Entra Connect bridges on-prem AD to cloud Entra ID. This phase ensures the sync pipeline is working before building anything on top of it.

**Objective:** Configure and verify Entra Connect sync between yearwood.local and your Entra ID tenant.

**Tasks:**
- Install or verify Entra Connect on the Windows Server VM
- Configure sync scope (which OUs sync to Entra)
- Configure attribute mapping (ensure department, jobTitle, manager, employeeID all sync)
- Run an initial full sync and verify users appear in Entra ID with correct attributes
- Document the sync cycle interval (default 30 minutes)
- Verify the health dashboard in Entra admin center shows healthy sync status

**Key settings to document:**
- Which OUs are in scope for sync
- Which attributes are mapped
- Whether password hash sync or pass-through authentication is configured
- The service account used for sync and its credential expiration

**Verification:** Create a test user in AD with all required attributes. Wait for sync cycle (or force a delta sync). Confirm the user appears in Entra ID with matching attributes.

---

### Setup Phase 4 — Dynamic Groups and Group-Based Licensing

Dynamic groups are the automation engine of the pipeline. Attributes drive group membership. Group membership drives everything else.

**Objective:** Create dynamic groups that automatically assign users to the correct access tier based on their role attributes, and configure group-based licensing so licenses are assigned automatically.

**Tasks:**
- Create dynamic groups for each role in the RBAC matrix:
    - Example: `DG_Clinical_Coordinator` with rule `(user.department -eq "Clinical") and (user.jobTitle -contains "Coordinator")`
    - Example: `DG_Cloud_Engineer` with rule `(user.department -eq "Engineering") and (user.jobTitle -contains "Engineer")`
    - Create at least 4 dynamic groups covering different roles
- For each dynamic group, document:
    - The rule syntax
    - The business justification (why this group exists, what access it grants)
    - Which RBAC matrix entry it maps to
- Configure group-based licensing:
    - Assign M365 E5 (or appropriate SKU) to each dynamic group
    - Verify that when a user joins the group, the license is assigned automatically
- Verify dynamic group evaluation:
    - Create a test user with matching attributes
    - Confirm they are added to the correct dynamic group within the evaluation window
    - Change their department attribute
    - Confirm they drop from the old group and join the new group

**Verification:** Create a user in AD with department "Clinical" and jobTitle "Care Coordinator". After Entra Connect sync, confirm the user lands in `DG_Clinical_Coordinator` and receives the M365 license automatically.

---

### Setup Phase 5 — SCIM Provisioning Configuration

This phase connects Entra ID to the downstream apps via SCIM and configures the attribute mappings.

**Objective:** Configure SCIM provisioning from Entra ID to each downstream app with correct attribute mappings and transformation rules.

**Tasks:**
- For each of the three downstream apps:
    - Add the app in Entra ID → Enterprise Applications
    - Enable provisioning and configure the SCIM endpoint URL and authentication
    - Configure attribute mapping:
        - Map `displayName`, `mail`, `department`, `jobTitle` from Entra to app fields
        - Map `userPrincipalName` or `mail` to `externalId`
        - Add transformation rules where the app expects a different format
    - Assign the corresponding dynamic group to the app (so only users in the correct role get provisioned)
- Document the three SCIM layers for each app:
    - Layer 1: Source attributes (what Entra ID holds)
    - Layer 2: Mapping configuration (what transformations are applied)
    - Layer 3: App schema (what the app requires and in what format)
- Run initial provisioning sync and verify all in-scope users are pushed successfully
- Review provisioning logs for any errors

**Pre-provisioning checklist (complete for each app):**
- [ ] What attributes are required by the app?
- [ ] What format does each attribute need to be in?
- [ ] Do any attributes need transformation rules?
- [ ] Are there existing users in the app that need to be linked?

**Verification:** Confirm provisioning logs show successful push for all in-scope users. Log into each downstream app and verify user accounts exist with correct attributes.

---

### Setup Phase 6 — Lifecycle Workflows (Entra ID Governance)

Lifecycle workflows automate the actions that need to happen at specific points in the employee lifecycle without manual intervention.

**Objective:** Create automated onboarding and offboarding workflows triggered by employee lifecycle attributes.

**Tasks:**
- Create a **Joiner workflow** (pre-hire):
    - Trigger: `employeeHireDate` attribute, execute 1 day before start date
    - Actions:
        - Add user to onboarding group
        - Generate temporary access pass
        - Send welcome email notification (if configured)
    - Configure the workflow to run on a daily schedule
- Create a **Leaver workflow** (offboarding):
    - Trigger: `employeeLeaveDateTime` attribute, execute on leave date
    - Actions:
        - Disable user account
        - Remove user from all groups
        - Revoke all sign-in sessions
    - Configure the workflow to run on a daily schedule
- Create a **Mover workflow** (optional, if supported):
    - Trigger: attribute change on `department` or `jobTitle`
    - Actions: send notification to IT for review
- For each workflow, document:
    - The trigger condition
    - The actions executed
    - The expected execution log entry
    - What happens if one action fails (does the workflow continue or stop?)

**Verification:** Set `employeeHireDate` on a test user to tomorrow. Wait for the workflow to trigger. Confirm the user was added to the onboarding group and a temporary access pass was generated. Check the lifecycle workflow execution log for timestamps and action results.

---

### Setup Phase 7 — Conditional Access and MFA Policies

Conditional Access policies control who gets in and under what conditions. These policies interact with the pipeline because they evaluate at authentication time.

**Objective:** Create a baseline set of Conditional Access policies that reflect production security requirements.

**Tasks:**
- Create three Conditional Access policies:
    - **Policy 1:** Require MFA for all users on all cloud apps
    - **Policy 2:** Require compliant device for sensitive apps (SharePoint, clinical platform)
    - **Policy 3:** Block sign-in from outside the US
- Deploy each policy in report-only mode first
- Test each policy by signing in as a test user and reviewing the Conditional Access tab in the sign-in log
- Switch policies to enabled after verifying correct evaluation
- Document each policy with:
    - Policy name and scope
    - Business justification
    - What users and apps it applies to
    - Expected behavior when triggered

**Verification:** Sign in as a test user. Confirm sign-in log shows all three policies evaluated. Verify MFA is prompted. Verify the Conditional Access tab shows correct policy evaluation results.

---

## Pipeline Phases

### Phase 1 — Joiner Provisioning

**Scenario:** New clinical coordinator hired. HR enters the record in the HR simulator.

**What happens end to end:**

1. HR simulator writes Joiners.csv record with department, jobTitle, manager, employeeID, startDate
2. `Invoke-JMLProvisioning.ps1` reads the CSV and creates the AD account with all attributes
3. Entra Connect syncs the user to Entra ID on the next cycle
4. Dynamic group `DG_Clinical_Coordinator` evaluates and adds the user based on department + jobTitle attributes
5. Group-based licensing assigns M365 E5 license
6. SCIM provisioning pushes user account to Clinical Platform and Internal Wiki (based on group-to-app assignment)
7. Lifecycle workflow (joiner) triggers based on `employeeHireDate`, generates temporary access pass
8. Okta AD Agent syncs user to Okta Universal Directory, SAML app access granted via group membership
9. User signs in on Day 1, completes MFA enrollment, accesses all assigned apps

**Evidence captured:**
- AD account creation (audit log)
- Entra Connect sync confirmation (sync health dashboard)
- Dynamic group membership (audit log showing group add)
- License assignment (audit log)
- SCIM provisioning log (Enterprise App → Provisioning)
- Lifecycle workflow execution log (Identity Governance → Lifecycle Workflows)
- Sign-in log showing successful first authentication with MFA

<!-- TODO: Insert screenshots of each evidence artifact -->

---

### Phase 2 — Post-Provisioning Verification

After the joiner pipeline completes, verification confirms the account is in the expected state across all systems.

**What gets verified:**
- AD account exists, is enabled, correct OU, correct attributes
- Entra ID user object has matching attributes
- Dynamic group membership is correct
- License is assigned
- SCIM provisioned accounts exist in downstream apps with correct attributes
- Lifecycle workflow executed successfully (temporary access pass generated)
- Okta user profile matches AD

**Output:** Timestamped verification log in `C:\JML-Lab\Logs\` capturing account state across all systems. This becomes the audit trail for compliance evidence.

<!-- TODO: Insert screenshot of verification log -->

---

### Phase 3 — Mover Provisioning

**Scenario:** Employee promoted from Clinical Coordinator to Senior Clinical Analyst. Department changes from "Clinical" to "Clinical Analytics."

**What happens end to end:**

1. HR simulator writes Movers.csv record with new department, new jobTitle
2. `Invoke-JMLProvisioning.ps1` reads the CSV, updates AD attributes, removes old group, adds new group
3. Entra Connect syncs updated attributes to Entra ID
4. Dynamic group `DG_Clinical_Coordinator` drops the user (department no longer matches)
5. Dynamic group `DG_Clinical_Analyst` adds the user (new department + jobTitle match)
6. Old license removed, new license assigned (if different SKU per role)
7. SCIM updates user role in Clinical Platform, provisions access to Analytics Dashboard
8. Okta syncs the updated group membership, SAML app access adjusts

**Critical sequencing:** Remove old group before adding new group. Simultaneous membership in conflicting groups can trigger unexpected access during the transition window.

**Evidence captured:**
- AD attribute change (audit log)
- Dynamic group removal from old group (audit log)
- Dynamic group addition to new group (audit log)
- SCIM update to downstream apps (provisioning log)
- Old app access removed, new app access granted

<!-- TODO: Insert screenshots of mover evidence -->

---

### Phase 4 — Leaver Offboarding

**Scenario:** Employee terminated. HR enters termination in HR simulator.

**What happens end to end:**

1. HR simulator writes Leavers.csv record with termination date and type
2. `Invoke-JMLProvisioning.ps1` reads the CSV:
    - Stage 1: Disable AD account immediately
    - Stage 2: Move to DisabledUsers OU
    - Stage 3: Strip all group memberships (runs independently of Stage 2)
3. Entra Connect syncs the disabled status and group removal to Entra ID
4. Lifecycle workflow (leaver) triggers:
    - Disables Entra ID account
    - Removes all remaining group memberships
    - Revokes all active sign-in sessions
5. Group removal cascades license removal
6. SCIM deprovisions user from all downstream apps
7. Okta AD Agent syncs the deactivation

**Stage independence:** Stage 3 (group removal) runs even if Stage 2 (OU move) fails. An account in the wrong OU with no group memberships is safe. An account in the right OU with active group memberships is a risk.

**Evidence captured:**
- AD account disable timestamp (audit log)
- Group removal timestamps (audit log)
- Lifecycle workflow execution log with all action timestamps
- SCIM deprovisioning log for each downstream app
- Sign-in log confirming no post-termination authentication
- Total elapsed time from termination trigger to full access revocation

<!-- TODO: Insert screenshots of leaver evidence chain with timestamps -->

---

## Failure Scenarios

Each failure scenario is intentionally created, diagnosed using production tools, and documented with a structured incident report.

**Documentation format for each scenario:**
- **User-facing symptom:** What the user or manager reports
- **Where the error was found:** Which log (sign-in, audit, provisioning, lifecycle workflow)
- **Root cause:** What specifically broke and why
- **PAD classification:** Process gap, Automation gap, or Detection gap
- **Resolution:** What was done to fix the immediate issue
- **Prevention:** What was changed to prevent recurrence

---

### Joiner Failures (Scenarios 1-6)

**Scenario 1 — Blank Department Attribute**

HR simulator submits a new hire but leaves the department field blank. The AD account is created with no department. Entra Connect syncs the user. Dynamic groups evaluate but no rule matches because department is empty. User has no group membership, no license, no app access on Day 1.

- **Symptom:** New hire reports "I can't access anything" on Day 1
- **Log:** Audit log shows user created but no group membership events
- **PAD:** Automation gap — no validation rule in the HR simulator or provisioning script to reject records with blank required attributes

---

**Scenario 2 — Usage Location Missing**

User is created with correct department and jobTitle. Dynamic group adds them. Group-based licensing attempts to assign M365 E5 but fails silently because `usageLocation` is not set on the user object.

- **Symptom:** New hire can sign in but can't activate M365 apps (Outlook, Teams, SharePoint)
- **Log:** Licensing error visible in Entra ID → Users → [user] → Licenses (processing error)
- **PAD:** Automation gap — Entra Connect attribute mapping doesn't include usageLocation, or AD doesn't have the attribute populated

---

**Scenario 3 — SCIM Attribute Format Mismatch**

User is provisioned correctly in Entra ID with department "Clinical Operations." SCIM pushes to the Clinical Platform app, but the app expects `clinical_operations` (lowercase with underscores). Provisioning log shows "Invalid department value."

- **Symptom:** New hire has M365 access but can't log into the clinical platform
- **Log:** Enterprise Applications → Clinical Platform → Provisioning → error entry
- **PAD:** Automation gap — no transformation rule in the SCIM attribute mapping

---

**Scenario 4 — SCIM Missing Required Attribute (externalId)**

SCIM provisioning is configured but `externalId` was never mapped to an Entra attribute. Provisioning fails for all users hitting that app with "Missing required attribute: externalId."

- **Symptom:** No users can access the downstream app despite having correct group membership and licenses
- **Log:** Enterprise Applications → [App] → Provisioning → quarantine status
- **PAD:** Automation gap — attribute mapping configuration is incomplete

---

**Scenario 5 — Duplicate User in Downstream App**

A user was manually created in the Analytics Dashboard before SCIM was enabled. When SCIM runs, it tries to create the same user and receives "User already exists in target app."

- **Symptom:** User has access to the app (from manual creation) but their role and attributes aren't managed by SCIM
- **Log:** Enterprise Applications → Analytics Dashboard → Provisioning → conflict error
- **PAD:** Process gap — no pre-provisioning audit was done to identify existing users before enabling SCIM

---

**Scenario 6 — Lifecycle Workflow Partial Failure**

Joiner lifecycle workflow triggers. The "add to onboarding group" action succeeds, but the "generate temporary access pass" action fails (TAP policy not configured or user not eligible).

- **Symptom:** New hire has correct group membership and app access but can't sign in on Day 1 because they have no way to authenticate for the first time
- **Log:** Identity Governance → Lifecycle Workflows → [workflow] → execution log showing partial completion
- **PAD:** Automation gap — TAP policy prerequisites not met, workflow didn't validate eligibility before executing

---

### Mover Failures (Scenarios 7-9)

**Scenario 7 — Dynamic Group Rule Mismatch After Role Name Change**

HR changes department from "Clinical" to "Clinical Operations" for a promoted employee. Entra Connect syncs the new attribute. Dynamic group `DG_Clinical_Coordinator` uses the rule `(user.department -eq "Clinical")`. User drops out of the old group. But `DG_Clinical_Analyst` uses the rule `(user.department -eq "Clinical Analytics")`. "Clinical Operations" matches neither. User has no group membership, no license, no access.

- **Symptom:** Promoted employee reports "I lost access to everything and didn't get access to anything new"
- **Log:** Audit log shows group removal but no group addition
- **PAD:** Automation gap (dynamic group rule too rigid) AND Process gap (HR changed department name without notifying IT)

---

**Scenario 8 — Stale Static Group Assignment Not Removed**

Employee was manually added to a static group granting access to a sensitive app for a project six months ago. The assignment had no expiration. Employee changes roles. Dynamic groups adjust correctly, but the static assignment remains. Employee retains access to the sensitive app that their new role doesn't warrant.

- **Symptom:** No symptom — this is a silent least privilege violation discovered only during access review
- **Log:** Audit log shows no removal event for the static group
- **PAD:** Detection gap — no periodic audit checks for static assignments that should have been time-bound

---

**Scenario 9 — SCIM Updates Department But App Doesn't Recognize New Value**

Employee moves from "Engineering" to "Product." SCIM pushes the department update to the Analytics Dashboard. The app receives "Product" but its internal role mapping only recognizes "Engineering", "Clinical", and "Finance." The user's role in the app doesn't change.

- **Symptom:** Employee's role in the analytics dashboard doesn't match their new position
- **Log:** SCIM provisioning log shows successful push (200 OK) but app-side role didn't update
- **PAD:** Automation gap — no transformation rule to convert department values to the app's internal role mapping

---

### Leaver Failures (Scenarios 10-13)

**Scenario 10 — SCIM Deprovisioning Didn't Trigger**

Account disabled in Entra ID. Group memberships removed. But the SCIM deprovisioning to the Clinical Platform didn't trigger because the app's provisioning scope is set to "sync only assigned users and groups" and the user was already removed from the assigned group before SCIM could process the deprovisioning event.

- **Symptom:** Manager reports terminated employee still has access to the clinical platform three days later
- **Log:** Enterprise Applications → Clinical Platform → Provisioning → no deprovisioning event for this user
- **PAD:** Automation gap — provisioning scope and group removal sequencing conflict

---

**Scenario 11 — OAuth Token Not Revoked**

Lifecycle workflow executes correctly: account disabled, groups removed, sign-in sessions revoked in Entra ID. But the terminated employee had an active session in a downstream app authenticated via OAuth. The refresh token is still valid. The app doesn't check back with Entra on every request. Employee's session stays alive for hours.

- **Symptom:** Terminated employee's activity detected in downstream app logs after account was disabled
- **Log:** Entra sign-in log shows no new authentication, but app-side activity log shows continued access
- **PAD:** Detection gap — no monitoring for active app sessions post-termination, AND Automation gap — session revocation in Entra doesn't cascade to OAuth tokens in third-party apps

---

**Scenario 12 — Contractor Extension After Workflow Fired**

A contractor's engagement was extended by two weeks. HR updates the HRIS. But the lifecycle workflow already fired based on the original `employeeLeaveDateTime`. Account is disabled, groups removed, SCIM deprovisioned. The contractor can't work.

- **Symptom:** Contractor reports "I'm locked out of everything but I'm supposed to still be working"
- **Log:** Lifecycle workflow execution log shows the workflow ran on the original leave date
- **PAD:** Process gap — no process exists for communicating contract extensions to IT before the workflow fires, AND Automation gap — lifecycle workflow doesn't re-check the attribute before executing

---

**Scenario 13 — Shared Mailbox Permissions Remain**

Account disabled and all group memberships removed. SCIM deprovisioned from downstream apps. But the terminated employee was a delegate on a shared mailbox containing PHI. The delegation permission is a direct assignment on the mailbox, not driven by group membership. The delegate access remains active.

- **Symptom:** Discovered during quarterly access review or by the mailbox owner noticing the delegate list
- **Log:** Exchange Online audit log shows no removal of mailbox delegation
- **PAD:** Process gap — offboarding checklist doesn't include shared mailbox delegation review, AND Detection gap — no script monitors for delegation permissions held by disabled accounts

---

## Compliance Detection Scripts

Three production-ready PowerShell scripts using the Microsoft Graph SDK to proactively detect the failures documented above.

### Script 1 — Stale Account Detection

**Purpose:** Identify user accounts with no sign-in activity in 90 days

**Logic:**
- Query Microsoft Graph for all enabled user accounts
- Pull `lastSignInDateTime` from the `signInActivity` resource
- Filter for accounts where last sign-in exceeds 90 days
- Exclude service accounts by filtering on account type or naming convention
- Output: CSV with UPN, displayName, department, manager, lastSignInDate

<!-- TODO: Insert script and sample output -->

---

### Script 2 — Orphaned Access Detection

**Purpose:** Identify terminated users who still have active accounts in downstream apps

**Logic:**
- Query Microsoft Graph for all disabled user accounts (terminated)
- For each disabled user, check SCIM provisioning status in each enterprise app
- Flag any disabled user whose downstream app account is still active
- Output: CSV with UPN, termination date, app name, app account status

<!-- TODO: Insert script and sample output -->

---

### Script 3 — Offboarding SLA Audit

**Purpose:** Verify offboarding actions are completed within the 24-hour SLA

**Logic:**
- Import termination dates from HR export CSV
- Query Entra audit logs for account disable events
- Calculate time gap between termination date and account disable timestamp
- Flag any offboarding where the gap exceeds 24 hours
- Output: CSV with UPN, termination date, disable date, gap in hours, SLA status (pass/fail)

<!-- TODO: Insert script and sample output -->

---

## Deliverables

| Deliverable | Description |
|---|---|
| JML Lifecycle Runbook | Step-by-step procedures for joiners, movers, and leavers covering happy path, failure modes at each stage, and escalation criteria |
| SCIM Troubleshooting Guide | Organized by error message (missing attribute, invalid format, duplicate user, quarantine status) with resolution steps and log screenshots |
| Failure Incident Report | All 13 scenarios documented with user-facing symptom, log evidence, PAD classification, resolution, and prevention |
| Pre-Provisioning Checklist | Template to complete before enabling SCIM for any new app (required attributes, expected formats, transformation rules, existing users) |
| Compliance Script Suite | Three PowerShell scripts with documentation, required permissions, and sample output |
| Conditional Access Policy Documentation | Each policy with business justification, scope, and troubleshooting steps |
| Access Review Evidence Package | Review results, audit log exports, and remediation documentation (completed in governance phase) |

---

## Key Lessons Learned

<!-- TODO: Populate as lessons are encountered during lab build -->

### 1. Dynamic groups are only as reliable as the attributes that feed them
If HR changes a department name and IT doesn't update the dynamic group rule, users fall into a gap where no group matches. Attribute governance between HR and IT is a process requirement, not a technical one.

### 2. SCIM provisioning success doesn't mean the user has correct access
A 200 OK from the SCIM endpoint means the push was received. It doesn't mean the app mapped the attributes to the correct role. Always verify at the app level, not just in the provisioning log.

### 3. Offboarding is a race condition
Group removal, account disable, session revocation, SCIM deprovisioning, and device wipe all need to happen in a coordinated sequence. If group removal happens before SCIM can process the deprovisioning event, the user may never get deprovisioned from the downstream app.

### 4. Silent failures are the most dangerous
License assignment failures, SCIM attribute mismatches, and stale OAuth tokens don't generate user-facing errors. They only surface when someone can't access something or when an auditor finds an active account that should have been disabled. Detection scripts are not optional.

### 5. The HR system is the source of truth — and the source of most problems
Blank attributes, renamed departments, extended contracts that aren't communicated — the majority of pipeline failures originate in the data, not the automation. The automation did exactly what it was told. The data was wrong.

---

## What I'd Do Differently in Production

**Replace the Flask HR simulator with a real HRIS webhook integration.**
The CSV-driven architecture mirrors production data flow, but a webhook from Workday or BambooHR would eliminate the manual script execution step entirely.

**Implement continuous access monitoring, not just quarterly reviews.**
The compliance detection scripts run on-demand. In production, they would run daily on a scheduled task with automated alerting to the security team.

**Add a rollback mechanism for lifecycle workflows.**
Scenario 12 (contractor extension after workflow fired) reveals the need for an undo capability. In production, the workflow should have a rollback path that re-enables the account, restores group memberships, and re-provisions downstream apps.

**Centralize all logs in a SIEM or Log Analytics workspace.**
Sign-in logs, audit logs, provisioning logs, and lifecycle workflow logs are currently in separate locations within the Entra portal. In production, they would all feed into Azure Monitor or a SIEM for unified alerting and correlation.

**Expand SCIM monitoring to include app-side verification.**
The current pipeline verifies that SCIM pushed successfully (provisioning log). In production, a verification step would also query each downstream app's API to confirm the user account exists with the correct role and attributes.

---

*Yearwood.Local IAM Lab — Built as a portfolio project for hands-on identity and access management experience.*

**Evan Yearwood**
[LinkedIn](https://linkedin.com/in/evan-yearwood/) · [GitHub](https://github.com/EvanHYearwood)
