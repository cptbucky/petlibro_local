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

`NTP`'s reply carries a timezone block (`timezoneOffsetSeconds` plus DST
transitions). It does **not** govern plan scheduling — `executionTime` is UTC
regardless. See [Scheduling](#scheduling).

Shared with other models: `HEARTBEAT`, `NTP`, `ATTR_GET_SERVICE`,
`ATTR_SET_SERVICE`, `ATTR_PUSH_EVENT`, `DEVICE_START_EVENT`, `ERROR_EVENT`,
`GET_CONFIG`.

Notably absent: **`GET_FEEDING_PLAN_EVENT` never appears.** There is no
plan-fetch command for this model.

## Feeding

A wet feed is not a quantity, and it is not brief. The firmware pauses
refrigeration, rotates to the plate, opens the door and shuts it again after
`feedingDuration` **minutes** — a meal window measured in hours, reported as
`execStep`:

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

`GRAIN_THAW` arrives **30 minutes before** the scheduled time: refrigeration
pauses first, then the plate rotates and the door opens at the scheduled
instant.

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

**Measured 2026-08-06.** `feedingDuration` is in **minutes, not seconds**. A
plan carrying `120` held the door open from 21:00:06Z to 23:00:12Z — 7205
seconds, i.e. 120.1 minutes. Vendor plans used 120 and 210, so a wet "feed" is
a meal window of hours rather than a brief dispense.

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

### Resolved

- **`executionTime` is UTC.** Measured 2026-08-06 from a scheduled feed:

  ```
  plan 44456688   executionTime "21:00"   executionDay 2026-08-05
  GRAIN_THAW      20:30:01Z    (30 min pre-thaw)
  GRAIN_START     21:00:01Z    <- fires at 21:00 UTC, which was 22:00 BST
  ```

  An earlier revision of this document claimed the opposite, reasoning from the
  vendor's naming convention: it suffixes `lightingStartTimeUtc` and
  `soundStartTimeUtc` but not `executionTime`, which looked like a deliberate
  distinction. It is not — everything on the wire is UTC and the suffix is
  merely inconsistent. **One observed feed outweighed the entire argument.**

  The device does receive `timezoneOffsetSeconds: 3600` from the vendor on
  every NTP exchange, but that is a red herring for scheduling: plan times do
  not depend on it, and an integration should not send its own value unless it
  can be sure the value is right.

  Practical consequence: convert the user's local time to UTC before sending,
  and back for display. Note that Home Assistant commonly runs in a container
  with no `TZ` set, where `datetime.now().astimezone()` reports UTC regardless
  of what the user configured — so that conversion must read Home Assistant's
  configured timezone, or it silently becomes a no-op and every feed lands an
  hour out.

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

### What this model does not report

Measured 2026-08-06 by diffing every wire key against the PLAF203 in the same
capture. None of the following ever appears, so any entity backed by one is
permanently unavailable on this model:

```
enableAudio            (it rings instead - see DEVICE_FUNCTION_TEST_SERVICE)
autoChangeMode  disableHardwareButton   bowlMode
cameraSwitch    videoRecordSwitch       feedingVideoSwitch
cloudVideoRecordSwitch  motionDetectionSwitch  soundDetectionSwitch
nightVision     resolution              videoRecordMode
motionDetectionRange    motionDetectionSensitivity
soundDetectionSensitivity
sdCardState     sdCardTotalCapacity     sdCardUsedCapacity
surplusGrain    motorState              grainOutletState
```

That is 8 switches, 6 selects and the SD card sensors' worth of entities if an
integration creates them unconditionally. `DETECTION_EVENT` never fires either.

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

## Liveness and cadence

**Measured 2026-08-06**, over 19 hours of vendor traffic on a TCP session that
never dropped:

| | PLAF109 | PLAF203 |
|---|---|---|
| `HEARTBEAT` interval | **90s** (p95 91s) | 72s |
| `DEVICE_START_EVENT` | every 30 min | every 30 min |
| `NTP` exchange | every 30 min | every 30 min |
| `DEVICE_CONFIG_SYNC` | every 30 min | every 30 min |

Two consequences worth designing for:

**An offline watchdog must clear 91s.** Anything shorter marks the PLAF109 dead
between every pair of heartbeats. This integration used 81s, so the feeder — and
therefore every entity on it — flapped unavailable roughly every 90 seconds.

**`DEVICE_START_EVENT` is not a boot event.** It arrives every 30 minutes on a
connection that never dropped, and the heartbeat `count` ran from 1285 to 2069
across 43 of them without a single reset — a restart would have zeroed it. It is
a periodic re-announce. Treating each one as a restart and re-requesting the full
attribute set costs 48 needless round trips a day.

The reliable restart signal is the heartbeat `count` going backwards.

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
