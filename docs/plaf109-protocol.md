# PLAF109 Polar Wet Food Feeder — protocol notes

Findings from running a PLAF109 against a local Mosquitto broker, and from
observing its traffic to the vendor cloud through a logging TCP proxy.

Each claim below is marked with how it was established:

- **Observed** — seen on the wire
- **Tested** — a command was sent and the device's response recorded
- **Inferred** — reasoned from payload structure, not directly confirmed
- **Unresolved** — known to be unknown

The wet feeder does **not** share the auger command set. `MANUAL_FEEDING_SERVICE`
with `grainNum` is not implemented: the firmware drops it silently, behaving
exactly as it does for a command name that does not exist (verified with an
invented `NONEXISTENT_PROBE_SERVICE` as a control).

## Topics

**Observed.** Identical structure to the auger models:

```
dl/{product_id}/{serial}/device/{channel}/post   device -> server
dl/{product_id}/{serial}/device/{channel}/sub    server -> device
```

The device subscribes to `dl/PLAF109/{serial}/device/+/sub` at QoS 1 — captured
from its SUBSCRIBE on reconnect.

Channels in use: `heart`, `ntp`, `config`, `service`, `event`.

## Commands

**Observed** unless noted. Every command below appeared in vendor traffic.

| Command | Direction | Payload |
|---|---|---|
| `WET_FOOD_FEED_NOW_SERVICE` | → device | `{planId, feedingDuration}` |
| `WET_GRAIN_FEEDING_PLAN_SERVICE` | → device | `{plans: [...]}` |
| `WET_GRAIN_OUTPUT_EVENT` | ← device | `{finished, feedingDuration, planId, plate, execTime}` |
| `GET_SOME_ATTR_SERVICE` | → device | `{attrKeys: ["zeroState", ...]}` |
| `DEVICE_FUNCTION_TEST_SERVICE` | → device | `{action: "AUDIO"}` — rings the bell |
| `DEVICE_CONFIG_SYNC` | → device | `{mqttAddr, httpsAddr}` — pushed unprompted |
| `DEVICE_LOG_REPORT_EVENT` | ← device | diagnostics |
| `PET_DETECT_EVENT` | ← device | `{type: "NEAR" \| "LEAVE"}` |
| `MACHINE_INFRARED_EVENT` | ← device | `{irState: bool}` |

