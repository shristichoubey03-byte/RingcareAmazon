"""
Snapshot generation and placeholder service for RingCare.
Uses Pillow to generate realistic 320x180 JPEG camera frames for demo mode.
Ensures static/snapshots/ contains valid images on startup.
"""

import os
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

from config import SNAPSHOTS_DIR, format_timestamp

def ensure_snapshots_dir():
    """Creates the static/snapshots directory if it does not exist."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

def generate_jpeg_snapshot(
    output_path: Path,
    camera_name: str = "Front Door Video Doorbell Pro 2",
    timestamp_str: str = "",
    event_type: str = "button_press",
    sub_type: str = None
) -> str:
    """Generates a 320x180 JPEG snapshot image with camera HUD, visitor indicator, and timestamps."""
    ensure_snapshots_dir()

    width, height = 320, 180
    # Background color scheme (night-vision dark navy/slate)
    bg_color = (15, 23, 42) # #0f172a
    img = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)

    # Grid / scan lines for camera aesthetic
    for y in range(30, height - 25, 30):
        draw.line([(0, y), (width, y)], fill=(30, 41, 59), width=1)
    draw.line([(width // 2, 25), (width // 2, height - 25)], fill=(30, 41, 59), width=1)

    # Top HUD Bar
    draw.rectangle([(0, 0), (width, 24)], fill=(0, 0, 0))
    # Red REC circle
    draw.ellipse([(10, 8), (18, 16)], fill=(239, 68, 68))
    # Top text
    draw.text((24, 6), "REC • LIVE", fill=(248, 250, 252))

    # Formatted timestamp
    display_time = format_timestamp(timestamp_str) if timestamp_str else datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    # Draw right-aligned timestamp (simple estimation: ~6px per char)
    draw.text((150, 6), display_time, fill=(148, 163, 184))

    # Center Visual Graphic depending on event type
    badge_text = "VISITOR AT DOOR"
    badge_color = (37, 99, 235) # Ring blue

    if event_type == "button_press" or sub_type == "doorbell":
        # Draw doorbell chime symbol
        draw.ellipse([(140, 45), (180, 85)], outline=(96, 165, 250), width=2)
        draw.ellipse([(152, 57), (168, 73)], fill=(59, 130, 246))
        badge_text = "DOORBELL PRESSED"
        badge_color = (37, 99, 235)
    elif sub_type == "human" or event_type == "motion_detected":
        # Draw human head and torso silhouette
        draw.ellipse([(150, 45), (170, 65)], fill=(16, 185, 129))
        draw.polygon([(140, 95), (180, 95), (172, 70), (148, 70)], fill=(16, 185, 129))
        badge_text = "HUMAN MOTION"
        badge_color = (16, 185, 129)
    elif sub_type == "pet":
        # Draw pet silhouette
        draw.ellipse([(150, 60), (170, 80)], fill=(245, 158, 11))
        draw.polygon([(145, 55), (153, 62), (148, 65)], fill=(245, 158, 11))
        draw.polygon([(175, 55), (167, 62), (172, 65)], fill=(245, 158, 11))
        badge_text = "PET MOTION"
        badge_color = (245, 158, 11)
    else:
        # Default motion box
        draw.rectangle([(140, 50), (180, 90)], outline=(139, 92, 246), width=2)
        badge_text = "MOTION EVENT"
        badge_color = (139, 92, 246)

    # Center Badge banner
    draw.rectangle([(80, 108), (240, 128)], fill=badge_color)
    draw.text((95, 112), badge_text, fill=(255, 255, 255))

    # Bottom HUD Bar
    draw.rectangle([(0, height - 25), (width, height)], fill=(0, 0, 0))
    # Camera name on left
    draw.text((8, height - 20), camera_name[:24], fill=(226, 232, 240))
    # Quality telemetry on right
    draw.text((220, height - 20), "1080p HD", fill=(56, 189, 248))

    # Save as JPEG
    img.save(output_path, "JPEG", quality=85)
    return f"/static/snapshots/{output_path.name}"

def init_snapshots():
    """Initializes static/snapshots directory and generates base placeholders if missing."""
    ensure_snapshots_dir()
    placeholders = {
        "placeholder_doorbell.jpg": ("Front Door Video Doorbell Pro 2", "button_press", "doorbell"),
        "placeholder_human.jpg": ("Hallway Stick Up Cam", "motion_detected", "human"),
        "placeholder_pet.jpg": ("Hallway Stick Up Cam", "motion_detected", "pet"),
        "placeholder_motion.jpg": ("Kitchen Motion Sensor", "motion_detected", "all"),
        "default_snapshot.jpg": ("Ring Camera", "motion_detected", None),
    }

    for filename, (cam, ev_type, sub_type) in placeholders.items():
        file_path = SNAPSHOTS_DIR / filename
        if not file_path.exists():
            generate_jpeg_snapshot(
                output_path=file_path,
                camera_name=cam,
                timestamp_str=datetime.now().isoformat(),
                event_type=ev_type,
                sub_type=sub_type
            )

def create_event_snapshot(device_name: str, timestamp_str: str, event_type: str, sub_type: str = None, event_id: str = None) -> str:
    """Generates a dedicated snapshot JPEG for a specific event."""
    ensure_snapshots_dir()
    safe_id = str(event_id or int(datetime.now().timestamp() * 1000)).replace(" ", "_")
    filename = f"snap_{safe_id}.jpg"
    file_path = SNAPSHOTS_DIR / filename

    return generate_jpeg_snapshot(
        output_path=file_path,
        camera_name=device_name,
        timestamp_str=timestamp_str,
        event_type=event_type,
        sub_type=sub_type
    )
