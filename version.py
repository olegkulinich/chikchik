VERSION = "0.9.0"
APP_NAME = "ChikChik!"   # shown to people; files/folders use "ChikChik" (no "!": special in cmd/bat)

try:
    from build_info import BUILD   # branch · commit (· modified), written by build.bat
except ImportError:                 # running from source
    BUILD = ""
