import sys
import types
import unittest


cli_ui = types.ModuleType('cli_ui')
cli_ui.ask_yes_no = lambda *_args, **_kwargs: False
sys.modules.setdefault('cli_ui', cli_ui)

console_module = types.ModuleType('src.console')
console_module.console = types.SimpleNamespace(print=lambda *_args, **_kwargs: None)
sys.modules.setdefault('src.console', console_module)

languages_module = types.ModuleType('src.languages')
languages_module.languages_manager = types.SimpleNamespace()
sys.modules.setdefault('src.languages', languages_module)

unit3d_module = types.ModuleType('src.trackers.UNIT3D')
unit3d_module.UNIT3D = type('UNIT3D', (), {})
sys.modules.setdefault('src.trackers.UNIT3D', unit3d_module)

from src.trackers.NQ import NQ


class NQNameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tracker = NQ.__new__(NQ)

    async def test_preserves_release_suffix_in_extensionless_uuid(self) -> None:
        meta = {
            'uuid': 'Snatched.2017.UHD.BluRay.2160p.DTS-HD.MA.7.1.HEVC.REMUX-FraMeSToR',
            'filelist': [],
        }

        result = await self.tracker.get_name(meta)

        self.assertEqual(
            result['name'],
            'Snatched.2017.UHD.BluRay.2160p.DTS-HD.MA.7.1.HEVC.REMUX-FraMeSToR',
        )

    async def test_prefers_single_media_filename_over_folder_uuid(self) -> None:
        meta = {
            'uuid': 'Snatched.2017.UHD.BluRay.2160p.DTS-HD.MA.7.1.HEVC',
            'filelist': [
                'D:/Movies/Snatched/Snatched.2017.UHD.BluRay.2160p.DTS-HD.MA.7.1.HEVC.REMUX-FraMeSToR.mkv'
            ],
        }

        result = await self.tracker.get_name(meta)

        self.assertEqual(
            result['name'],
            'Snatched.2017.UHD.BluRay.2160p.DTS-HD.MA.7.1.HEVC.REMUX-FraMeSToR',
        )

    async def test_strips_only_a_known_media_extension(self) -> None:
        meta = {
            'uuid': 'unused-folder-name',
            'filelist': ['D:/Movies/Movie.2025.1080p.BluRay.REMUX-GROUP.MKV'],
        }

        result = await self.tracker.get_name(meta)

        self.assertEqual(result['name'], 'Movie.2025.1080p.BluRay.REMUX-GROUP')

    async def test_falls_back_to_generated_name(self) -> None:
        meta = {
            'name': 'Movie 2025 1080p BluRay REMUX DTS-HD MA 7.1-GROUP',
            'filelist': [],
        }

        result = await self.tracker.get_name(meta)

        self.assertEqual(result['name'], 'Movie.2025.1080p.BluRay.REMUX.DTS-HD.MA.7.1-GROUP')


if __name__ == '__main__':
    unittest.main()
