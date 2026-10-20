"""Safe regression tests for the web draft and authentication flow.

These tests use the committed pilot fixture and mocked database/provider calls.
They never connect to or write to the configured PostgreSQL database.
"""

import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from flask_restx import Api

from src.controllers.Itinerary_controller import itinerary_ns
from src.controllers.auth_controller import auth_ns
from src.services.ai.orchestrator import orchestrate_draft_itinerary


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "osm_vietnam_pilot_sample.json"
CURRENT_DATE = date.today().isoformat()


class MultiAgentWebFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pois = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        app = Flask(__name__)
        api = Api(app, doc=False)
        api.add_namespace(itinerary_ns, path="/api/itinerary")
        api.add_namespace(auth_ns, path="/api/auth")
        cls.client = app.test_client()

    def test_offline_planner_accepts_flat_pilot_coordinates(self):
        result = orchestrate_draft_itinerary(
            {"selected_date": CURRENT_DATE, "query": "Ha Noi", "max_stops": 3},
            candidate_pois=self.pois,
            execute_api=False,
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(len(result["draft"]["items"]), 3)
        self.assertTrue(all(item["coordinates"]["latitude"] is not None for item in result["draft"]["items"]))
        self.assertTrue(all(item["coordinates"]["longitude"] is not None for item in result["draft"]["items"]))

    def test_plan_draft_endpoint_returns_valid_unsaved_draft(self):
        with patch(
            "src.services.ai.orchestrator.search_staging_pois",
            return_value={"success": True, "data": self.pois},
        ):
            response = self.client.post(
                "/api/itinerary/multi-agent/plan-draft",
                json={"selected_date": CURRENT_DATE, "query": "Ha Noi", "max_stops": 3},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertFalse(payload["is_saved"])
        self.assertTrue(payload["validation"]["valid"])

    def test_plan_draft_rejects_invalid_date(self):
        response = self.client.post(
            "/api/itinerary/multi-agent/plan-draft",
            json={"selected_date": "not-a-date", "query": "Ha Noi"},
        )
        self.assertEqual(response.status_code, 400)

    def test_plan_draft_rejects_past_date(self):
        response = self.client.post(
            "/api/itinerary/multi-agent/plan-draft",
            json={"selected_date": "2000-01-01", "query": "Ha Noi"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["status"], "invalid_request")

    def test_plan_draft_rejects_incomplete_coordinates(self):
        response = self.client.post(
            "/api/itinerary/multi-agent/plan-draft",
            json={"selected_date": CURRENT_DATE, "area": "Ha Noi", "latitude": 21.03},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["status"], "invalid_request")

    def test_plan_draft_rejects_non_numeric_coordinates(self):
        response = self.client.post(
            "/api/itinerary/multi-agent/plan-draft",
            json={
                "selected_date": CURRENT_DATE,
                "area": "Ha Noi",
                "latitude": "north",
                "longitude": 105.85,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["status"], "invalid_request")

    def test_plan_draft_reports_database_unavailable(self):
        with patch(
            "src.services.ai.orchestrator.search_staging_pois",
            return_value={
                "success": False,
                "error_code": "database_error",
                "message": "Database unavailable",
            },
        ):
            response = self.client.post(
                "/api/itinerary/multi-agent/plan-draft",
                json={"selected_date": CURRENT_DATE, "query": "Ha Noi"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "database_error")

    def test_confirm_requires_bearer_token(self):
        response = self.client.post("/api/itinerary/multi-agent/confirm", json={})
        self.assertEqual(response.status_code, 401)

    def test_login_endpoint_uses_auth_service_response(self):
        user = {"id": 7, "email": "test@example.invalid", "full_name": "Test", "is_verified": True}
        with patch(
            "src.controllers.auth_controller.login_user",
            return_value=(True, {"token": "mock.jwt.token", "user": user}),
        ):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "test@example.invalid", "password": "not-a-real-password"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["token"], "mock.jwt.token")
        self.assertEqual(response.get_json()["user"]["id"], 7)


if __name__ == "__main__":
    unittest.main()