`NTP` is shared but carries more than a clock: its reply sets the device's
timezone, which is what `executionTime` in a plan is measured against. See
[Scheduling](#scheduling) — getting it wrong shifts every scheduled feed.

Shared with other models: `HEARTBEAT`, `NTP`, `ATTR_GET_SERVICE`,
`ATTR_SET_SERVICE`, `ATTR_PUSH_EVENT`, `DEVICE_START_EVENT`, `ERROR_EVENT`,
`GET_CONFIG`.

Notably absent: **`GET_FEEDING_PLAN_EVENT` never appears.** There is no
plan-fetch command for this model.

## Feeding

A wet feed is not a quantity. The firmware runs a multi-second sequence —
pause refrigeration, rotate to the plate, open the door, close it after
`feedingDuration` seconds — reporting progress as `execStep`:

```
GRAIN_THAW -> GRAIN_START -> OPEN_DOOR -> CLOSE_DOOR -> GRAIN_END
```

**Observed 2026-08-05.** `CLOSE_DOOR` was missing from an earlier version of
this list. It arrives immediately before `GRAIN_END`, carrying
`finished: false`, and only `GRAIN_END` carries `finished: true`:

```
CLOSE_DOOR  finished:false  plate:3  feedingDuration:210  execTime:1785940468677
GRAIN_END   finished:true   plate:3  feedingDuration:210  execTime:1785940468677
```

Both share one `msgId` and `execTime`, so a feed cycle is correlated by
`execTime` rather than by message. Anything treating "not GRAIN_END" as "still
feeding" is correct; anything enumerating the steps must include `CLOSE_DOOR`.

**Observed.** The feed that produced the trace above reported `planId: 1`,
while stored plans carry eight-digit ids. Do not assume `planId` in an output
event resolves to a plan in the pushed list.

**Tested.** The door closes only at `GRAIN_END`. A power cycle does **not**
close an open door; the cycle has to complete.

### Feed now

```json
{"cmd": "WET_FOOD_FEED_NOW_SERVICE", "msgId": "...", "ts": 0,
 "planId": 44122386, "feedingDuration": 240}
```

**Tested.** The command carries no plate — the device resolves one from the
referenced plan. An unreferenced `planId` is rejected with **`code 2050`** and
nothing actuates. A feed therefore cannot be fabricated.

**Observed.** Feeding is not constrained to the plan's schedule: a plan whose
`executionTime` was 12:00 the following day fed immediately when triggered.

## Plans

```json
{"planId": 44142243, "executionTime": "07:00", "executionDay": "2026-08-03",
 "plate": 1, "feedingDuration": 210, "optCode": "PLATE_POSTPONE"}
```

- **Observed.** Each plan owns exactly one plate. Two plans pushed together
  carried plate 1 and plate 3 with independent times and durations.
- **Observed.** `optCode` is absent when a plan is created and appears on later
  edits. `PLATE_POSTPONE` is the only value seen.
- **Observed.** Pushes **replace the entire list** — an empty `"plans":[]` push
  immediately followed by the full new set.
- **Tested.** Locally-issued `planId`s are accepted (`code 0`). Plans need not
  originate from the vendor cloud.
- **Tested.** The ack echoes `{planId, syncTime}` for each stored plan. This is
  the only read-back available: it confirms which plans exist, not their
  contents.
- **Observed 2026-08-05.** `syncTime` in that ack is always `0`, across every
  push seen — creates and edits alike. It carries no information; only the ids
  are meaningful. An earlier reading of this document implied `syncTime` might
  be a usable timestamp. It is not.
- **Tested.** The device does **not validate plan contents**. `plate: 99` was
  accepted with `code 0` on a three-plate carousel. Any client must validate
  before sending, and a `code 0` response is not evidence that a field is
  understood.

### Ownership

Push-only, wholesale replacement, no fetch, and an ack that names only ids
together mean **a local integration must own the plan list outright**. It
cannot merge with, or add to, plans created by the vendor cloud, because it can
neither read them nor extend them without replacing them.

### Scheduling

**Tested.** A plan pushed to a local broker fires at its scheduled time and runs
a complete feed cycle — audio, rotation, door open, door close — with no cloud
involved. Local scheduling works.

**Observed.** Pushing a plan list also causes the carousel to reposition
immediately, without opening the door. This is distinct from feeding and is
easily mistaken for it.

### Resolved 2026-08-05

- **`executionTime` is local time, not UTC.** The vendor suffixes its UTC
  fields explicitly and does not suffix this one — in the same session it sent
  `lightingStartTimeUtc: "07:00"` and `soundStartTimeUtc: "07:00"` alongside
  `executionTime: "17:00"`. Converting local → UTC before sending a wet plan
  would shift every feed by the offset.

  But "local" is whatever the device has been *told*, and the NTP reply is the
  only channel that tells it:

  ```json
  {"cmd": "NTP", "ts": ..., "code": 0, "calibrationTag": false,
   "timezoneOffsetSeconds": 3600,
   "nextDSTOffsetSeconds": 0,          "nextDSTTransitionTs": 1792890000000,
   "secondNextDSTOffsetSeconds": 3600, "secondNextDSTTransitionTs": 1806195600000,
   "timezone": 1}
  ```

  **`timezoneOffsetSeconds` is the field the firmware honours.** An NTP reply
  carrying only `timezone` leaves the device on UTC, and a plan entered as
  17:00 then fires at 17:00 UTC — an hour late in BST. That was this
  integration's behaviour until 2026-08-05: it sent `timezone` alone, and users
  compensated by entering times in UTC and letting them run at BST. That
  workaround is how the bug was found, and it is also why the naming-convention
  argument above looked wrong from the outside — the plan time really is local,
  but the device's idea of local was never set.

  **The `nextDST*` / `secondNextDST*` fields are not optional either.** The
  vendor preloads the next two transitions with the offset that applies after
  each, so the device re-bases itself when the clocks change: `1792890000000`
  is 2026-10-25 01:00 UTC (BST→GMT), `1806195600000` is 2027-03-28 01:00 UTC
  (GMT→BST). A correct offset without these drifts by an hour at the next
  transition.

  *Still worth doing:* time one scheduled feed against the wall clock now that
  the offset is sent. Everything above is consistent, but no feed has yet been
  observed firing at a known local time with a correct NTP reply in place.

- **`executionDay` is server-managed — the integration must roll it forward.**
  Editing a plan's time moved its date without being asked to:

  ```
  14:34  planId 44452179  executionTime 07:00  executionDay 2026-08-06
  14:37  planId 44452179  executionTime 21:00  executionDay 2026-08-07
  ```

  while `planId 44452178` kept `2026-08-05` and changed only its time. So the
  server, not the device, decides the next occurrence. A local integration that
  does not advance `executionDay` gets one occurrence and then silence, which
  is what `046dd15` fixes.

### Unresolved

- **Whether `repeatDay` is honoured.** The field is accepted, but the device
  accepts anything, so acceptance proves nothing. Distinguishing it requires
  observing behaviour across days. Note the wet plans captured on 2026-08-05
  carried **no `repeatDay` at all** — only `executionDay` — whereas PLAF203
  plans in the same capture did carry `repeatDay: [7,1,2,3,4,5,6]`. That is
  weak evidence that wet plans are single-occurrence by design and that
  recurrence is entirely the server's job.

## Plate homing (`zeroState`)

**Observed.** `PROCESSING` → `SUCCESS`, or `TIMEOUT` on failure.

**Observed.** The firmware silently declines to actuate unless `zeroState` reads
`SUCCESS`. It accepts the feed command and does nothing. In practice this
presents as an app that hangs with no error, and the cause — a misseated or
jammed plate — is invisible without querying the attribute.

Encountered during development: the feeder reported `TIMEOUT` for hours while
feeds appeared to be ignored. Reseating the plate restored `SUCCESS` and
feeding resumed immediately.

Related attributes: `plateStuckCurrent`, `doorStuckCurrent`, `plateErrTimeout`,
`idenFirstPlateTime`, `irSensorIdenTimeout`.

## Attribute set

**Observed** via `ATTR_GET_SERVICE`, answered on `event/post` (not
`service/post`):

```
powerMode powerType electricQuantity wifiSsid volume
enableLight lightSwitch lightAgingType
enableSound soundSwitch soundAgingType
closeDoorTime doorCheckSignalTime doorNotcheckSignalTime
doorStuckCurrent plateStuckCurrent plateErrTimeout
irSensorIdenTimeout idenFirstPlateTime zeroState
ringerMode ringerInterval ringerDuration
```

**Observed 2026-08-05**, settable via `ATTR_SET_SERVICE` but never appearing in
an attribute push, so they are write-mostly and invisible unless you watch the
vendor set them:

```
lightingStartTimeUtc lightingEndTimeUtc      (e.g. "07:00" / "19:00")
soundStartTimeUtc    soundEndTimeUtc         (e.g. "07:00" / "19:00")
temperatureCheckSwitch                       (bool)
```

These are the light and sound aging windows — the `*AgingType` attributes above
select the mode, and these carry its schedule. Unlike `executionTime` they are
explicitly UTC.

No grain attributes appear — no `surplusGrain`, `motorState` or
`grainOutletState` — and no storage attributes either: `sdCardState`,
`sdCardTotalCapacity` and `sdCardUsedCapacity` are sent by the PLAF203 in the
same capture and never by this model.

`temperature` arrives on the **heartbeat**, this being a cooled feeder, not in
an attribute push:

```json
{"cmd": "HEARTBEAT", "count": 1455, "rssi": -37, "wifiType": 1,
 "temperature": 16.63}
```

That means it updates roughly every 90 seconds without polling, and that a
field map covering only attribute pushes will silently drop it.

**Observed.** `electricQuantity` reads `0` on every push, with `powerType: 1`
and `powerMode: 1`. The PLAF203 also reports `0`, so this is not a wet-feeder
quirk and the attribute is genuinely supported — it simply has no charge to
report on a mains-powered unit.

**Observed.** The vendor polls exactly one attribute via
`GET_SOME_ATTR_SERVICE`, and it is `platePosition` — not `zeroState`. An
earlier note here claimed `zeroState` was what the cloud polled before feeding;
that was wrong. `zeroState` still gates actuation (see below), but the cloud
does not read it on this path.

## Telemetry on a local broker — works

**Tested.** An earlier draft of this document claimed the device publishes only
heartbeats when connected to a local broker, and named that the most
consequential open question for the integration. **That was wrong, and it was a
measurement error rather than device behaviour.**

Queried directly with `GET_SOME_ATTR_SERVICE` against a local Mosquitto, the
device replied within milliseconds:

```
event/post  GET_SOME_ATTR_SERVICE  code 0
event/post  ATTR_PUSH_EVENT  platePosition: 3
event/post  ATTR_PUSH_EVENT  zeroState: SUCCESS
```

This was the *control* arm of a test of the theory that some handshake
(`DEVICE_CONFIG_SYNC`) had to be completed before the device would report. The
control passed before the handshake was sent, so the theory was never needed.
Sending `DEVICE_CONFIG_SYNC` afterwards changed nothing; the device
acknowledges it with `code 0` and behaves identically either way.

Home Assistant is also visibly acknowledging events on `event/sub` in the same
capture, so the integration receives them.

The earlier empty captures came from the `mosquitto_sub | grep` buffering
problem described under Method notes: events that certainly occurred appeared
never to have happened.

**Still unconfirmed:** a full `execStep` sequence has not been cleanly observed
on a local broker during a scheduled feed. That is likely the same measurement
problem, but it has not been proven either way, so anything depending on feed
progress should be verified before being relied upon.

## Response codes

| Code | Meaning |
|---|---|
| `0` | accepted (**not** "understood" — see plan validation above) |
| `2030` | device not bound |
| `2050` | `planId` not found on the device |

## Method notes

Captured with a logging TCP proxy between the feeder and the vendor cloud,
placed by overriding `mqtt.us.petlibro.com` in local DNS. The device speaks
plaintext MQTT on port 1883, so payloads are readable directly.

Two pitfalls cost real time and are worth repeating:

- `mosquitto_sub | grep` **buffers**. When the process is killed by `timeout`
  the buffer is lost, and events that certainly occurred appear never to have
  happened. Capture raw, filter afterwards.
- The proxy must reach the vendor by **IP, not hostname**. Resolving the
  hostname inside a network that overrides it returns the proxy's own address
  and produces a loop rather than a relay. The underlying cause is *which
  resolver answers*: query a public one directly and refuse to start if the
  answer is a local address.
- The override record's **TTL applies to the feeder too**. With `TTL 3600`, a
  PLAF109 kept reconnecting to the pre-change address for the best part of an
  hour while a PLAF203 on the same network moved immediately — which looks
  exactly like a firmware difference and is not one. Drop the TTL to 60s before
  repointing, or no test inside the window means anything.

The 2026-08-05 observations in this document come from
[petlibro-local-proxy](https://github.com/erosen14/petlibro-local-proxy), a
rebuilt version of that rig which records decoded JSONL alongside the raw byte
stream, so a decoding mistake costs a re-decode rather than the capture.
