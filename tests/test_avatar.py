from stackchan_control.avatar import AVATAR_FILES, validate_esp_avatar
from stackchan_control.settings import PROJECT_ROOT


def test_elysia_avatar_assets_are_esp_decoder_compatible():
    assets_dir = PROJECT_ROOT / "assets/avatars/elysia/v1"

    for filename in AVATAR_FILES.values():
        validate_esp_avatar((assets_dir / filename).read_bytes())
