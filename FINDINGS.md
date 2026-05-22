# FINDINGS.md — Reverse engineering Sportrick SocialBooking (Sport Polimi)

Target: `https://ecomm.sportrick.com/sportpolimi`

Status legend: ✅ confirmed (observed) · ❓ unknown — needs an authenticated HAR capture.

---

## 1. Platform identification ✅

- **Not** a JS SPA. It is a **server-rendered ASP.NET MVC** application.
  - Theme: Metronic (KeenThemes), Bootstrap 3.3.4, jQuery.
  - Page title: `Sportrick SocialBooking`.
  - Razor app: uses `~/`-prefixed paths, MVC bundles (`/bundles/myapp`, `/bundles/metronic/app`).
  - Culture: `it-IT`. Currency `€`. Server header reported `MyApp._currentServer = "SRAPP04"`.
- Booking UI is built on **FullCalendar** (config present in `/sportpolimi/Javascript/main`).
  FullCalendar fetches events from an AJAX endpoint that returns JSON event objects.

Implication: this is a classic cookie-authenticated MVC site with anti-forgery
tokens on POST forms. There is almost certainly **no SPA/JSON bearer token** —
the session lives in **HTTP cookies**.

## 2. Routing ✅ (via unauthenticated probing)

Unauthenticated requests to `[Authorize]` routes return **302 → `/sportpolimi/Account/Login?returnUrl=...`**.
Nonexistent routes return **404**. Using this oracle:

| Route | Result | Meaning |
|-------|--------|---------|
| `/sportpolimi` | 302 → `/Account/Login` | root requires auth |
| `/sportpolimi/Account/Login` | 200 | login page |
| `/sportpolimi/Booking` | 302 → login | ✅ **booking controller exists** |
| `/sportpolimi/Booking/Index` | 302 → login | ✅ booking landing action |
| `/sportpolimi/Account/LogOut` | (referenced in JS) | logout |
| `/sportpolimi/Account/ChangeBranch` | (referenced in JS) | branch switch |
| `/sportpolimi/Cart/TopBarPartial` | (referenced in JS) | cart partial |
| Booking/GetEvents, /Book, /Create, … | 404 on GET | POST-only ⇒ invisible to GET probing ❓ |

So the booking controller is **`Booking`**. Its AJAX sub-actions (the FullCalendar
event source and the booking POST) are POST-only and cannot be enumerated without
an authenticated session.

## 3. Session / auth mechanism ✅ (mechanism) ❓ (exact cookie names)

- Auth = **HTTP cookies** (ASP.NET). Expect one or more of:
  - `.AspNet.ApplicationCookie` (OWIN) **or** `.ASPXAUTH` (Forms auth) — the auth cookie.
  - `ASP.NET_SessionId` — session cookie.
  - possibly `__RequestVerificationToken` (anti-forgery cookie, paired with a hidden form field).
- ❓ Confirm the exact set after login via HAR. The bot grabs **all** `document.cookie`
  pairs anyway (see bookmarklet), so exact names are not strictly required to *send*
  the session — only useful for documentation/validation.
- localStorage: likely unused for auth (server-rendered). Bookmarklet captures it too,
  just in case, but it is probably empty/irrelevant.
- ⚠️ **HttpOnly caveat:** ASP.NET auth cookies are usually flagged `HttpOnly`, which
  makes them **invisible to `document.cookie`** — so the bookmarklet may fail to
  capture the critical cookie. Confirm in the HAR (look at the `Set-Cookie` flags).
  Fallback documented in README + bookmarklet.html: copy the full `Cookie:` request
  header from DevTools → Network, which *does* include HttpOnly cookies. The bot
  accepts that raw string directly via `/cookies`.

## 4. Anti-forgery token ✅ (expected) ❓ (exact field/placement)

ASP.NET MVC POST actions are normally protected with `[ValidateAntiForgeryToken]`.
The token is rendered as a hidden input:

```html
<input name="__RequestVerificationToken" type="hidden" value="...">
```

The booking POST must include this field (value scraped fresh from the Booking page
HTML at request time) **and** the matching `__RequestVerificationToken` cookie.
`booker.py` scrapes the hidden input from `GET /sportpolimi/Booking` before booking.
❓ Confirm field name and whether the calendar/book actions actually require it.

## ✅ RESOLVED FROM HAR (ecomm.sportrick.com.har, 2026-05-22)

Confirmed multi-step booking flow (class-type activity):

1. `GET /sportpolimi/Booking` → index
2. `GET /sportpolimi/Booking/ChooseActivity` → activity picker
3. `GET /sportpolimi/Booking/BookActivity?activityId=245765508040862208&activityFlags=Classes`
4. **Slot list** — `POST /sportpolimi/Booking/GetActivitySchedule`
   `Content-Type: application/x-www-form-urlencoded`, header `X-Requested-With: XMLHttpRequest`,
   **no anti-forgery token**. Body (one POST per day):
   ```
   currentDate=2026-05-24&activityID=245765508040862208&skillID=0&activityFlags=Classes&getForCatchUp=false
   ```
   Response `{status:"OK", data:[ {Type, Status, TimeStart, TimeDuration, DateStart,
   EventName, EventDescription, ServiceId, ParentCourseId, AppointmentId, StartDateTime,
   AvailabilitiesAlmostOver, ...} ]}` (field names read from `booking-bookactivity` bundle).
