# Fitness Studio Access Platform Design

## Goal
Build Phase 1 of a 24/7 access platform for a fitness studio running on a Synology NAS via Docker Compose. The platform manages access for booked training slots using Magicline as the source of truth, Nuki Pro with Keypad 2 as the access medium, and a web interface for operators and admins.

## Phase 1 Scope
- Sync relevant Magicline appointment bookings, especially `Freies Training`
- Sync the member data required for those bookings
- Determine whether a member is entitled to access the booked slot
- Generate a personal Nuki keypad code per relevant access window
- Send the code by email `15 minutes before` slot start
- Set the code validity in Nuki to `15 minutes before start` through `30 minutes after end`
- Provide an admin web interface with audit log, member view, booking history, door action history, alerts, manual code resend, manual deactivation, emergency code generation, and remote open for admins

All access-time calculations, email dispatch timing, and Nuki code validity windows use the timezone `Europe/Berlin`. Daylight saving time handling follows that timezone consistently.

Out of scope for Phase 1:
- Tapo camera orchestration
- Aqara presence automation
- Shelly energy or relay automation
- Full alarm scenarios

## Business Rules
- Magicline is the leading business source.
- Access is based on a combination of entitlement and a concrete appointment booking.
- `XXLARGE` members may book `Freies Training`.
- Other members need the Magicline add-on product or tariff named `Freies Training` to qualify.
- Access is granted only for an actual booked `Freies Training` time slot.
- The service syncs appointment bookings regularly and fetches the related member data for those bookings.
- Magicline webhooks are used as a fast path for short-notice bookings and changes.
- Each member has one regular active code at a time. Overlapping or adjacent relevant windows are consolidated.
- On a new relevant access lifecycle, a new personal code is always generated.
- Access-relevant member changes are limited to:
  - loss of `XXLARGE`
  - loss of the Magicline product or tariff named `Freies Training`
  - booking cancellation or booking change
  - explicit member suspension or blocking state
- If a booking is canceled or an access-relevant member change occurs, the current code remains active until the planned end time unless manually deactivated by staff.

## Architecture
Use a modular monolith split into two deployables:

1. Web/API service
- Admin UI
- Local auth with email and password
- Admin and Operator roles
- Member detail pages
- Audit views
- Manual operations

2. Worker service
- Scheduled Magicline sync every 30 minutes
- Webhook ingestion for short-notice bookings
- Entitlement evaluation
- Access window generation
- Scheduler execution
- Nuki integration
- Email dispatch
- Retry handling and alert production

Shared persistence stores operational state, audit entries, alert records, and integration snapshots.

## Roles
- `Admin`: full access, including integration settings, system actions, user management, and remote open
- `Operator`: can view members, resend codes, deactivate access early, and handle operational cases, but cannot change integrations or system rules

## Auth and User Lifecycle
- Local auth uses email and password.
- The first admin account is bootstrapped from environment variables.
- Additional `Admin` and `Operator` accounts are created in the admin UI by an admin.
- Password reset is supported in two ways:
  - self-service via email reset link
  - manual reset by an admin in the admin UI

## Data Flow
1. Regular sync imports relevant appointment bookings from Magicline every 30 minutes.
2. For each relevant booking, the service fetches or refreshes the associated member information.
3. Webhooks supplement the regular sync for bookings created or changed close to slot start.
4. The policy layer checks whether the member is entitled to the booked `Freies Training` slot.
5. The system creates or updates an internal access window.
6. A scheduler job triggers 15 minutes before slot start.
7. The worker generates a new personal Nuki code.
8. The code is emailed to the member.
9. The worker sets the code validity in Nuki for the defined access period.
10. After slot end plus 30 minutes, the code is no longer valid.

Late-booking rule:
- If a relevant booking is created or first discovered less than 15 minutes before slot start, the system processes it immediately.
- In that case, code generation, email dispatch, and Nuki validity setup happen as soon as the booking is validated instead of waiting for the normal `T-15 minutes` scheduler point.

Webhook and polling reconciliation rules:
- Booking identity is keyed by the Magicline booking ID.
- The newest Magicline state wins when the webhook and the periodic sync disagree.
- Newness is determined by the most recent authoritative update timestamp available from Magicline on the booking record.
- If Magicline does not provide a reliable booking update timestamp, webhook receipt time wins over periodic polling receipt time.
- The system must update existing records idempotently rather than create duplicate access windows.
- Create, update, cancel, and reschedule events all resolve onto the same booking record and may recompute the linked access window and code plan.

## Main Modules
- `admin-ui/api`: auth, UI, member views, manual actions, audit endpoints
- `magicline-sync`: polling sync, webhook processing, member refresh
- `access-policy`: entitlement checks and access-window derivation
- `scheduler/worker`: timed jobs, retries, state transitions
- `nuki-adapter`: keypad code management and remote open
- `notifications`: member and operator email flows
- `audit-alerting`: immutable operational trail and alert generation

## Data Model
- `members`: local member snapshot keyed by `magicline_customer_id`
- `bookings`: Magicline appointment bookings keyed by booking ID
- `member_entitlements`: synced eligibility snapshot such as `XXLARGE` or the add-on package
- `access_windows`: operational access periods derived from bookings and entitlement
- `access_codes`: Nuki code records, dispatch state, validity state, and Nuki references
- `door_events`: door actions and outcomes from Nuki
- `admin_actions`: manual staff actions
- `alerts`: sync, Nuki, scheduling, and email failures

## Error Handling and Safety
- If a booking cannot be matched to an entitled member, no access window is created.
- If code generation or Nuki sync fails before activation time, automatic access is not granted.
- In that case, operators are alerted, the member is informed, and staff may create a one-time emergency code for that access window.
- An emergency code replaces the normal code for that access window.
- An emergency code is valid only for that single access window and is intended for one-time use.
- Remote open is admin-only and always audited.
- Codes are never reused across new access lifecycles.
- Manual overrides are visible in the member view.
- Retries must be bounded and visible; failures must raise alerts rather than looping silently.

## Required UI Capabilities
- Login for Admin and Operator
- Member search and detail view
- Display: name, email, Magicline ID, entitlement status, booking history, door action history, latest sync, current code state, and manual overrides
- Manual resend of a current code
- Early deactivation of active access
- Emergency code creation for one access window
- Alert list for failed or flagged operational cases
- Admin-only remote open

## Operational Notes
- Deploy on Synology via Docker Compose.
- Secrets must live in environment variables or a secret store, not in source files.
- Magicline and Nuki credentials must be treated as production secrets.
- The design should allow later Phase 2+ extensions for cameras, sensors, and Shelly automation without rewriting the access core.

## Planning Constraints
- Keep Phase 1 limited to the access core.
- Do not expand into camera or building automation planning yet.
- Prefer operational reliability over feature breadth.
