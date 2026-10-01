"""Numerical contracts, profile integrity and source-coordinate grain checks.

Run with pytest, or without extra test dependencies:
    python -m unittest discover -s tests -p test_color.py -v
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from film_lab import profiles
from film_lab.color import (PROPHOTO_TO_SRGB, apply_adjustments, apply_grain,
                            baseline_render, normalize_libraw_prophoto,
                            resize_rgb, srgb_decode, srgb_encode)
from film_lab.profiles import (ProfileError, apply_style, get_profile,
                               interpolate_tetrahedral, list_profiles, load_cube)


class TetrahedralTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(126)
        self.rgb = self.rng.random((21, 37, 3), dtype=np.float32)

    def test_identity_and_rgb_axis_order(self):
        axis = np.linspace(0, 1, 5, dtype=np.float32)
        r, g, b = np.meshgrid(axis, axis, axis, indexing="ij")
        identity = np.stack([r, g, b], axis=-1)
        np.testing.assert_allclose(interpolate_tetrahedral(self.rgb, identity), self.rgb, atol=2e-7)
        np.testing.assert_allclose(interpolate_tetrahedral(self.rgb, identity[..., [2, 0, 1]]),
                                   self.rgb[..., [2, 0, 1]], atol=2e-7)

    def test_six_tetrahedra_against_explicit_scalar_reference(self):
        # Non-affine corner values detect wrong diagonals that identity LUTs miss.
        table = self.rng.random((2, 2, 2, 3), dtype=np.float32)
        points = np.array([[.8, .5, .2], [.8, .2, .5], [.5, .2, .8],
                           [.5, .8, .2], [.2, .8, .5], [.2, .5, .8]], dtype=np.float32)
        expected = []
        low, high = table[0, 0, 0], table[1, 1, 1]
        for r, g, b in points:
            if r >= g >= b:
                value = low + r * (table[1, 0, 0] - low) + g * (table[1, 1, 0] - table[1, 0, 0]) + b * (high - table[1, 1, 0])
            elif r >= b >= g:
                value = low + r * (table[1, 0, 0] - low) + b * (table[1, 0, 1] - table[1, 0, 0]) + g * (high - table[1, 0, 1])
            elif b >= r >= g:
                value = low + b * (table[0, 0, 1] - low) + r * (table[1, 0, 1] - table[0, 0, 1]) + g * (high - table[1, 0, 1])
            elif g >= r >= b:
                value = low + g * (table[0, 1, 0] - low) + r * (table[1, 1, 0] - table[0, 1, 0]) + b * (high - table[1, 1, 0])
            elif g >= b >= r:
                value = low + g * (table[0, 1, 0] - low) + b * (table[0, 1, 1] - table[0, 1, 0]) + r * (high - table[0, 1, 1])
            else:
                value = low + b * (table[0, 0, 1] - low) + g * (table[0, 1, 1] - table[0, 0, 1]) + r * (high - table[0, 1, 1])
            expected.append(value)
        np.testing.assert_array_equal(interpolate_tetrahedral(points, table), expected)

    def test_corners_and_tetrahedral_boundaries(self):
        table = self.rng.random((2, 2, 2, 3), dtype=np.float32)
        corners = np.array([[r, g, b] for r in (0, 1) for g in (0, 1) for b in (0, 1)], dtype=np.float32)
        np.testing.assert_allclose(interpolate_tetrahedral(corners, table), table.reshape(-1, 3), atol=1e-7)
        points = np.array([[.4, .4, .2], [.4, .2, .4], [.2, .4, .4], [.4, .4, .4]], dtype=np.float32)
        a = interpolate_tetrahedral(points - 1e-7, table)
        b = interpolate_tetrahedral(points + 1e-7, table)
        self.assertLess(np.max(np.abs(a - b)), 1e-6)


class CubeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "identity.cube"
        self.valid = ('TITLE "Test identity"\nLUT_3D_SIZE 2\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n' +
                      "\n".join(f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)) + "\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_red_fastest_cube_file_and_hash(self):
        self.path.write_text(self.valid, encoding="utf-8")
        cube = load_cube(self.path, expected_grid_size=2)
        pixel = np.array([[.75, .25, .5]], dtype=np.float32)
        np.testing.assert_array_equal(cube.apply(pixel), pixel)
        self.assertEqual(load_cube(self.path, expected_sha256=cube.sha256).sha256, cube.sha256)

    def test_invalid_contracts_are_rejected(self):
        invalid = {
            "duplicate": self.valid.replace("LUT_3D_SIZE 2", "LUT_3D_SIZE 2\nLUT_3D_SIZE 2"),
            "domain": self.valid.replace("DOMAIN_MAX 1 1 1", "DOMAIN_MAX 2 2 2"),
            "nonfinite": self.valid + "nan 0 0\n",
            "extra": self.valid + "0 0 0\n",
            "missing": self.valid.rsplit("1 1 1\n", 1)[0],
            "unknown": self.valid + "LUT_1D_SIZE 2\n",
            "outside": self.valid.replace("1 0 0\n", "1.5 0 0\n"),
            "wrong_header_order": self.valid + "DOMAIN_MIN 0 0 0\n",
        }
        for name, content in invalid.items():
            with self.subTest(name=name):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(ProfileError):
                    load_cube(self.path)

    def test_wrong_hash_or_manifest_grid_is_rejected(self):
        self.path.write_text(self.valid, encoding="utf-8")
        with self.assertRaises(ProfileError):
            load_cube(self.path, expected_sha256="0" * 64)
        with self.assertRaises(ProfileError):
            load_cube(self.path, expected_grid_size=33)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.rgb = np.random.default_rng(31).random((19, 29, 3), dtype=np.float32)

    def test_bundled_profiles_and_strength_endpoints(self):
        profile_list = list_profiles()
        self.assertEqual(len(profile_list), 6)
        outputs = []
        for profile in profile_list:
            with self.subTest(profile=profile["id"]):
                self.assertTrue(profile["available"], profile.get("error"))
                self.assertEqual(profile["validation_status"], "initial_style")
                np.testing.assert_array_equal(apply_style(self.rgb, profile["id"], 0), self.rgb)
                styled = apply_style(self.rgb, profile["id"], 1)
                outputs.append(styled)
                self.assertTrue(np.isfinite(styled).all())
                self.assertGreaterEqual(styled.min(), 0)
                self.assertLessEqual(styled.max(), 1)
                half = apply_style(self.rgb, profile["id"], .5)
                np.testing.assert_allclose(half, (self.rgb + styled) * .5, atol=1e-7)
        for a in range(1, len(outputs)):
            for b in range(a):
                self.assertGreater(float(np.mean(np.abs(outputs[a] - outputs[b]))), .01)

    def test_monochrome_channel_contribution_and_monochrome(self):
        primaries = np.eye(3, dtype=np.float32)
        mono = apply_style(primaries, "monochrome", 1)
        np.testing.assert_array_equal(mono[:, 0], mono[:, 1])
        np.testing.assert_array_equal(mono[:, 1], mono[:, 2])
        # Green > red > blue distinguishes weighted luminance from RGB average.
        self.assertGreater(mono[1, 0], mono[0, 0])
        self.assertGreater(mono[0, 0], mono[2, 0])

    def test_missing_style_is_disabled_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as empty:
            with patch.object(profiles, "PROFILE_ROOT", Path(empty)):
                self.assertTrue(all(not p["available"] and p["error"] for p in list_profiles()))
                with self.assertRaises(ProfileError):
                    apply_style(self.rgb, "documentary", 0)

    def test_modified_asset_is_rejected_even_after_cached_load(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            style_dir = root / "documentary"
            style_dir.mkdir()
            source = profiles.PROFILE_ROOT / "documentary"
            for name in ("manifest.json", "style.cube"):
                (style_dir / name).write_bytes((source / name).read_bytes())
            with patch.object(profiles, "PROFILE_ROOT", root):
                apply_style(self.rgb, "documentary", 1)
                with (style_dir / "style.cube").open("ab") as handle:
                    handle.write(b"# changed\n")
                with self.assertRaises(ProfileError):
                    apply_style(self.rgb, "documentary", 1)

    def test_checked_in_assets_are_reproducible(self):
        script = Path(__file__).resolve().parents[1] / "tools" / "generate_profiles.py"
        spec = importlib.util.spec_from_file_location("generate_profiles_test", script)
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        with tempfile.TemporaryDirectory() as folder:
            generator.ROOT = Path(folder)
            generator.main()
            for profile in list_profiles():
                profile_id = profile["id"]
                for name in ("manifest.json", profile["asset"]):
                    self.assertEqual((profiles.PROFILE_ROOT / profile_id / name).read_bytes(),
                                     (Path(folder) / profile_id / name).read_bytes())


class ColourPipelineTests(unittest.TestCase):
    def setUp(self):
        self.rgb = np.random.default_rng(132).random((45, 61, 3), dtype=np.float32)

    def test_transfer_function_round_trip(self):
        ramp = np.linspace(0, 1, 65536, dtype=np.float32)
        np.testing.assert_allclose(srgb_encode(srgb_decode(ramp)), ramp, atol=2e-7)

    def test_libraw_prophoto_normalization_matches_decoder_matrix(self):
        # Independent fixed constants from official LibRaw 0.21.4 colorconst.cpp.
        # This catches a D50/D65 mismatch even if a neutral-only test would pass.
        decoder_matrix = np.array([[.529317, .330092, .140588],
                                   [.098368, .873465, .028169],
                                   [.016879, .117663, .865457]], dtype=np.float32)
        primaries_and_neutrals = np.concatenate([
            np.eye(3, dtype=np.float32),
            np.repeat(np.linspace(0, 1, 257, dtype=np.float32)[:, None], 3, axis=1),
            self.rgb.reshape(-1, 3),
        ])
        decoder_output = primaries_and_neutrals @ decoder_matrix.T
        canonical = normalize_libraw_prophoto(decoder_output)
        reconstructed = canonical @ PROPHOTO_TO_SRGB.T
        np.testing.assert_allclose(reconstructed, primaries_and_neutrals, atol=1e-6, rtol=0)
        neutral = np.repeat(np.linspace(0, 1, 257, dtype=np.float32)[:, None], 3, axis=1)
        baseline = baseline_render(normalize_libraw_prophoto(neutral @ decoder_matrix.T))
        expected_tone = srgb_encode(1.25 * neutral / (1 + .25 * neutral))
        np.testing.assert_allclose(baseline, expected_tone, atol=1e-6, rtol=0)

    def test_libraw_normalization_preserves_values_before_gamut_mapping(self):
        # Matrix rounding produces small out-of-range values near some axes.
        # Clipping here would violate the canonical working-space contract.
        canonical = normalize_libraw_prophoto(np.eye(3, dtype=np.float32))
        self.assertEqual(canonical.dtype, np.float32)
        self.assertLess(canonical.min(), 0)
        self.assertGreater(canonical.max(), 1)
        rendered = baseline_render(canonical)
        self.assertTrue(np.isfinite(rendered).all())
        self.assertGreaterEqual(rendered.min(), 0)
        self.assertLessEqual(rendered.max(), 1)

    def test_baseline_neutral_ramp_is_finite_monotonic_and_neutral(self):
        ramp = np.repeat(np.linspace(0, 1, 65536, dtype=np.float32)[:, None], 3, axis=1)
        base = baseline_render(ramp)
        self.assertTrue(np.isfinite(base).all())
        self.assertGreaterEqual(np.min(np.diff(base, axis=0)), -1e-7)
        self.assertLess(float(np.max(np.ptp(base, axis=-1))), 1e-4)
        np.testing.assert_allclose(base[0], 0, atol=1e-7)
        np.testing.assert_allclose(base[-1], 1, atol=1e-4)
        self.assertGreater(len(np.unique(base[:, 0])), 60000)

    def test_out_of_gamut_primaries_and_invalid_pixels(self):
        out = baseline_render(np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [4, 2, .1]], dtype=np.float32))
        self.assertTrue(np.isfinite(out).all())
        self.assertGreaterEqual(out.min(), 0)
        self.assertLessEqual(out.max(), 1)
        for invalid in (np.nan, np.inf, -np.inf):
            with self.assertRaises(ValueError):
                baseline_render(np.array([[invalid, 0, 0]]))

    def test_neutral_adjustments_and_extreme_control_values(self):
        np.testing.assert_array_equal(apply_adjustments(self.rgb, {}), self.rgb)
        for value in (-100, 100):
            out = apply_adjustments(self.rgb, dict(contrast=value, saturation=value, highlights=value, shadows=value))
            self.assertTrue(np.isfinite(out).all())
            self.assertGreaterEqual(out.min(), 0)
            self.assertLessEqual(out.max(), 1)
        np.testing.assert_array_equal(apply_adjustments(self.rgb, {"style_id": "monochrome", "saturation": 100}), self.rgb)

    def test_highlights_and_shadows_direction(self):
        ramp = np.repeat(np.linspace(.05, .95, 100, dtype=np.float32)[:, None], 3, axis=1)
        for control in ("highlights", "shadows"):
            brighter = apply_adjustments(ramp, {control: 100})
            darker = apply_adjustments(ramp, {control: -100})
            self.assertTrue(np.all(brighter >= ramp))
            self.assertTrue(np.all(darker <= ramp))

    def test_float_resize_has_no_upscaling_or_eight_bit_quantization(self):
        self.assertIs(resize_rgb(self.rgb, None), self.rgb)
        self.assertIs(resize_rgb(self.rgb, 2048), self.rgb)
        small = resize_rgb(self.rgb, 31)
        self.assertEqual(small.shape, (23, 31, 3))
        self.assertEqual(small.dtype, np.float32)
        self.assertGreater(len(np.unique(small[..., 0])), 256)

    def test_grain_repeatability_and_seed_source_dependence(self):
        settings = {"grain": {"amount": 70, "size": "small", "seed": 123}}
        a = apply_grain(self.rgb, settings, "source-a")
        np.testing.assert_array_equal(a, apply_grain(self.rgb, settings, "source-a"))
        self.assertFalse(np.array_equal(a, apply_grain(self.rgb, settings, "source-b")))
        changed = {"grain": {"amount": 70, "size": "small", "seed": 124}}
        self.assertFalse(np.array_equal(a, apply_grain(self.rgb, changed, "source-a")))
        self.assertFalse(np.array_equal(a, self.rgb))
        np.testing.assert_array_equal(self.rgb, apply_grain(self.rgb, {"grain": {"amount": 0}}, "source-a"))

    def test_grain_matches_vertical_and_horizontal_tiles(self):
        settings = {"grain": {"amount": 70, "size": "large", "seed": 312}, "_grain_scale": 1}
        full = apply_grain(self.rgb, settings, "source", (61, 45))
        tiled = np.empty_like(full)
        for top, bottom in ((0, 17), (17, 45)):
            for left, right in ((0, 23), (23, 61)):
                tiled[top:bottom, left:right] = apply_grain(self.rgb[top:bottom, left:right], settings, "source", (61, 45), (left, top))
        np.testing.assert_array_equal(full, tiled)

    def test_reduced_grain_tiles_use_source_coordinates(self):
        settings = {"grain": {"amount": 60, "size": "small", "seed": 92}, "_grain_scale": (4, 4)}
        full = apply_grain(self.rgb, settings, "source", (244, 180))
        tiled = np.concatenate([
            apply_grain(self.rgb[:20], settings, "source", (244, 180), (0, 0)),
            apply_grain(self.rgb[20:], settings, "source", (244, 180), (0, 80)),
        ])
        np.testing.assert_array_equal(full, tiled)


if __name__ == "__main__":
    unittest.main()
