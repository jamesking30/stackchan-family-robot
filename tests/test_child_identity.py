import math

import numpy as np

from stackchan_control.child_identity import ChildVoiceClassifier


def sine_pcm(frequency: float, seconds: float = 0.9) -> bytes:
    sample_rate = 16000
    samples = np.arange(round(sample_rate * seconds))
    signal = np.sin(2 * math.pi * frequency * samples / sample_rate) * 5000
    return signal.astype("<i2").tobytes()


def test_high_pitch_voiced_wake_audio_is_child_evidence():
    evidence = ChildVoiceClassifier().classify(sine_pcm(320))

    assert evidence.is_child is True
    assert evidence.median_pitch_hz is not None
    assert evidence.median_pitch_hz >= 300


def test_low_pitch_voiced_wake_audio_is_not_child_evidence():
    evidence = ChildVoiceClassifier().classify(sine_pcm(150))

    assert evidence.is_child is False
    assert evidence.median_pitch_hz is not None
    assert evidence.median_pitch_hz < 200


def test_silence_is_unknown_not_child():
    evidence = ChildVoiceClassifier().classify(b"\x00\x00" * 16000)

    assert evidence.is_child is False
    assert evidence.median_pitch_hz is None
