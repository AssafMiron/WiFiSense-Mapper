"""Tests for WiFiSense Mapper RF Disturbance and Motion Sensing Engine."""

from __future__ import annotations

from custom_components.wifisense_mapper.engine.rf_sensing import (
    RFPerturbationDetector,
    RollingBaselineTracker,
    StationaryDeviceClassifier,
)


def test_stationary_device_classifier() -> None:
    """Test stationary device classifier distinguishing static IoT from roaming phones."""
    classifier = StationaryDeviceClassifier(observation_window_sec=600.0)

    # 1. Device seen on single AP multiple times (e.g. smart plug)
    plug_mac = "aa:bb:cc:11:22:33"
    ap1_mac = "11:22:33:44:55:66"
    t0 = 1000.0

    classifier.record_client(plug_mac, ap1_mac, now=t0)
    assert not classifier.is_stationary(plug_mac)  # Only 1 sample, need >= 2

    classifier.record_client(plug_mac, ap1_mac, now=t0 + 10)
    assert classifier.is_stationary(plug_mac)

    # 2. Roaming phone moving between AP1 and AP2
    phone_mac = "aa:bb:cc:99:88:77"
    ap2_mac = "22:33:44:55:66:77"

    classifier.record_client(phone_mac, ap1_mac, now=t0)
    classifier.record_client(phone_mac, ap2_mac, now=t0 + 15)
    assert not classifier.is_stationary(phone_mac)

    # 3. Forced exclusions and forced inclusions
    classifier.set_excluded_macs([plug_mac])
    assert not classifier.is_stationary(plug_mac)

    classifier.set_forced_stationary([phone_mac])
    assert classifier.is_stationary(phone_mac)


def test_rolling_baseline_tracker() -> None:
    """Test rolling baseline mean and standard deviation updates."""
    tracker = RollingBaselineTracker(window_size=10, min_std=1.0)
    detector = RFPerturbationDetector()
    detector.feed_backhaul_sample(
        satellite_mac="22:22:22:22:22:22",
        parent_mac="11:11:11:11:11:11",
        rssi=-60,
        now=100.0,
    )
    link = detector.links["backhaul:11:11:11:11:11:11->22:22:22:22:22:22"]

    # Feed multiple quiet samples
    for val in [-60, -61, -59, -60, -60]:
        tracker.update_link_baseline(link, val)

    assert -61.0 <= link.baseline_mean <= -59.0
    assert link.baseline_std >= 1.0

    # Ensure frozen baseline when perturbed
    link.is_perturbed = True
    old_mean = link.baseline_mean
    tracker.update_link_baseline(link, -30)  # Extreme sample while perturbed
    assert link.baseline_mean == old_mean  # Should NOT adapt during perturbation


def test_baseline_not_contaminated_by_perturbation() -> None:
    """Verify sudden RSSI perturbation spikes are excluded from baseline tracker."""
    detector = RFPerturbationDetector(sensitivity="medium")
    sat = "22:22:22:22:22:22"
    parent = "11:11:11:11:11:11"

    # Establish quiet baseline at -60 dBm
    for i in range(10):
        detector.feed_backhaul_sample(sat, parent, -60, now=100.0 + i)

    link = detector.links[f"backhaul:{parent}->{sat}"]
    assert -60.1 <= link.baseline_mean <= -59.9

    # Sharp disturbance spike arrives
    detector.feed_backhaul_sample(sat, parent, -88, now=120.0)

    # Baseline samples must NOT have absorbed the spike -88
    assert -88 not in link.baseline_samples
    assert link.baseline_mean == -60.0


def test_rf_perturbation_detection_and_area_hysteresis() -> None:
    """Test full RF perturbation detection, scoring, and area motion triggering."""
    detector = RFPerturbationDetector(
        sensitivity="medium",
        off_delay_sec=20.0,
        min_consecutive=2,
    )

    sat_mac = "33:33:33:33:33:33"
    parent_mac = "11:11:11:11:11:11"
    area_id = "living_room"
    t0 = 1000.0

    # 1. Establish quiet baseline (10 stable samples around -65 dBm)
    for i in range(10):
        detector.feed_backhaul_sample(
            satellite_mac=sat_mac,
            parent_mac=parent_mac,
            rssi=-65,
            area_id=area_id,
            now=t0 + (i * 5),
        )

    snap_quiet = detector.evaluate_areas(now=t0 + 50)
    assert not snap_quiet.area_motion.get(area_id, False)
    assert snap_quiet.area_scores.get(area_id, 0.0) < 30.0

    # 2. Simulate human walking across line of sight (sudden signal drops and high variance)
    # First hit
    detector.feed_backhaul_sample(
        satellite_mac=sat_mac,
        parent_mac=parent_mac,
        rssi=-78,
        area_id=area_id,
        now=t0 + 55,
    )
    snap1 = detector.evaluate_areas(now=t0 + 55)
    # Need min_consecutive=2 hits before triggering area motion
    assert not snap1.area_motion.get(area_id, False)

    # Second hit with high fluctuation
    detector.feed_backhaul_sample(
        satellite_mac=sat_mac,
        parent_mac=parent_mac,
        rssi=-82,
        area_id=area_id,
        now=t0 + 60,
    )
    snap2 = detector.evaluate_areas(now=t0 + 60)
    assert snap2.area_motion.get(area_id) is True
    assert snap2.area_scores[area_id] > 55.0
    assert snap2.has_active_perturbation is True
    assert snap2.burst_recommended is True

    # 3. Disturbance subsides, verify hold-down timer
    for i in range(6):
        detector.feed_backhaul_sample(
            satellite_mac=sat_mac,
            parent_mac=parent_mac,
            rssi=-65,
            area_id=area_id,
            now=t0 + 62 + (i * 2),
        )
    # At t0 + 75s (only 15s after last trigger at t0+60), off_delay is 20s -> should still be True
    snap_held = detector.evaluate_areas(now=t0 + 75)
    assert snap_held.area_motion.get(area_id) is True

    # At t0 + 85s (>20s after trigger), should clear to False
    snap_cleared = detector.evaluate_areas(now=t0 + 85)
    assert snap_cleared.area_motion.get(area_id) is False


def test_stationary_client_rf_sensing() -> None:
    """Test RF disturbance sensing using stationary Wi-Fi client links."""
    detector = RFPerturbationDetector(sensitivity="high", min_consecutive=1)

    client_mac = "aa:bb:cc:dd:ee:ff"
    ap_mac = "11:22:33:44:55:66"
    area_id = "kitchen"
    t = 2000.0

    # Feed baseline for client
    detector.feed_client_sample(client_mac, ap_mac, -55, client_name="Smart Plug", area_id=area_id, now=t)
    detector.feed_client_sample(client_mac, ap_mac, -55, client_name="Smart Plug", area_id=area_id, now=t + 5)
    detector.feed_client_sample(client_mac, ap_mac, -55, client_name="Smart Plug", area_id=area_id, now=t + 10)

    snap_init = detector.evaluate_areas(now=t + 10)
    assert not snap_init.area_motion.get(area_id, False)

    # Induce sharp perturbation
    detector.feed_client_sample(client_mac, ap_mac, -72, client_name="Smart Plug", area_id=area_id, now=t + 15)
    detector.feed_client_sample(client_mac, ap_mac, -78, client_name="Smart Plug", area_id=area_id, now=t + 18)

    snap_disturbed = detector.evaluate_areas(now=t + 18)
    assert snap_disturbed.area_motion.get(area_id) is True
    assert snap_disturbed.area_scores[area_id] >= 40.0
