"""Constants for the Petlibro Local integration."""

DOMAIN = "petlibro_local"

# MQTT
DEVICE_PRODUCT_ID = "PLAF203"
MQTT_PORT = 1883

# MQTT topic templates
TOPIC_BASE = "dl/{product_id}/{serial}/device"
TOPIC_HEART_POST = TOPIC_BASE + "/heart/post"
TOPIC_NTP_POST = TOPIC_BASE + "/ntp/post"
TOPIC_NTP_SUB = TOPIC_BASE + "/ntp/sub"
TOPIC_OTA_POST = TOPIC_BASE + "/ota/post"
TOPIC_OTA_SUB = TOPIC_BASE + "/ota/sub"
TOPIC_CONFIG_POST = TOPIC_BASE + "/config/post"
TOPIC_CONFIG_SUB = TOPIC_BASE + "/config/sub"
TOPIC_EVENT_POST = TOPIC_BASE + "/event/post"
TOPIC_EVENT_SUB = TOPIC_BASE + "/event/sub"
TOPIC_SERVICE_POST = TOPIC_BASE + "/service/post"
TOPIC_SERVICE_SUB = TOPIC_BASE + "/service/sub"
TOPIC_SYSTEM_POST = TOPIC_BASE + "/system/post"
TOPIC_SYSTEM_SUB = TOPIC_BASE + "/system/sub"
TOPIC_BROADCAST_SUB = TOPIC_BASE + "/broadcast/sub"

# Config entry keys
CONF_SERIAL = "serial"
# Product/model id (e.g. PLAF203, PLAF109). Forms the second segment of every
# topic: dl/{product_id}/{serial}/device/... Entries created before this key
# existed fall back to DEVICE_PRODUCT_ID.
CONF_PRODUCT_ID = "product_id"
CONF_MQTT_USERNAME = "mqtt_username"
CONF_MQTT_PASSWORD = "mqtt_password"
CONF_FEEDING_PLANS = "feeding_plans"

# Timing
HEARTBEAT_WATCHDOG_SEC = 81
HEARTBEAT_INTERVAL_SEC = 30
NTP_DRIFT_THRESHOLD_SEC = 10
DEVICE_INIT_WATCHDOG_SEC = 10

# Feeding plans
MAX_FEEDING_PLANS = 9

# Commands (cmd field values)
CMD_HEARTBEAT = "HEARTBEAT"
CMD_NTP = "NTP"
CMD_NTP_SYNC = "NTP_SYNC"
CMD_OTA_INFORM = "OTA_INFORM"
CMD_OTA_PROGRESS = "OTA_PROGRESS"
CMD_OTA_UPGRADE = "OTA_UPGRADE"
CMD_GET_CONFIG = "GET_CONFIG"
CMD_SERVER_CONFIG_PUSH = "SERVER_CONFIG_PUSH"
CMD_ATTR_GET_SERVICE = "ATTR_GET_SERVICE"
CMD_ATTR_SET_SERVICE = "ATTR_SET_SERVICE"
CMD_ATTR_PUSH_EVENT = "ATTR_PUSH_EVENT"
CMD_DEVICE_START_EVENT = "DEVICE_START_EVENT"
CMD_ERROR_EVENT = "ERROR_EVENT"
CMD_GRAIN_OUTPUT_EVENT = "GRAIN_OUTPUT_EVENT"
CMD_GET_FEEDING_PLAN_EVENT = "GET_FEEDING_PLAN_EVENT"
CMD_FEEDING_PLAN_SERVICE = "FEEDING_PLAN_SERVICE"
CMD_MANUAL_FEEDING_SERVICE = "MANUAL_FEEDING_SERVICE"
CMD_DEVICE_REBOOT = "DEVICE_REBOOT"
CMD_RESTORE = "RESTORE"
CMD_RESET = "RESET"
CMD_BINDING = "BINDING"
CMD_UNBIND = "UNBIND"
CMD_DEVICE_INFO_SERVICE = "DEVICE_INFO_SERVICE"
CMD_DEVICE_PROPERTIES_SERVICE = "DEVICE_PROPERTIES_SERVICE"
CMD_INITIALIZE_SD_CARD_SERVICE = "INITIALIZE_SD_CARD_SERVICE"
CMD_WIFI_RECONNECT_SERVICE = "WIFI_RECONNECT_SERVICE"
CMD_WIFI_CHANGE_SERVICE = "WIFI_CHANGE_SERVICE"
CMD_TUTK_CONTRACT_SERVICE = "TUTK_CONTRACT_SERVICE"
CMD_DETECTION_EVENT = "DETECTION_EVENT"
CMD_DEVICE_DATA_EVENT = "DEVICE_DATA_EVENT"

