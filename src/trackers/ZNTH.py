# Upload Assistant (local custom tracker)
from typing import Any

from src.trackers.COMMON import COMMON
from src.trackers.UNIT3D import UNIT3D


class ZNTH(UNIT3D):
    def __init__(self, config: dict[str, Any]):
        super().__init__(config, tracker_name='ZNTH')
        self.config = config
        self.common = COMMON(config)
        self.tracker = 'ZNTH'
        self.base_url = 'https://znth.cx'
        self.id_url = f'{self.base_url}/api/torrents/'
        self.upload_url = f'{self.base_url}/api/torrents/upload'
        self.requests_url = f'{self.base_url}/api/requests/filter'
        self.search_url = f'{self.base_url}/api/torrents/filter'
        self.torrent_url = f'{self.base_url}/torrents/'
        self.banned_groups = [
            'd3g', 'FGT', 'ION10', 'MeGusta', 'RARBG', 'YIFY', 'YTS', 'AROMA', 'DNL', 'Hi10', 'LAMA',
            'nikt0', 'x0r', '4K4U', 'Alcaide_Kira', 'aXXo', 'BRrip', 'CM8', 'CrEwSaDe', 'EVO',
            'FaNGDiNG0', 'FRDS', 'HD2DVD', 'HDTime', 'iPlanet', 'KiNGDOM', 'mHD', 'mSD', 'NhaNc3',
            'nHD', 'nSD', 'OFT', 'PRODJi', 'SANTi', 'SPDVD', 'STUTTERSHIT', 'Telly', 'TGx', 'TSP',
            'TSPxL', 'WAF', 'GalaxyTV',
        ]

    async def get_name(self, meta: dict[str, Any]) -> dict[str, str]:
        znth_name = meta['name']
        if meta['category'] == 'TV' and meta.get('episode_title', "") != "":
            znth_name = znth_name.replace(f"{meta['episode_title']} {meta['resolution']}", f"{meta['resolution']}", 1)
        return {'name': znth_name}

    async def get_type_id(
        self, meta: dict[str, Any], type: str = "", reverse: bool = False, mapping_only: bool = False
    ) -> dict[str, str]:
        type_id = {
            'DISC': '1',
            'REMUX': '2',
            'ENCODE': '3',
            'DVDRIP': '11',
            'WEBDL': '4',
            'WEBRIP': '5',
            'HDTV': '6',
        }
        if mapping_only:
            return type_id
        elif reverse:
            return {v: k for k, v in type_id.items()}
        elif type:
            return {'type_id': type_id.get(type, '0')}
        else:
            meta_type = meta.get('type', '')
            resolved_id = type_id.get(meta_type, '0')
            return {'type_id': resolved_id}

    async def get_additional_data(self, meta: dict[str, Any]) -> dict[str, str]:
        return {
            'mod_queue_opt_in': await self.get_flag(meta, 'modq'),
        }
      
