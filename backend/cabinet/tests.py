from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from .models import Device, Telemetry, Alert


class CabinetAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.device = Device.objects.create(
            device_id="cabinet-test-01",
            name="Tủ Test 01",
            location="Phòng Lab",
            connection_state="online",
            health_state="normal",
            mode="AUTO",
            fan_output=False,
            temp_threshold_on=30.0,
            temp_threshold_off=28.0,
            temp_alert_thresh=35.0,
            humidity_alert_thresh=80.0,
        )
        self.telemetry = Telemetry.objects.create(
            device=self.device,
            temperature_c=31.5,
            humidity_pct=65.0,
            fan_output=True,
            mode="AUTO",
        )
        self.alert = Alert.objects.create(
            device=self.device,
            alert_type="HIGH_TEMPERATURE",
            severity="high",
            message="Nhiệt độ cao test",
            status="open",
        )

    def test_list_devices_api(self):
        response = self.client.get("/api/devices/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["device_id"], "cabinet-test-01")

    def test_list_telemetry_api(self):
        response = self.client.get("/api/telemetry/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["temperature_c"], 31.5)

    def test_list_alerts_api(self):
        response = self.client.get("/api/alerts/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["status"], "open")

    def test_control_fan_api(self):
        # Bật quạt thủ công
        response = self.client.post(
            f"/api/devices/{self.device.device_id}/control/",
            {"action": "SET_FAN", "on": True},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.device.refresh_from_db()
        self.assertTrue(self.device.fan_output)

    def test_resolve_alert_api(self):
        # Giải quyết cảnh báo
        response = self.client.post(f"/api/alerts/{self.alert.id}/resolve/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, "resolved")
        self.assertIsNotNone(self.alert.resolved_at)
