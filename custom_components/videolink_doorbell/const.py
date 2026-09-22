"""Constants for the Videolink Doorbell integration."""

from homeassistant.const import Platform

DOMAIN = "videolink_doorbell"

CONF_CHANNEL = "channel"
CONF_RTSP_PORT = "rtsp_port"
CONF_STREAM = "stream"
CONF_VIDEO_SOURCE = "video_source"
CONF_VERIFY_SSL = "verify_ssl"

DEFAULT_CHANNEL = 0
DEFAULT_RTSP_PORT = 554
DEFAULT_STREAM = "main"
DEFAULT_VIDEO_SOURCE = "flv"
DEFAULT_VERIFY_SSL = False

STREAM_MAIN = "main"
STREAM_SUB = "sub"
STREAMS = (STREAM_MAIN, STREAM_SUB)

VIDEO_SOURCE_FLV = "flv"
VIDEO_SOURCE_RTSP = "rtsp"
VIDEO_SOURCES = (VIDEO_SOURCE_FLV, VIDEO_SOURCE_RTSP)

PLATFORMS = [Platform.CAMERA]
