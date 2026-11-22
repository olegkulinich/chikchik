"""Renderer selection, also used before Qt loads in the frozen runtime hook."""
import os
import sys


USE_D3D11 = (sys.platform == "win32" and not os.environ.get("QTC_SAFE_GL")
             and os.environ.get("QTC_VIDEO_BACKEND", "").lower() != "opengl")