5. `GET /sportpolimi/Booking/ConfirmActivityBooking?appointmentID=<id>` → confirm HTML (skippable)
6. **Book** — `POST /sportpolimi/Booking/BookAppointment`, form body `appointmentID=<id>`.
   Response `{status, message, url, canBookAgain}`.

Key facts the bot uses:
- **Slot identifier** = `AppointmentId` (same value flows schedule → confirm → book).
- **Slot time** = `TimeStart` first 5 chars (`HH:MM`).
- **Bookable** when `Status == "Available"` (CSS class `slot-available`); otherwise full/closed.
- **class** path uses `AppointmentId` + `BookAppointment`. (A `service` variant exists —
  `ServiceId` + `ConfirmServiceSlot`/`bookService` — but Sport Polimi gym slots are `class`.)
- `activityID` is the only per-gym magic value → config `ACTIVITY_ID`
  (HAR value `245765508040862208`), with `SKILL_ID=0`, `ACTIVITY_FLAGS=Classes`.
- HAR `BookAppointment` body was just `appointmentID=…` (no `areaElementID`) ⇒ bot can
  POST `BookAppointment` directly with the cached `AppointmentId`, skipping the Confirm GET
  for speed.

Cookies (from Application→Cookies, all **HttpOnly** ⇒ bookmarklet impossible):
`.ASPXAUTH_SOCIALBOOKING`, `ASP.NET_SessionId`, `__RequestVerificationToken`.
Cookie extraction switched to **DevTools → Copy as cURL** (parsed by `/cookies`).

> Note: the HAR did not store response *bodies* (content text empty) and the live
> cookies were stripped from it, so the exact JSON could not be replayed; field
> names above come from the site's own `booking-bookactivity.js` which parses that
> JSON. Verify field casing on the first real `/test` run.

---

## 5. Slot-listing endpoint ✅ (was ❓ — now resolved above)

FullCalendar event source. Expected shape (typical Sportrick / FullCalendar):

```
GET (or POST) /sportpolimi/Booking/GetEvents?start=2026-05-24&end=2026-05-25[&resource=...]
→ 200 application/json
[
  { "id": 123456, "title": "Sala Pesi 08:30", "start": "2026-05-24T08:30:00",
    "end": "2026-05-24T10:00:00", "availablePlaces": 4, ... },
  ...
]
```

Unknowns to capture:
- exact action name + HTTP method + query/body param names for the date range,
- the JSON field that holds the **slot identifier** (`id` / `eventId` / `slotId` / `serviceSessionId`),
- the field for **start time** (to match the user's preferred `HH:MM`),
- the field that signals **availability / full** (`availablePlaces`, `free`, `bookable`).

`booker.py` isolates this in `parse_slots()` and `EVENTS_*` constants — fill from HAR.

## 6. Booking (confirm) endpoint ❓ — NEEDS HAR

Expected shape:

```
POST /sportpolimi/Booking/Book   (or /Create, /Confirm, /Reserve)
Content-Type: application/x-www-form-urlencoded   (or application/json)
Body: id=123456&__RequestVerificationToken=...   (+ maybe quantity, resourceId, ...)
→ 200 JSON { "success": true, ... }  or a redirect / partial HTML
```

Unknowns to capture:
- exact action name + method,
- content type (form-urlencoded vs JSON),
- the exact body keys (which key carries the slot id, any extra required fields),
- success/failure signalling (HTTP status, JSON `success` flag, error message text such
  as “posto esaurito” / “completo” for a full slot).

`booker.py` isolates this in `book_slot()` + `BOOK_*` constants — fill from HAR.

---

## 7. HOW TO CAPTURE THE HAR (do this once, then send to fill §5/§6)

1. Chrome/Edge → open `https://ecomm.sportrick.com/sportpolimi`, log in with **SPID**.
2. Open **DevTools (F12) → Network**. Tick **Preserve log**. Filter: **Fetch/XHR**.
3. Go to the booking calendar, navigate to a day, **click a slot and confirm a booking**.
4. In Network, find:
   - the request that returns the **list of slots** (JSON array of events),
   - the request fired when you **confirm** the booking (POST).
5. Right-click each → **Copy → Copy as cURL** (or right-click in the list → **Save all as HAR**).
6. Send those to update §5 and §6 here, then `booker.py` constants get filled in.

> Note: reverse engineering past the login wall is impossible without your SPID
> session (identity + 2FA). The bot code is fully built around the confirmed
> platform mechanics above; only the two endpoint constants + their tiny
> parser/payload functions remain to be locked from the HAR.

---

## 8. Confirmed constants for the bot

| Thing | Value |
|-------|-------|
| Base URL | `https://ecomm.sportrick.com` |
| Gym path | `/sportpolimi` |
| Booking page (session check + token scrape) | `/sportpolimi/Booking` |
| Login redirect (⇒ session expired) | `/sportpolimi/Account/Login` |
| Logout | `/sportpolimi/Account/LogOut` |
| Culture / TZ | `it-IT` / `Europe/Rome` |
| Auth transport | HTTP cookies |
