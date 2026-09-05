from __future__ import annotations

import unittest

from ai_interview.stt import mlx_repo_for, resolve_whisper_runtime


class WhisperRuntimeTests(unittest.TestCase):
    def test_auto_prefers_cuda(self) -> None:
        device, compute = resolve_whisper_runtime(
            "auto", "", cuda_devices=1, mlx_available=True
        )
        self.assertEqual(device, "cuda")
        self.assertEqual(compute, "float16")

    def test_auto_uses_mlx_on_apple_when_installed(self) -> None:
        device, compute = resolve_whisper_runtime(
            "auto", "", cuda_devices=0, mlx_available=True
        )
        self.assertEqual(device, "mlx")
        self.assertEqual(compute, "float16")

    def test_auto_falls_back_to_cpu_int8(self) -> None:
        device, compute = resolve_whisper_runtime(
            "auto", "", cuda_devices=0, mlx_available=False
        )
        self.assertEqual(device, "cpu")
        self.assertEqual(compute, "int8")

    def test_explicit_cpu_ignores_cuda(self) -> None:
        device, compute = resolve_whisper_runtime(
            "cpu", "int8_float16", cuda_devices=4, mlx_available=True
        )
        self.assertEqual(device, "cpu")
        self.assertEqual(compute, "int8_float16")

    def test_metal_alias_and_repo_map(self) -> None:
        device, _compute = resolve_whisper_runtime(
            "metal", "", cuda_devices=0, mlx_available=True
        )
        self.assertEqual(device, "mlx")
        self.assertEqual(mlx_repo_for("base"), "mlx-community/whisper-base-mlx")
        self.assertEqual(mlx_repo_for("org/custom-model"), "org/custom-model")
