# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0

import os
import re
import unicodedata
from typing import Any

import cli_ui

from src.console import console
from src.languages import languages_manager
from src.trackers.UNIT3D import UNIT3D


class NQ(UNIT3D):
    NORDIC_LANGUAGE_TOKENS = frozenset({
        'da', 'dan', 'danish',
        'fi', 'fin', 'finnish',
        'ice', 'icelandic', 'is', 'isl',
        'no', 'nno', 'nob', 'nor', 'norwegian',
        'sv', 'swe', 'swedish',
    })

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, tracker_name='NQ')
        self.config = config
        self.tracker = 'NQ'
        self.base_url = 'https://nordicq.org'
        self.id_url = f'{self.base_url}/api/torrents/'
        self.upload_url = f'{self.base_url}/api/torrents/upload'
        self.search_url = f'{self.base_url}/api/torrents/filter'
        self.torrent_url = f'{self.base_url}/torrents/'
        self.banned_groups: list[str] = []

    @staticmethod
    def _language_values(languages: Any) -> list[str]:
        if isinstance(languages, str):
            return [languages]
        if isinstance(languages, list):
            return [language for language in languages if isinstance(language, str)]
        return []

    @classmethod
    def _language_tokens(cls, languages: Any) -> set[str]:
        tokens: set[str] = set()
        for language in cls._language_values(languages):
            normalized = unicodedata.normalize('NFKD', language)
            normalized = ''.join(char for char in normalized if not unicodedata.combining(char))
            tokens.update(re.findall(r'[a-z0-9]+', normalized.casefold()))
        return tokens

    async def get_additional_checks(self, meta: dict[str, Any]) -> bool:
        if str(meta.get('category', '')).upper() not in {'MOVIE', 'TV'}:
            return True

        if not meta.get('language_checked', False):
            await languages_manager.process_desc_language(meta, tracker=self.tracker)

        console.print(f'[yellow]{self.tracker}: Checking file for approved Nordic subtitles...[/yellow]')
        subtitle_languages = meta.get('subtitle_languages', [])
        subtitle_tokens = self._language_tokens(subtitle_languages)

        if self.NORDIC_LANGUAGE_TOKENS.intersection(subtitle_tokens):
            nordic_subtitles = [
                subtitle for subtitle in self._language_values(subtitle_languages)
                if self.NORDIC_LANGUAGE_TOKENS.intersection(self._language_tokens(subtitle))
            ]
            console.print(f'[green]{self.tracker}: Nordic subtitle requirement met: {", ".join(nordic_subtitles)}[/green]')
            return meta.get('unattended', False) or cli_ui.ask_yes_no('Do you wish to continue uploading?', default=False)

        subtitle_display = ', '.join(subtitle_languages) if isinstance(subtitle_languages, list) else str(subtitle_languages or 'None')
        console.print(
            f'[bold red]{self.tracker} requires at least one Nordic subtitle for Movie and TV uploads.\n'
            f'Found Subtitles: {subtitle_display}[/bold red]'
        )
        return False

    async def get_name(self, meta: dict[str, Any]) -> dict[str, str]:
        name = os.path.splitext(str(meta.get('uuid', '')))[0]
        name = name.replace(' ', '.')

        name = name.translate(str.maketrans({
            'Æ': 'AE', 'æ': 'ae', 'Ð': 'D', 'ð': 'd',
            'Ø': 'O', 'ø': 'o', 'Þ': 'TH', 'þ': 'th',
            'Å': 'A', 'å': 'a', 'Œ': 'OE', 'œ': 'oe',
            'ß': 'ss',
        }))

        name = (
            name.replace('HDR10+', 'HDR10P')
                .replace('DD+', 'DDP')
                .replace('DTS:X', 'DTS-X')
                .replace('&', 'and')
        )

        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'[^A-Za-z0-9._()\-]+', '.', name)
        name = re.sub(r'\.{2,}', '.', name).strip('.')

        console.print(f'[cyan]Name: {name}')
        return {'name': name}
