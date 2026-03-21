# Fitness Studio Access Implementation Plan

## Goal
Deliver Phase 1 of the studio access platform as a modular monolith on Synology via Docker Compose, based on the approved design in `2026-03-20-fitness-studio-access-design.md`.

## Phase 1 Workstreams

### 1. Project foundation
- Create the actual application package layout and align it with `pyproject.toml`
- Add Docker Compose services for:
  - `web`
  - `worker`
  - `db`
- Add environment-based configuration for:
  - Magicline
  - Nuki
  - SMTP
  - bootstrap admin
  - scheduler timing
- Decide SQLite vs PostgreSQL for Phase 1 startup and document the choice

### 2. Data model and persistence
- Create database schema and migrations for:
  - `users`
  - `members`
  - `bookings`
  - `member_entitlements`
  - `access_windows`
  - `access_codes`
  - `door_events`
  - `admin_actions`
  - `alerts`
- Define idempotency keys for Magicline bookings and Nuki-side code sync
- Add audit-safe storage rules for sensitive code handling

### 3. Authentication and roles
- Implement bootstrap admin creation from `.env`
- Implement local login with password hashing and session/auth token handling
- Implement admin-created `Admin` and `Operator` accounts
- Implement self-service reset by email and admin-driven password reset
- Enforce role checks for admin-only operations such as remote open

### 4. Magicline integration
- Build a client for:
  - customer search and member refresh
  - relevant booking fetches
  - entitlement discovery for `XXLARGE` and `Freies Training`
- Implement 30-minute polling sync
- Implement webhook endpoint for short-notice booking changes
- Implement reconciliation rules:
  - booking ID as identity
  - newest Magicline booking state wins
  - webhook receipt time beats poll time if no reliable update timestamp exists

### 5. Access policy and scheduler
- Implement booking relevance filter for `Freies Training`
- Implement entitlement evaluation
- Build access-window derivation in `Europe/Berlin`
- Support:
  - normal `T-15 minutes` activation
  - immediate processing for late bookings
  - `T+30 minutes` expiry
- Consolidate overlapping or adjacent windows per member

### 6. Nuki integration
- Implement keypad code creation, update, and deactivation
- Set validity windows per access window
- Implement emergency-code replacement flow
- Implement admin-only remote open
- Capture Nuki failures and retries with visible alerts

### 7. Notifications
- Send member emails with access code
- Send operator/admin alert emails for failed automation
- Add templates for:
  - normal code dispatch
  - late or failed provisioning notice
  - password reset

### 8. Admin UI and operational tooling
- Build views for:
  - login
  - member search
  - member detail
  - booking history
  - door action history
  - alerts
  - user management
- Add actions for:
  - resend code
  - early deactivate
  - create one-time emergency code
  - remote open for admin

### 9. Testing and verification
- Unit-test entitlement logic, access-window calculation, reconciliation rules, and late-booking behavior
- Integration-test Magicline sync, webhook ingestion, Nuki flows, and email dispatch
- Add role/permission tests for `Admin` vs `Operator`
- Add end-to-end tests for:
  - normal booking flow
  - short-notice booking
  - cancellation or reschedule
  - Nuki failure with alerting
  - emergency-code replacement

## Recommended delivery order
1. Foundation, config, package layout, and database
2. Auth and role model
3. Magicline sync and booking persistence
4. Access policy and scheduler
5. Nuki integration
6. Notifications
7. Admin UI
8. Testing hardening and deployment validation

## First implementation slice
Build the smallest end-to-end path first:
- bootstrap admin
- member and booking sync from Magicline
- entitlement check for `XXLARGE` and `Freies Training`
- access-window creation
- scheduled code creation in Nuki
- member email dispatch
- simple admin page to inspect members, windows, and alerts

## Risks to control early
- exact Magicline field mapping for booking updates and entitlement detection
- Nuki keypad limits and one-time emergency-code behavior
- reliable scheduler execution on Synology
- email deliverability and secret handling