# --- Wet food feeders (e.g. PLAF109 Polar) ---
# These have a rotating plate and a motorised door rather than an auger, and
# implement a different command set. MANUAL_FEEDING_SERVICE/grainNum is not
# implemented at all - the firmware drops it silently, exactly as it does an
# entirely made-up command.
CMD_WET_FOOD_FEED_NOW_SERVICE = "WET_FOOD_FEED_NOW_SERVICE"
CMD_WET_GRAIN_FEEDING_PLAN_SERVICE = "WET_GRAIN_FEEDING_PLAN_SERVICE"
CMD_WET_GRAIN_OUTPUT_EVENT = "WET_GRAIN_OUTPUT_EVENT"
CMD_GET_SOME_ATTR_SERVICE = "GET_SOME_ATTR_SERVICE"
CMD_DEVICE_FUNCTION_TEST_SERVICE = "DEVICE_FUNCTION_TEST_SERVICE"
CMD_DEVICE_CONFIG_SYNC = "DEVICE_CONFIG_SYNC"
CMD_DEVICE_LOG_REPORT_EVENT = "DEVICE_LOG_REPORT_EVENT"
# Pet presence, via the feeder's infrared sensor.
CMD_PET_DETECT_EVENT = "PET_DETECT_EVENT"
CMD_MACHINE_INFRARED_EVENT = "MACHINE_INFRARED_EVENT"
PET_DETECT_NEAR = "NEAR"
PET_DETECT_LEAVE = "LEAVE"

# Feeding lifecycle, reported via WET_GRAIN_OUTPUT_EVENT.execStep
EXEC_STEP_GRAIN_THAW = "GRAIN_THAW"
EXEC_STEP_GRAIN_START = "GRAIN_START"
EXEC_STEP_OPEN_DOOR = "OPEN_DOOR"
# Observed 2026-08-05 in vendor traffic, immediately before GRAIN_END and
# carrying finished=false. The door shuts here; GRAIN_END then closes out the
# cycle with finished=true.
EXEC_STEP_CLOSE_DOOR = "CLOSE_DOOR"
EXEC_STEP_GRAIN_END = "GRAIN_END"

# Plate homing state (zeroState). The firmware refuses to actuate unless this
# reads SUCCESS; a jammed or misseated plate reports TIMEOUT, which otherwise
# surfaces to the user only as an unexplained app timeout.
ZERO_STATE_PROCESSING = "PROCESSING"
ZERO_STATE_SUCCESS = "SUCCESS"
ZERO_STATE_TIMEOUT = "TIMEOUT"

# Feed duration. The wire carries MINUTES, not seconds - measured 2026-08-06
# from a scheduled feed whose plan said 120 and whose door stood open from
# 21:00:06Z to 23:00:12Z, i.e. 120 minutes. The UI also works in minutes, so
# nothing converts. Treating the wire as seconds multiplied every duration by
# 60: a 4-minute feed held the door open for 4 hours.
DEFAULT_WET_FEEDING_DURATION = 240  # minutes (4h); vendor plans used 120-210
WET_FEEDING_MIN_MINUTES = 1
WET_FEEDING_MAX_MINUTES = 240  # 4 hours

# --- Capabilities ---
# Entity platforms gate on these rather than on product id, so an untested
# model gets the entities its protocol actually supports instead of being
# assumed to have an auger. Declared by each feeder profile in feeders/.
CAP_DISPENSE_PORTIONS = "dispense_portions"  # auger; feed takes a quantity
# Declaring CAP_DISPENSE_PLATE obliges the profile to expose PLATE_COUNT (its
# number of physical carousel plates) and a `serve_plate` coroutine. Every
# reader of PLATE_COUNT sits inside a `supports(CAP_DISPENSE_PLATE)` branch and
# accesses it directly, so a plate profile that omits it fails loudly rather
# than silently reporting someone's guess - two readers used to default to 0
# and 3 respectively for the same missing attribute.
CAP_DISPENSE_PLATE = "dispense_plate"        # carousel; feed references a plan
CAP_PLATE = "plate"                          # plate position / homing state
CAP_AUDIO_TEST = "audio_test"                # can play call-to-eat audio
CAP_FEEDING_PLANS = "feeding_plans"
CAP_DETECTION = "detection"                  # camera models
CAP_PET_PRESENCE = "pet_presence"            # infrared pet proximity sensor
CAP_SD_CARD = "sd_card"                      # onboard storage; camera models
CAP_TEMPERATURE = "temperature"              # cooled models report it on the
                                             # heartbeat

# Code response values
CODE_OK = 0
CODE_ERROR_DEVICE_NOT_BOUND = 2030
# Returned by WET_FOOD_FEED_NOW_SERVICE when planId does not match a plan
# stored on the device. Confirmed by sending a deliberately unreferenced
# planId: the device refuses cleanly and does not actuate.
CODE_ERROR_PLAN_NOT_FOUND = 2050

# Entity platforms
PLATFORMS = [
    "sensor",
    "binary_sensor",
    "switch",
    "button",
    "number",
    "select",
    "event",
]
