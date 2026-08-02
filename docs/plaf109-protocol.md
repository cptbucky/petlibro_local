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
GRAIN_THAW -> GRAIN_START -> OPEN_DOOR -> GRAIN_END
```

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

### Unresolved

- **Timezone of `executionTime`.** The auger service converts local → UTC before
  sending, but this has not been confirmed for wet plans. Getting it wrong
  shifts every scheduled feed by the UTC offset.
- **Whether `repeatDay` is honoured.** The field is accepted, but the device
  accepts anything, so acceptance proves nothing. Distinguishing it requires
  observing behaviour across days.
- **Whether `executionDay` advances by itself.** The vendor repeatedly pushed the
  same `planId` with a changed `executionDay` and `optCode: PLATE_POSTPONE`,
  which is consistent with the *server* rolling the date forward. If so, a local
  integration must do the same or schedules stop after one occurrence.

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

No grain attributes appear — no `surplusGrain`, `motorState` or
`grainOutletState`. `temperature` arrives on the heartbeat, this being a cooled
feeder.

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
  and produces a loop rather than a relay.
