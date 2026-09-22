from hand_controller.config import AppConfig, CameraConfig, TrackingConfig, GestureConfig

def test_app_config_defaults():
    cfg = AppConfig()
    assert cfg.preview_enabled is True
    assert cfg.num_hands == 2
    assert cfg.debug is True
    assert cfg.audio_feedback is False
    assert cfg.camera.width == 1280
    assert cfg.camera.height == 720
    assert cfg.camera.fps == 30

def test_app_config_preview_disabled():
    cfg = AppConfig(preview_enabled=False)
    assert cfg.preview_enabled is False

def test_app_config_custom_camera():
    cam = CameraConfig(index=1, width=640, height=480, fps=60)
    cfg = AppConfig(camera=cam)
    assert cfg.camera.index == 1
    assert cfg.camera.width == 640
    assert cfg.camera.height == 480
    assert cfg.camera.fps == 60
