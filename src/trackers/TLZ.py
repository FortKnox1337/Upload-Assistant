# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any, Optional
import re

from src.trackers.COMMON import COMMON
from src.trackers.UNIT3D import UNIT3D

Meta = dict[str, Any]
Config = dict[str, Any]


class TLZ(UNIT3D):
    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name='TLZ')
        self.config: Config = config
        self.common = COMMON(config)
        self.tracker = 'TLZ'
        self.base_url = 'https://tlzdigital.com'
        self.id_url = f'{self.base_url}/api/torrents/'
        self.upload_url = f'{self.base_url}/api/torrents/upload'
        self.search_url = f'{self.base_url}/api/torrents/filter'
        self.torrent_url = f'{self.base_url}/torrents/'
        self.banned_groups = [""]

    async def get_category_id(
        self,
        meta: Meta,
        category: Optional[str] = None,
        reverse: bool = False,
        mapping_only: bool = False
    ) -> dict[str, str]:
        _ = (category, reverse, mapping_only)
        category_value = str(meta.get('category', '')).upper()
        category_id = {
            'MOVIE': '1',
            'TV': '2',
        }.get(category_value, '0')
        return {'category_id': category_id}

    async def get_type_id(
        self,
        meta: Meta,
        type: Optional[str] = None,
        reverse: bool = False,
        mapping_only: bool = False
    ) -> dict[str, str]:
        _ = (type, reverse, mapping_only)

        type_value = str(meta.get('type', '')).upper()
        quality = str(meta.get('quality', '')).upper()
        title = str(meta.get('title', '')).upper()
        category_value = str(meta.get('category', '')).upper()

        # Base mapping
        type_id = {
            'FILM': '1',
            'CAM': '2',
            'HDCAM': '2',
            'TS': '2',
            'TELESYNC': '2',
            'HDTS': '2',
            'TC': '2',
            'TELECINE': '2',
            'PDVD': '2',
            'WORKPRINT': '2',
            'WP': '2',
            'SCR': '2',
            'SCREENER': '2',
            'DVDSCR': '2',
            'EPISODE': '3',
            'PACK': '4',
        }.get(type_value, '0')

        # Check multiple likely metadata fields used in scene-style naming
        release_name = ' '.join([
            str(meta.get('title', '')),
            str(meta.get('name', '')),
            str(meta.get('release_name', '')),
            str(meta.get('filename', '')),
            quality
        ]).upper()

        # Use boundaries and separators to avoid false positives such as TS inside other words
        cam_pattern = re.compile(
            r'(?<![A-Z0-9])('
            r'CAM(?:RIP)?|'
            r'HD[ ._-]?CAM|'
            r'TS|'
            r'TELE[ ._-]?SYNC|'
            r'HDTS|'
            r'TC|'
            r'TELECINE|'
            r'PDVD|'
            r'WORKPRINT|WP|'
            r'SCR|SCREENER|DVDSCR|'
            r'R5|'
            r'LINE(?:[ ._-]?AUDIO)?|'
            r'LD'
            r')(?![A-Z0-9])'
        )

        if cam_pattern.search(release_name):
            type_id = '2'

        # TV packs override everything else
        if meta.get('tv_pack'):
            type_id = '4'
        elif category_value == 'TV' and type_id not in ['2', '4']:
            type_id = '3'
        elif category_value == 'MOVIE' and type_id != '2':
            type_id = '1'

        return {'type_id': type_id}
